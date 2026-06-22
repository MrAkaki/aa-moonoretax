"""Tax engine: per-pop ledger attribution → per-player ore invoices (Requirements §5).

Flow per finalizable pop:
1. **Attribution window** — ledger rows for the pop's structure whose ``recorded_date``
   is ``>= chunk_arrival_date`` and ``< next pop's chunk_arrival_date``.
2. **Per (player, ore type)** sum units (a player = all their linked characters).
3. **Tax** ``owed = floor(units × rate)``; ore types flooring to 0 are dropped.
4. **Invoice** one per (player, pop); a player whose total owed is 0 gets none.

Idempotent per (player, pop): finalize never duplicates and never recomputes a **paid**
invoice; re-running updates **unpaid** invoices only.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict

from django.db import transaction

from moontax.core import matching
from moontax.core.timeutils import eve_now
from moontax.models import (
    Configuration,
    EveName,
    Extraction,
    Invoice,
    InvoiceItem,
    MiningLedger,
    MoonPopSummary,
    OreTaxRate,
    OreType,
    UnmatchedMiner,
)

logger = logging.getLogger(__name__)


def finalize_ready_pops() -> int:
    """Finalize every fractured pop past its despawn window. Returns count processed."""
    config = Configuration.get_solo()
    despawn = config.despawn_hours
    count = 0
    qs = Extraction.objects.filter(fracture_time__isnull=False, finalized=False)
    for extraction in qs.select_related("structure", "moon"):
        if extraction.is_ready_to_finalize(despawn):
            finalize_pop(extraction, config=config)
            count += 1
    return count


def _attribution_window(extraction: Extraction):
    """``(start_date, end_date_exclusive)`` for the pop. ``end`` is ``None`` if open."""
    start = extraction.chunk_arrival_time.date()
    nxt = (
        Extraction.objects.filter(
            structure=extraction.structure,
            chunk_arrival_time__gt=extraction.chunk_arrival_time,
        )
        .order_by("chunk_arrival_time")
        .first()
    )
    end = nxt.chunk_arrival_time.date() if nxt else None
    return start, end


def units_by_character(extraction: Extraction) -> dict[tuple[int, int], int]:
    """``{(character_id, ore_type_id): units}`` summed over the pop's window."""
    start, end = _attribution_window(extraction)
    rows = MiningLedger.objects.filter(
        observer_id=extraction.structure_id, recorded_date__gte=start
    )
    if end is not None:
        rows = rows.filter(recorded_date__lt=end)
    totals: dict[tuple[int, int], int] = defaultdict(int)
    for r in rows.values("character_id", "ore_type_id", "quantity"):
        totals[(r["character_id"], r["ore_type_id"])] += r["quantity"]
    return dict(totals)


def _rate_for(ore_type_id: int, config: Configuration):
    # Quality/compressed variants inherit their base ore's explicit rate.
    effective_id = OreType.objects.effective_type_id(ore_type_id)
    explicit = OreTaxRate.objects.rate_for(effective_id)
    return explicit if explicit is not None else config.default_tax_rate


def compute_owed(extraction: Extraction, config: Configuration):
    """Per player, the owed ore after tax+floor+zero-drop.

    Returns ``{user_id: {ore_type_id: owed_units}}`` (only positive entries), plus a
    ``{user_id: User}`` map.
    """
    char_totals = units_by_character(extraction)

    # Roll character units up to the owning player, summing per ore type.
    player_units: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    users: dict[int, object] = {}
    for (character_id, ore_type_id), units in char_totals.items():
        user = matching.user_for_character(character_id)
        if user is None:
            continue  # unlinked ore is lost by design (§5); recorded elsewhere
        users[user.pk] = user
        player_units[user.pk][ore_type_id] += units

    owed: dict[int, dict[int, int]] = {}
    for user_id, ores in player_units.items():
        per_ore: dict[int, int] = {}
        for ore_type_id, units in ores.items():
            rate = _rate_for(ore_type_id, config)
            owed_units = math.floor(units * rate)
            if owed_units > 0:
                per_ore[ore_type_id] = owed_units
        if per_ore:  # a player whose every ore floored to 0 gets no invoice
            owed[user_id] = per_ore
    return owed, users


@transaction.atomic
def finalize_pop(extraction: Extraction, config: Configuration | None = None) -> None:
    """Emit/update invoices for one pop. Idempotent per (player, pop)."""
    config = config or Configuration.get_solo()
    was_finalized = extraction.finalized
    owed, users = compute_owed(extraction, config)

    existing = {
        inv.user_id: inv
        for inv in Invoice.objects.filter(extraction=extraction).select_related("user")
    }

    for user_id, per_ore in owed.items():
        invoice = existing.get(user_id)
        if invoice and invoice.is_paid:
            continue  # immutable when paid; finalize never recomputes it
        if invoice is None:
            invoice = Invoice.objects.create(
                code=Invoice.objects.generate_code(),
                user=users[user_id],
                extraction=extraction,
                structure=extraction.structure,
                moon=extraction.moon,
                status=Invoice.EMITTED,
                emitted_at=eve_now(),
            )
            _sync_items(invoice, per_ore)
            _notify_new_invoice(invoice)
        else:
            _sync_items(invoice, per_ore)

    # An existing unpaid invoice that now computes to zero owed is removed.
    for user_id, invoice in existing.items():
        if user_id not in owed and not invoice.is_paid:
            invoice.delete()

    if not extraction.finalized:
        extraction.finalized = True
        extraction.finalized_at = eve_now()
        extraction.save(update_fields=["finalized", "finalized_at", "updated_at"])

    _write_pop_summary(extraction)
    if not was_finalized:
        _notify_moon_dead(extraction)


