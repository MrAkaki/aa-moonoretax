"""Celery tasks: hourly ESI collection + first-setup backfill.

Tasks are thin: they pull from :mod:`moontax.providers`, persist via the models/managers,
and hand off to the tax/reconcile engines. The trap-prone bits live in the managers
(ledger overwrite) and parsers (FILETIME), not here.
"""

from __future__ import annotations

import datetime as dt
import logging

from celery import shared_task
from django.core.cache import cache
from django.db import IntegrityError, transaction

from moontax import providers
from moontax.core import matching
from moontax.core.notifications_parse import (
    FRACTURE_TYPES,
    LASER_FIRED,
    STARTED,
    parse_extraction_started,
    parse_fracture,
)
from moontax.core.timeutils import eve_now
from moontax.models import (
    Configuration,
    EveName,
    Extraction,
    MiningLedger,
    Moon,
    OreType,
    ProcessedNotification,
    Structure,
    TokenConfig,
    UnmatchedMiner,
)
from moontax.ores import MOON_ORE_GROUP_IDS

logger = logging.getLogger(__name__)

_LOCK_TTL = 3600
_RETRY = dict(autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)

MOON_DRILLING_SERVICE = "Moon Drilling"


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def _g(obj, key, default=None):
    """Read ``key`` from an ESI result whether it's a dict or an attribute object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _lock(key: str):
    """Best-effort non-overlapping lock; returns True if acquired."""
    return cache.add(f"moontax:lock:{key}", "1", _LOCK_TTL)


def _unlock(key: str):
    cache.delete(f"moontax:lock:{key}")


def _as_date(value):
    """Coerce an ESI ``last_updated`` value to a ``date`` (UTC ledger day)."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.timezone.utc).date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _corp_context():
    """Return ``(token, corporation_id)`` or ``(None, None)``, flagging a broken token."""
    cfg = Configuration.get_solo()
    token = providers.get_corp_token()
    if token is None:
        _mark_token_broken("No valid corp token with the required scopes.")
        return None, None
    corp_id = cfg.target_corporation_id
    if not corp_id:
        tc = TokenConfig.get_solo()
        corp_id = tc.corporation_id if tc else None
    return token, corp_id


def _mark_token_broken(reason: str):
    tc = TokenConfig.get_solo()
    if tc and tc.is_valid:
        tc.is_valid = False
        tc.last_error = reason
        tc.save(update_fields=["is_valid", "last_error", "updated_at"])
    logger.warning("moontax: corp token broken: %s", reason)
    try:
        from moontax import notifications

        notifications.notify_token_broken(reason)
    except Exception:  # noqa: BLE001 - notifications must never break collection
        logger.exception("moontax: failed to notify admins of broken token")


def _resolve_names(ids, category=""):
    """Resolve unknown ids via ESI and cache them in ``EveName``. Returns ``{id: name}``."""
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    known = EveName.objects.name_map(ids)
    missing = [i for i in ids if i not in known]
    if missing:
        try:
            for eid, row in providers.resolve_names(missing).items():
                name = row.get("name", "")
                EveName.objects.set_name(eid, name, category or row.get("category", ""))
                known[eid] = name
        except Exception:  # noqa: BLE001 - names are cosmetic; never fail a sync
            logger.exception("moontax: name resolution failed")
    return known


# --------------------------------------------------------------------------------------
# Moon-ore catalog (per-ore tax dropdown)
# --------------------------------------------------------------------------------------


def load_ore_catalog() -> int:
    """Mirror the moon-ore catalog from **public** ESI; return the catalog size.

    Moon ores are exactly the members of the five moon-asteroid groups.  The groups
    also contain quality variants ("Brimful Bitumens", "Glistening Bitumens") and
    compressed variants ("Compressed Bitumens").  Base ores have single-word names;
    variants carry one or more prefix words.

    Strategy:
    - Base ores (no space in name) are stored with ``base_type_id=None``.
    - Quality/compressed variants (space in name) are stored with ``base_type_id``
      pointing at their base ore, derived from the last whitespace token of their name
      (e.g. "Brimful Bitumens" → "Bitumens").  Variants whose base name is not found
      in the same catalog are dropped (they belong to a non-moon group and are not
      relevant to moon-ore tax).
    - The Admin dropdown is restricted to base ores; the tax engine uses
      ``OreType.objects.effective_type_id()`` to resolve a variant to its base before
      rate lookup.

    Uses no token, so it runs at install time (``moontax_load_ores``) before a corp
    token exists, and weekly thereafter via :func:`update_ore_catalog`.
    """
    entries: dict[int, dict] = {}
    for group_id in MOON_ORE_GROUP_IDS:
        group = providers.universe_group(group_id)
        for type_id in _g(group, "types", []) or []:
            entries[int(type_id)] = {"group_id": group_id}

    # Resolve all names in one ESI call.
    names = providers.resolve_names(entries.keys())

    # Assign names; drop entries with no name returned.
    for type_id in list(entries):
        name = names.get(type_id, {}).get("name", "")
        if not name:
            del entries[type_id]
            continue
        entries[type_id]["name"] = name

    # Index base ores by name (single-word names are base ores).
    base_by_name: dict[str, int] = {
        fields["name"]: tid
        for tid, fields in entries.items()
        if " " not in fields["name"]
    }

    # Classify every entry: base ore or variant.
    for type_id in list(entries):
        name = entries[type_id]["name"]
        if " " not in name:
            # Base ore — no parent.
            entries[type_id]["base_type_id"] = None
        else:
            # Variant — the last token is the base ore name.
            base_name = name.rsplit(" ", 1)[-1]
            base_tid = base_by_name.get(base_name)
            if base_tid is None:
                # Unknown base (non-moon ore or unresolved) — exclude from catalog.
                del entries[type_id]
            else:
                entries[type_id]["base_type_id"] = base_tid

    count = OreType.objects.replace_catalog(entries)
    logger.info("moontax: moon-ore catalog loaded (%d ores, variants included)", count)
    return count


