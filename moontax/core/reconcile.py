"""Contract reconciliation (Requirements §6).

A contract matches an invoice only when **all** hold:
- the invoice **code** appears in the contract ``title``,
- ``issuer_id`` is one of the invoice player's characters,
- ``assignee_id == payment corp`` and ``type == item_exchange``.

Status mapping → invoice:
- ``outstanding`` / ``in_progress`` → ``payment_sent`` (+ mismatch check while pending),
- ``finished*`` → ``payment_accepted`` (final — paid even if ore differs),
- ``cancelled`` / ``rejected`` / ``failed`` / ``deleted`` / ``reversed`` → invoice
  reverts to ``emitted`` with a **new code**.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from django.db import transaction

from moontax import providers
from moontax.core import compression, matching
from moontax.core.timeutils import eve_now
from moontax.models import Invoice, OreType, PaymentContract

logger = logging.getLogger(__name__)

ITEM_EXCHANGE = "item_exchange"

PENDING_STATUSES = {"outstanding", "in_progress"}
FINISHED_STATUSES = {"finished", "finished_issuer", "finished_contractor"}
FAILED_STATUSES = {"cancelled", "rejected", "failed", "deleted", "reversed"}

# Invoice codes look like ``MT-AB12CD`` (see InvoiceManager.generate_code).
_CODE_RE = re.compile(rf"{Invoice.objects.CODE_PREFIX}-[0-9A-Fa-f]{{6}}", re.IGNORECASE)


def _g(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def ingest_and_reconcile(token, corp_id: int, contracts) -> None:
    """Persist relevant payment-corp contracts and apply §6 matching/status transitions.

    ``corp_id`` is the **payment** corporation id; the ``assignee_id == corp_id``
    filter ensures we only process contracts addressed to the payment corp.
    """
    for raw in contracts:
        if _g(raw, "type") != ITEM_EXCHANGE:
            continue
        if _g(raw, "assignee_id") != corp_id:  # assignee_id == payment corp
            continue
        _reconcile_one(token, corp_id, raw)


@transaction.atomic
def _reconcile_one(token, corp_id: int, raw) -> None:
    contract_id = _g(raw, "contract_id")
    status = (_g(raw, "status", "") or "").lower()
    title = _g(raw, "title", "") or ""
    issuer_id = _g(raw, "issuer_id")

    pc, _ = PaymentContract.objects.update_or_create(
        contract_id=contract_id,
        defaults={
            "contract_type": ITEM_EXCHANGE,
            "status": status,
            "issuer_id": issuer_id,
            "assignee_id": _g(raw, "assignee_id"),
            "title": title,
            "price": _g(raw, "price", 0) or 0,
            "reward": _g(raw, "reward", 0) or 0,
            "volume": _g(raw, "volume"),
            "location_id": _g(raw, "start_location_id") or _g(raw, "location_id"),
            "date_issued": _g(raw, "date_issued"),
            "date_completed": _g(raw, "date_completed"),
        },
    )

    invoice = pc.invoice or _match_invoice(title, issuer_id)
    if invoice is None:
        return  # no match → ignored (§6)
    if pc.invoice_id != invoice.pk:
        pc.invoice = invoice
        pc.save(update_fields=["invoice", "updated_at"])

    if status in FINISHED_STATUSES:
        _apply_finished(invoice)
    elif status in PENDING_STATUSES:
        _apply_pending(token, corp_id, pc, invoice)
    elif status in FAILED_STATUSES:
        _apply_failed(pc, invoice)


def _covers_invoice(invoice: Invoice, offered: dict) -> bool:
    """Every owed line covered by the offered raw and/or compressed ore (mix-and-match).

    Each line may be paid in the mined ore, its compressed equivalent (1 raw : 1
    compressed), or any mix where raw + compressed >= owed — see
    :mod:`moontax.core.compression`.
    """
    for item in invoice.items.all():
        comp_id = OreType.objects.compressed_type_id(item.ore_type_id)
        offered_raw = int(offered.get(item.ore_type_id, 0) or 0)
        offered_comp = int(offered.get(comp_id, 0) or 0) if comp_id else 0
        if not compression.line_satisfied(item.units_owed, offered_raw, offered_comp):
            return False
    return True


def _match_invoice(title: str, issuer_id: int):
    """Find the invoice whose code is in ``title`` and whose player owns ``issuer_id``."""
    codes = {m.upper() for m in _CODE_RE.findall(title or "")}
    if not codes:
        return None
    for invoice in Invoice.objects.filter(code__in=codes).select_related("user"):
        if issuer_id in matching.character_ids_for_user(invoice.user):
            return invoice
    return None


def _apply_finished(invoice: Invoice) -> None:
    """In-game acceptance is final — paid even if ore quantities differ."""
    if invoice.is_paid:
        return
    invoice.status = Invoice.PAYMENT_ACCEPTED
    invoice.paid_at = eve_now()
    invoice.save(update_fields=["status", "paid_at", "updated_at"])


def _apply_pending(token, corp_id: int, pc: PaymentContract, invoice: Invoice) -> None:
    """Contract submitted, awaiting acceptance. Verify items; notify on mismatch."""
    if not invoice.is_paid and invoice.status != Invoice.PAYMENT_SENT:
        invoice.status = Invoice.PAYMENT_SENT
        invoice.save(update_fields=["status", "updated_at"])

    offered, has_requested = _fetch_items(token, corp_id, pc)
    isk_clean = (pc.price or 0) == 0 and (pc.reward or 0) == 0
    matches = _covers_invoice(invoice, offered) and not has_requested and isk_clean

    if matches:
        if pc.last_mismatch_notified_at is not None:
            pc.last_mismatch_notified_at = None
            pc.save(update_fields=["last_mismatch_notified_at", "updated_at"])
        return

    # Mismatch while still outstanding → ask the player to fix it (once).
    if pc.last_mismatch_notified_at is None:
        expected = {item.ore_type_id: item.units_owed for item in invoice.items.all()}
        _notify_mismatch(invoice, expected, offered, pc)
        pc.last_mismatch_notified_at = eve_now()
        pc.save(update_fields=["last_mismatch_notified_at", "updated_at"])


def _apply_failed(pc: PaymentContract, invoice: Invoice) -> None:
    """A matched contract went terminal-bad → revert invoice to emitted with a new code."""
    if invoice.is_paid:
        return  # already settled by another route; leave it
    if invoice.status == Invoice.EMITTED:
        return
    invoice.status = Invoice.EMITTED
    invoice.paid_at = None
    invoice.regenerate_code(save=False)
    invoice.save(update_fields=["status", "paid_at", "code", "updated_at"])


def _fetch_items(token, corp_id: int, pc: PaymentContract):
    """Fetch + cache contract items. Returns ``({type_id: qty_offered}, has_requested)``."""
    try:
        rows = providers.contract_items(token, corp_id, pc.contract_id)
    except Exception:  # noqa: BLE001 - treat unreadable items as a mismatch, don't crash
        logger.exception("moontax: failed to fetch items for contract %s", pc.contract_id)
        return {item.ore_type_id: item.units_owed for item in []}, True

    offered: dict[int, int] = defaultdict(int)
    has_requested = False
    for row in rows:
        if _g(row, "is_included", True):
            offered[_g(row, "type_id")] += int(_g(row, "quantity", 0) or 0)
        else:
            has_requested = True
    offered = dict(offered)

    pc.offered_items = [{"type_id": k, "quantity": v} for k, v in offered.items()]
    pc.has_requested_items = has_requested
    pc.items_fetched = True
    pc.save(update_fields=["offered_items", "has_requested_items", "items_fetched", "updated_at"])
    return offered, has_requested


def _notify_mismatch(invoice, expected, submitted, pc) -> None:
    try:
        from moontax import notifications

        notifications.notify_mismatch(invoice, expected, submitted, pc)
    except Exception:  # noqa: BLE001
        logger.exception("moontax: failed to notify mismatch for %s", invoice.code)