def _notify_moon_dead(extraction: Extraction) -> None:
    try:
        from moontax import notifications

        notifications.notify_moon_dead(extraction)
    except Exception:  # noqa: BLE001 - notification must never block finalize
        logger.exception("moontax: failed to send moon-dead notification for %s", extraction)


def _write_pop_summary(extraction: Extraction) -> None:
    """Write (or refresh) the MoonPopSummary snapshot for a finalized pop.

    Called at the end of finalize_pop, inside the same @transaction.atomic block.
    ore_mined_units sums quantity over the attribution window from both MiningLedger
    and UnmatchedMiner. expected_total_taxes sums units_owed across all InvoiceItems
    for this extraction. invoices_emitted is the current invoice count.
    """
    from django.db.models import Sum

    start, end = _attribution_window(extraction)

    def _window(qs):
        qs = qs.filter(observer_id=extraction.structure_id, recorded_date__gte=start)
        if end is not None:
            qs = qs.filter(recorded_date__lt=end)
        return qs

    ledger_units = (
        _window(MiningLedger.objects).aggregate(total=Sum("quantity"))["total"] or 0
    )
    unmatched_units = (
        _window(UnmatchedMiner.objects).aggregate(total=Sum("quantity"))["total"] or 0
    )
    ore_mined_units = ledger_units + unmatched_units

    expected_total_taxes = (
        InvoiceItem.objects.filter(invoice__extraction=extraction).aggregate(
            total=Sum("units_owed")
        )["total"]
        or 0
    )

    invoices_emitted = Invoice.objects.filter(extraction=extraction).count()

    MoonPopSummary.objects.update_or_create(
        extraction=extraction,
        defaults={
            "structure": extraction.structure,
            "moon": extraction.moon,
            "ore_mined_units": ore_mined_units,
            "expected_total_taxes": expected_total_taxes,
            "invoices_emitted": invoices_emitted,
            "finalized_at": extraction.finalized_at,
        },
    )


def _sync_items(invoice: Invoice, per_ore: dict[int, int]) -> None:
    """Make the invoice's items exactly ``per_ore`` (ore_type_id → units)."""
    invoice.items.exclude(ore_type_id__in=per_ore.keys()).delete()
    for ore_type_id, units in per_ore.items():
        name = OreTaxRate.objects.filter(ore_type_id=ore_type_id).values_list(
            "ore_type_name", flat=True
        ).first() or EveName.objects.get_name(ore_type_id)
        InvoiceItem.objects.update_or_create(
            invoice=invoice,
            ore_type_id=ore_type_id,
            defaults={"units_owed": units, "ore_type_name": name},
        )


def _notify_new_invoice(invoice: Invoice) -> None:
    try:
        from moontax import notifications

        notifications.notify_invoice(invoice)
    except Exception:  # noqa: BLE001 - notification must never block finalize
        logger.exception("moontax: failed to notify invoice %s", invoice.code)


def pop_ore_breakdown(extraction: Extraction) -> list[dict]:
    """Per-ore breakdown for a single pop (matched + unmatched), sorted by units desc.

    Returns ``[{"type_id": int, "name": str, "units": int}, ...]``.

    Uses the same observer_id / date-window filters as ``_write_pop_summary`` so the
    unit totals reconcile with ``MoonPopSummary.ore_mined_units``.  Ore names are
    resolved in bulk: OreType catalog first, then EveName, then str(type_id) as a
    last resort.
    """
    from django.db.models import Sum

    start, end = _attribution_window(extraction)

    def _window(qs):
        qs = qs.filter(observer_id=extraction.structure_id, recorded_date__gte=start)
        if end is not None:
            qs = qs.filter(recorded_date__lt=end)
        return qs

    # Aggregate per ore_type_id from matched ledger rows.
    ledger_rows = (
        _window(MiningLedger.objects)
        .values("ore_type_id")
        .annotate(units=Sum("quantity"))
    )
    # Aggregate per ore_type_id from unmatched miner rows.
    unmatched_rows = (
        _window(UnmatchedMiner.objects)
        .values("ore_type_id")
        .annotate(units=Sum("quantity"))
    )

    # Merge both sources into a single dict keyed by ore_type_id.
    totals: dict[int, int] = defaultdict(int)
    for row in ledger_rows:
        if row["units"]:
            totals[row["ore_type_id"]] += row["units"]
    for row in unmatched_rows:
        if row["units"]:
            totals[row["ore_type_id"]] += row["units"]

    if not totals:
        return []

    all_ore_ids = set(totals)

    # Bulk name resolution: OreType catalog wins, EveName is fallback, str(id) is last resort.
    ore_type_name_map: dict[int, str] = {
        r["type_id"]: r["name"]
        for r in OreType.objects.filter(type_id__in=all_ore_ids).values("type_id", "name")
        if r["name"]
    }
    eve_name_fallback = EveName.objects.name_map(all_ore_ids - set(ore_type_name_map))
    ore_name_map = {**eve_name_fallback, **ore_type_name_map}  # OreType wins

    result = [
        {
            "type_id": tid,
            "name": ore_name_map.get(tid, str(tid)),
            "units": units,
        }
        for tid, units in totals.items()
        if units > 0
    ]
    result.sort(key=lambda x: x["units"], reverse=True)
    return result