@shared_task(**_RETRY)
def update_ore_catalog():
    """Weekly refresh of the moon-ore catalog (public ESI; no token needed)."""
    if not _lock("update_ore_catalog"):
        return
    try:
        load_ore_catalog()
    finally:
        _unlock("update_ore_catalog")


# --------------------------------------------------------------------------------------
# Collection tasks
# --------------------------------------------------------------------------------------


@shared_task(**_RETRY)
def update_structures():
    """Refresh corp structures: fuel, Moon Drilling service state, names."""
    token, corp_id = _corp_context()
    if not corp_id:
        return
    if not _lock("update_structures"):
        return
    try:
        rows = providers.corp_structures(token, corp_id)
        type_ids, system_ids = set(), set()
        for row in rows:
            sid = _g(row, "structure_id")
            if sid is None:
                continue
            services = _g(row, "services") or []
            drill_state = ""
            has_drill = False
            for svc in services:
                if _g(svc, "name") == MOON_DRILLING_SERVICE:
                    has_drill = True
                    drill_state = _g(svc, "state", "") or ""
                    break
            type_id = _g(row, "type_id")
            system_id = _g(row, "system_id")
            type_ids.add(type_id)
            system_ids.add(system_id)
            Structure.objects.update_or_create(
                structure_id=sid,
                defaults={
                    "name": _g(row, "name", "") or "",
                    "corporation_id": corp_id,
                    "type_id": type_id,
                    "system_id": system_id,
                    "fuel_expires": _g(row, "fuel_expires"),
                    "has_moon_drilling": has_drill,
                    "drill_state": drill_state,
                },
            )
        # Cosmetic name backfill for types/systems.
        type_names = _resolve_names(type_ids, EveName.STRUCTURE_TYPE)
        system_names = _resolve_names(system_ids, EveName.SYSTEM)
        for s in Structure.objects.filter(corporation_id=corp_id):
            changed = []
            if not s.type_name and s.type_id in type_names:
                s.type_name = type_names[s.type_id]
                changed.append("type_name")
            if not s.system_name and s.system_id in system_names:
                s.system_name = system_names[s.system_id]
                changed.append("system_name")
            if changed:
                s.save(update_fields=changed + ["updated_at"])
    finally:
        _unlock("update_structures")


