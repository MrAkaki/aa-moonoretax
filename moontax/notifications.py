"""Notification dispatch (Requirements §7).

Primary channel is a Discord DM via ``allianceauth-discordbot``; if the player has no
linked Discord or the DM fails, fall back to Alliance Auth's built-in ``notify()``.
Entry points (called from tax/reconcile/tasks): :func:`notify_invoice`,
:func:`notify_mismatch`, :func:`notify_token_broken`, :func:`send_due_reminders`.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from moontax.models import Configuration, EveName, Invoice, NotificationSetting

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------
# Delivery primitives
# --------------------------------------------------------------------------------------


def _discord_dm(user, message: str) -> bool:
    """DM ``user`` via aadiscordbot. False if no linked Discord or the send fails."""
    try:
        from allianceauth.services.modules.discord.models import DiscordUser

        discord_uid = DiscordUser.objects.get(user=user).uid
    except Exception:  # noqa: BLE001 - no linked Discord ⇒ fall back
        return False
    try:
        from aadiscordbot.tasks import send_message

        send_message(user_id=discord_uid, message=message)
        return True
    except Exception:  # noqa: BLE001 - bot down / API drift ⇒ fall back
        logger.info("moontax: discord DM to %s failed; falling back to notify()", user)
        return False


def _aa_notify(user, title: str, message: str, level: str = "info") -> None:
    try:
        from allianceauth.notifications import notify

        notify(user, title=title, message=message, level=level)
    except Exception:  # noqa: BLE001 - never let a notification break a task
        logger.exception("moontax: AA notify() failed for %s", user)


def deliver(user, title: str, message: str, level: str = "info") -> None:
    """Discord DM first, AA ``notify()`` fallback."""
    if _discord_dm(user, f"**{title}**\n{message}"):
        return
    _aa_notify(user, title, message, level)


# --------------------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------------------


def _ore_lines(mapping) -> str:
    """Render ``{ore_type_id: units}`` as readable lines.

    Names resolve through the OreType catalog (then EveName, then the raw id) so contract
    items and ad-hoc maps show ore names rather than type ids.
    """
    if not mapping:
        return "  (none)"
    from moontax.core import tax

    names = tax.resolve_ore_names(mapping.keys())
    return "\n".join(
        f"  • {names.get(tid, tid)}: {units:,}" for tid, units in mapping.items()
    )


def _invoice_ore_map(invoice: Invoice) -> dict[int, int]:
    return {item.ore_type_id: item.units_owed for item in invoice.items.all()}


def _ore_owed_lines(invoice: Invoice) -> str:
    """Render owed ore, showing the compressed alternative ("or N Compressed …")."""
    items = list(invoice.items.all())
    if not items:
        return "  (none)"
    from moontax.core import tax

    # Backfill any blank/numeric names from the catalog and persist them, so this DM
    # (and every later render) shows ore names instead of raw type ids.
    tax.heal_invoice_item_names(items)
    lines = []
    for item in items:
        name = item.ore_type_name or EveName.objects.get_name(item.ore_type_id)
        line = f"  • {name}: {item.units_owed:,}"
        alt = item.compressed_alternative
        if alt:
            line += f"  (or {alt['units']:,} {alt['name']})"
        lines.append(line)
    return "\n".join(lines)


def _dashboard_url() -> str:
    from django.conf import settings
    from django.urls import reverse

    try:
        rel = reverse("moontax:index")
    except Exception:  # noqa: BLE001 - URLs may be unmounted in some test contexts
        return ""
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    return f"{base}{rel}" if base else rel


# --------------------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------------------


def _basic_users():
    """Active users holding ``moontax.basic_access`` (direct, group, or superuser)."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.db.models import Q

    User = get_user_model()
    perm = Permission.objects.filter(
        content_type__app_label="moontax", codename="basic_access"
    ).first()
    if perm is None:
        return User.objects.filter(is_superuser=True, is_active=True)
    return (
        User.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(user_permissions=perm) | Q(groups__permissions=perm))
        .distinct()
    )


def _wants(user, flag: str) -> bool:
    """Return True if the user wants this notification.

    Missing NotificationSetting row ⇒ all flags are considered enabled (opt-in by default).
    """
    try:
        setting = NotificationSetting.objects.get(user=user)
        return bool(getattr(setting, flag))
    except NotificationSetting.DoesNotExist:
        return True


def _payment_corp_name() -> str:
    """Human-readable name of the payment corporation, or an empty string."""
    try:
        cfg = Configuration.get_solo()
        return cfg.payment_corporation_name or ""
    except Exception:  # noqa: BLE001 - never crash a notification
        return ""


def notify_invoice(invoice: Invoice) -> None:
    """On invoice calculation: DM the player the taxed-ore details + the code."""
    if not _wants(invoice.user, "invoice_emitted"):
        return
    url = _dashboard_url()
    corp_name = _payment_corp_name()
    corp_label = f" **{corp_name}**" if corp_name else ""
    tail = (
        f"\n\nPay in ore via an item-exchange contract to the payment corp{corp_label}, "
        f"with the code `{invoice.code}` in the contract title."
    )
    if url:
        tail += f"\nDetails: {url}"
    message = (
        f"You have a moon-ore tax invoice (**{invoice.code}**).\n"
        f"Ore owed (pay raw, or the compressed equivalent, or a mix):\n"
        f"{_ore_owed_lines(invoice)}{tail}"
    )
    deliver(invoice.user, "Moon ore tax invoice", message)