@shared_task(**_RETRY)
def update_extractions():
    """Refresh the scheduled-extraction list (chunk arrival / natural decay times)."""
    token, corp_id = _corp_context()
    if not corp_id:
        return
    if not _lock("update_extractions"):
        return
    try:
        rows = providers.mining_extractions(token, corp_id)
        moon_ids = set()
        for row in rows:
            structure = Structure.objects.filter(
                structure_id=_g(row, "structure_id")
            ).first()
            if structure is None:
                continue
            moon_id = _g(row, "moon_id")
            moon = None
            if moon_id:
                moon, _ = Moon.objects.get_or_create(moon_id=moon_id)
                moon_ids.add(moon_id)
                if structure.moon_id != moon_id:
                    structure.moon = moon
                    structure.save(update_fields=["moon", "updated_at"])
            Extraction.objects.update_or_create(
                structure=structure,
                chunk_arrival_time=_g(row, "chunk_arrival_time"),
                defaults={
                    "moon": moon,
                    "auto_fracture_time": _g(row, "natural_decay_time"),
                },
            )
        # Resolve moon names via GET /universe/moons/{moon_id}/ (the bulk
        # POST /universe/names/ endpoint does NOT support moon IDs and 404s).
        # Collect system_ids so we can batch-resolve system names afterwards.
        new_system_ids = set()
        moon_system_map: dict[int, int] = {}  # moon_id → system_id
        for moon_id in moon_ids:
            moon_obj = Moon.objects.filter(moon_id=moon_id).first()
            if moon_obj and moon_obj.name:
                # Already named — skip ESI call; still note system_id for
                # system-name backfill if system_name is missing.
                if moon_obj.system_id and not moon_obj.system_name:
                    moon_system_map[moon_id] = moon_obj.system_id
                    new_system_ids.add(moon_obj.system_id)
                continue
            try:
                data = providers.universe_moon(moon_id)
                moon_name = _g(data, "name", "") or ""
                system_id = _g(data, "system_id")
                if moon_obj and moon_name:
                    moon_obj.name = moon_name
                    update_fields = ["name"]
                    if system_id and not moon_obj.system_id:
                        moon_obj.system_id = system_id
                        update_fields.append("system_id")
                    moon_obj.save(update_fields=update_fields)
                    EveName.objects.set_name(moon_id, moon_name, EveName.MOON)
                if system_id:
                    moon_system_map[moon_id] = system_id
                    new_system_ids.add(system_id)
            except Exception:  # noqa: BLE001 - names are cosmetic; never fail a sync
                logger.exception("moontax: failed to resolve moon %s via ESI", moon_id)
        # Resolve system names (system IDs work fine with POST /universe/names/).
        if new_system_ids:
            system_names = _resolve_names(new_system_ids, EveName.SYSTEM)
            for moon_id, system_id in moon_system_map.items():
                moon_obj = Moon.objects.filter(moon_id=moon_id).first()
                if moon_obj and not moon_obj.system_name and system_id in system_names:
                    moon_obj.system_name = system_names[system_id]
                    moon_obj.save(update_fields=["system_name"])
    finally:
        _unlock("update_extractions")


@shared_task(**_RETRY)
def update_ledger():
    """Pull every observer ledger and upsert rows (cumulative overwrite; never sum)."""
    token, corp_id = _corp_context()
    if not corp_id:
        return
    if not _lock("update_ledger"):
        return
    try:
        observers = providers.mining_observers(token, corp_id)
        unmatched_char_ids = set()
        for obs in observers:
            observer_id = _g(obs, "observer_id")
            if observer_id is None:
                continue
            structure = Structure.objects.filter(structure_id=observer_id).first()
            rows = providers.mining_observer_ledger(token, corp_id, observer_id)
            for row in rows:
                character_id = _g(row, "character_id")
                recorded_date = _as_date(_g(row, "last_updated"))
                if character_id is None or recorded_date is None:
                    continue
                kwargs = dict(
                    observer_id=observer_id,
                    character_id=character_id,
                    ore_type_id=_g(row, "type_id"),
                    recorded_date=recorded_date,
                    quantity=int(_g(row, "quantity", 0) or 0),
                    structure=structure,
                )
                if matching.user_for_character(character_id) is not None:
                    MiningLedger.objects.upsert_row(
                        recorded_corporation_id=_g(row, "recorded_corporation_id"),
                        **kwargs,
                    )
                else:
                    UnmatchedMiner.objects.upsert_row(**kwargs)
                    unmatched_char_ids.add(character_id)
        # Backfill unmatched character names for the Staff table.
        if unmatched_char_ids:
            names = _resolve_names(unmatched_char_ids, EveName.CHARACTER)
            for cid, name in names.items():
                UnmatchedMiner.objects.filter(
                    character_id=cid, character_name=""
                ).update(character_name=name)
    finally:
        _unlock("update_ledger")


@shared_task(**_RETRY)
def poll_notifications():
    """Read corp moon notifications; apply extraction-started + pop events once each."""
    token, corp_id = _corp_context()
    if not corp_id:
        return
    if not _lock("poll_notifications"):
        return
    try:
        notes = providers.character_notifications(token)
        # Oldest-first so a chunk's "started" is applied before its "pop".
        for note in sorted(notes, key=lambda n: _g(n, "timestamp")):
            ntype = _g(note, "type")
            if ntype == STARTED:
                if _apply_started(note):
                    _claim_notification(note)
            elif ntype in FRACTURE_TYPES:
                if _apply_fracture(note, ntype):
                    _claim_notification(note)
    finally:
        _unlock("poll_notifications")
    # A fresh pop may now be finalizable.
    finalize_pops.delay()


def _claim_notification(note) -> bool:
    """Record a notification as processed; True only the first time it's seen."""
    nid = _g(note, "notification_id")
    if nid is None:
        return True
    try:
        with transaction.atomic():
            ProcessedNotification.objects.create(
                notification_id=nid,
                notification_type=_g(note, "type", "") or "",
                timestamp=_g(note, "timestamp"),
            )
    except IntegrityError:
        return False
    return True


def _apply_started(note) -> bool:
    """Apply a MoonminingExtractionStarted notification.

    Returns True if the notification was handled (or can be permanently skipped),
    False if it should be retried later because required data does not exist yet.
    """
    parsed = parse_extraction_started(_g(note, "text", "") or "")
    if parsed["chunk_arrival_time"] is None:
        # Malformed notification — no valid chunk time; skip permanently.
        logger.warning(
            "moontax: notification %s has no chunk_arrival_time; skipping permanently",
            _g(note, "notification_id"),
        )
        return True
    structure = Structure.objects.filter(structure_id=parsed["structure_id"]).first()
    if structure is None:
        # Structure not yet collected — leave unclaimed so a later poll can apply it.
        logger.warning(
            "moontax: notification %s references unknown structure %s; will retry next poll",
            _g(note, "notification_id"),
            parsed["structure_id"],
        )
        return False
    moon = None
    if parsed["moon_id"]:
        moon, _ = Moon.objects.get_or_create(moon_id=parsed["moon_id"])
        if structure.moon_id != parsed["moon_id"]:
            structure.moon = moon
            structure.save(update_fields=["moon", "updated_at"])
    extraction, _ = Extraction.objects.update_or_create(
        structure=structure,
        chunk_arrival_time=parsed["chunk_arrival_time"],
        defaults={
            "moon": moon,
            "auto_fracture_time": parsed["auto_fracture_time"],
            "ore_volume_by_type": {str(k): v for k, v in parsed["ore_volume_by_type"].items()},
            "started_notification_id": _g(note, "notification_id"),
        },
    )
    try:
        from moontax import notifications

        notifications.notify_moon_pop(extraction)
    except Exception:  # noqa: BLE001 - notifications must never break collection
        logger.exception("moontax: failed to send moon-pop notification")
    return True


def _apply_fracture(note, ntype) -> bool:
    """Apply a fracture (laser-fired / auto-fracture) notification.

    Returns True if the notification was handled (or can be permanently skipped),
    False if it should be retried later because no matching extraction exists yet.
    """
    parsed = parse_fracture(_g(note, "text", "") or "")
    structure_id = parsed["structure_id"]
    pop_time = _g(note, "timestamp")
    # Pop the latest already-arrived, not-yet-fractured chunk for the structure.
    candidates = Extraction.objects.filter(
        structure__structure_id=structure_id, fracture_time__isnull=True
    )
    extraction = (
        candidates.filter(chunk_arrival_time__lte=pop_time)
        .order_by("-chunk_arrival_time")
        .first()
        or candidates.order_by("chunk_arrival_time").first()
    )
    if extraction is None:
        # No matching extraction exists yet — leave unclaimed so a later poll can apply it.
        logger.warning(
            "moontax: fracture notification %s (type=%s) found no matching extraction "
            "for structure %s; will retry next poll",
            _g(note, "notification_id"),
            ntype,
            structure_id,
        )
        return False
    extraction.fracture_time = pop_time
    extraction.fracture_type = Extraction.LASER if ntype == LASER_FIRED else Extraction.AUTO
    extraction.fracture_notification_id = _g(note, "notification_id")
    extraction.save(
        update_fields=[
            "fracture_time",
            "fracture_type",
            "fracture_notification_id",
            "updated_at",
        ]
    )
    return True


@shared_task(**_RETRY)
def update_contracts():
    """Land corp item-exchange contracts, then run reconciliation against invoices."""
    token, corp_id = _corp_context()
    if not corp_id:
        return
    if not _lock("update_contracts"):
        return
    try:
        from moontax.core import reconcile

        contracts = providers.corp_contracts(token, corp_id)
        reconcile.ingest_and_reconcile(token, corp_id, contracts)
    finally:
        _unlock("update_contracts")


@shared_task(**_RETRY)
def finalize_pops():
    """Finalize pops whose despawn window has elapsed (emits invoices)."""
    from moontax.core import tax

    tax.finalize_ready_pops()


@shared_task(**_RETRY)
def send_reminders():
    """Send due unpaid-invoice reminders (cadence in notifications)."""
    from moontax import notifications

    notifications.send_due_reminders()


# --------------------------------------------------------------------------------------
# Umbrella + backfill
# --------------------------------------------------------------------------------------


@shared_task(**_RETRY)
def run_hourly():
    """One entry point for the hourly beat: collect, finalize, reconcile, remind."""
    update_structures()
    update_extractions()
    update_ledger()
    poll_notifications()
    update_contracts()
    finalize_pops()
    send_reminders()


@shared_task(**_RETRY)
def backfill():
    """First-setup backfill: full collection pass over all available ESI data."""
    logger.info("moontax: starting first-setup backfill")
    update_structures()
    update_extractions()
    update_ledger()
    poll_notifications()
    update_contracts()
    finalize_pops()
    logger.info("moontax: backfill complete")