def notify_mismatch(invoice: Invoice, expected, submitted, pc=None) -> None:
    """On a quantity/type mismatch while the contract is still outstanding."""
    message = (
        f"Your payment contract for invoice **{invoice.code}** doesn't match what's owed. "
        f"Please fix it. You can pay in the mined ore, the compressed equivalent, or a mix.\n\n"
        f"Expected:\n{_ore_owed_lines(invoice)}\n\n"
        f"Submitted:\n{_ore_lines(submitted)}"
    )
    deliver(invoice.user, "Contract mismatch", message, level="warning")


def notify_token_broken(reason: str, role: str | None = None) -> None:
    """Token broken/invalid: message everyone with plugin-admin privilege.

    ``role`` identifies which corp's token is broken (``"mining"`` or ``"payment"``).
    When ``None`` the generic "corp token" label is used (legacy call-sites).
    """
    role_label = f"{role} corp" if role else "corp"
    message = (
        f"The Moon Ore Tax {role_label} ESI token is broken or invalid and data "
        f"collection has stopped.\n\nReason: {reason}\n\nA Director or CEO must "
        "re-add the token from the Admin tab."
    )
    title = f"Moon Ore Tax: {role_label} token broken"
    for user in _admins():
        deliver(user, title, message, level="danger")


def notify_moon_pop(extraction) -> None:
    """On a new extraction scheduled: notify all basic-access users who opted in."""
    try:
        chunk = extraction.chunk_arrival_time.strftime("%Y-%m-%d %H:%M")
    except Exception:
        chunk = str(getattr(extraction, "chunk_arrival_time", "unknown"))
    message = (
        f"A new moon extraction has been scheduled at {extraction.structure} "
        f"({extraction.moon}). Chunk ready: {chunk} EVE."
    )
    for user in _basic_users():
        if _wants(user, "moon_pop"):
            deliver(user, "Moon extraction scheduled", message)


def notify_moon_dead(extraction) -> None:
    """On a pop finalized / ore field despawned: notify all basic-access users who opted in."""
    message = (
        f"The moon ore field at {extraction.structure} ({extraction.moon}) "
        f"has been finalized (ore field despawned)."
    )
    for user in _basic_users():
        if _wants(user, "moon_dead"):
            deliver(user, "Moon pop finalized", message)


def _admins():
    """Active users holding ``moontax.admin_access`` (direct, group, or superuser)."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.db.models import Q

    User = get_user_model()
    perm = Permission.objects.filter(
        content_type__app_label="moontax", codename="admin_access"
    ).first()
    if perm is None:
        return User.objects.filter(is_superuser=True, is_active=True)
    return (
        User.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(user_permissions=perm) | Q(groups__permissions=perm))
        .distinct()
    )


# --------------------------------------------------------------------------------------
# Reminders (cadence anchored on the invoice emit timestamp, §7)
# --------------------------------------------------------------------------------------


def _reminder_due(invoice: Invoice, now, every_days: int, daily_after_days: int) -> bool:
    """True if an unpaid invoice is due for a reminder.

    Day 0 is the initial invoice DM (not a reminder). Then a reminder every
    ``every_days`` until the invoice is older than ``daily_after_days``, after which
    reminders go daily. Anchored on ``emitted_at``; paced by ``last_reminder_at``.
    """
    age_days = (now - invoice.emitted_at).days
    cadence = 1 if age_days >= daily_after_days else every_days
    reference = invoice.last_reminder_at or invoice.emitted_at
    return (now - reference) >= timezone.timedelta(days=cadence)


def send_due_reminders() -> int:
    """Send reminders for unpaid (emitted) invoices per the §7 cadence. Returns count."""
    config = Configuration.get_solo()
    now = timezone.now()
    sent = 0
    qs = Invoice.objects.filter(status=Invoice.EMITTED).select_related("user")
    for invoice in qs:
        if not _reminder_due(
            invoice, now, config.reminder_every_days, config.reminder_daily_after_days
        ):
            continue
        age = (now - invoice.emitted_at).days
        corp_name = _payment_corp_name()
        corp_label = f" **{corp_name}**" if corp_name else ""
        message = (
            f"Reminder: moon-ore tax invoice **{invoice.code}** is still unpaid "
            f"({age} day(s) old).\n\nOre owed:\n{_ore_lines(_invoice_ore_map(invoice))}\n\n"
            f"Pay in ore via an item-exchange contract to the payment corp{corp_label} "
            f"with `{invoice.code}` in the title."
        )
        deliver(invoice.user, "Unpaid moon-ore tax invoice", message, level="warning")
        invoice.last_reminder_at = now
        invoice.save(update_fields=["last_reminder_at", "updated_at"])
        sent += 1
    return sent
