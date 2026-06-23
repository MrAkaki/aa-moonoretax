"""Contract reconciliation: matching, status machine, mismatch, revert (Requirements §6)."""

import datetime as dt
from unittest import mock

from django.test import TestCase

from moontax.core import reconcile
from moontax.models import Invoice, InvoiceItem, OreType
from moontax.tests.helpers import (
    link_character,
    make_config,
    make_extraction,
    make_structure,
    make_user,
)

UTC = dt.timezone.utc
# CORP is the payment corp id — assignee_id on all contracts sent to it.
CORP = 2001
# A different corp id (e.g. the mining corp); contracts assigned here must be ignored.
OTHER_CORP = 9999
ORE_A = 46300


def _contract(code, status="outstanding", issuer_id=90001, **kw):
    base = dict(
        contract_id=kw.get("contract_id", 5001),
        type="item_exchange",
        status=status,
        issuer_id=issuer_id,
        assignee_id=CORP,
        title=f"pay {code} thanks",
        price=0,
        reward=0,
        volume=10.0,
        start_location_id=60003760,
        date_issued=dt.datetime(2026, 1, 11, tzinfo=UTC),
    )
    base.update(kw)
    return base


class ReconcileTest(TestCase):
    def setUp(self):
        make_config()
        self.structure = make_structure()
        self.pop = make_extraction(self.structure, dt.datetime(2026, 1, 10, tzinfo=UTC))
        self.user = make_user("payer")
        link_character(self.user, 90001, "Payer Main")
        self.invoice = Invoice.objects.create(
            code="MT-ABC123",
            user=self.user,
            extraction=self.pop,
            structure=self.structure,
            status=Invoice.EMITTED,
        )
        InvoiceItem.objects.create(invoice=self.invoice, ore_type_id=ORE_A, units_owed=100)

    def _items(self, offered):
        return [{"type_id": tid, "quantity": q, "is_included": True} for tid, q in offered.items()]

    def test_no_match_when_wrong_issuer(self):
        with mock.patch("moontax.providers.contract_items", return_value=[]):
            reconcile.ingest_and_reconcile(None, CORP, [_contract("MT-ABC123", issuer_id=88888)])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.EMITTED)

    def test_pending_matching_items_sets_payment_sent(self):
        with mock.patch("moontax.providers.contract_items", return_value=self._items({ORE_A: 100})):
            with mock.patch("moontax.notifications.notify_mismatch") as notify:
                reconcile.ingest_and_reconcile(None, CORP, [_contract("MT-ABC123")])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.PAYMENT_SENT)
        notify.assert_not_called()

    def test_pending_mismatch_notifies_once(self):
        with mock.patch("moontax.providers.contract_items", return_value=self._items({ORE_A: 50})):
            with mock.patch("moontax.notifications.notify_mismatch") as notify:
                reconcile.ingest_and_reconcile(None, CORP, [_contract("MT-ABC123")])
                reconcile.ingest_and_reconcile(None, CORP, [_contract("MT-ABC123")])
        self.assertEqual(notify.call_count, 1)  # throttled by last_mismatch_notified_at

    def test_finished_marks_accepted_even_if_unverified(self):
        with mock.patch("moontax.providers.contract_items", return_value=[]):
            reconcile.ingest_and_reconcile(None, CORP, [_contract("MT-ABC123", status="finished")])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.PAYMENT_ACCEPTED)
        self.assertIsNotNone(self.invoice.paid_at)

    def test_failed_reverts_to_emitted_with_new_code(self):
        # First make it payment_sent.
        with mock.patch("moontax.providers.contract_items", return_value=self._items({ORE_A: 100})):
            reconcile.ingest_and_reconcile(None, CORP, [_contract("MT-ABC123")])
        with mock.patch("moontax.providers.contract_items", return_value=[]):
            reconcile.ingest_and_reconcile(None, CORP, [_contract("MT-ABC123", status="cancelled")])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.EMITTED)
        self.assertNotEqual(self.invoice.code, "MT-ABC123")

    def test_contract_assigned_to_other_corp_is_ignored(self):
        """A contract whose assignee_id is NOT the payment corp must be ignored.

        Passing OTHER_CORP as the corp_id (the payment corp id for this run) means
        the contract assigned to CORP (a different corp) should not match any invoice.
        """
        # Build a contract assigned to CORP (== 2001), but ingest it as if the
        # payment corp is OTHER_CORP (== 9999).  The assignee_id check filters it out.
        wrong_corp_contract = dict(
            contract_id=5099,
            type="item_exchange",
            status="outstanding",
            issuer_id=90001,
            assignee_id=CORP,  # assigned to mining corp, not payment corp
            title="pay MT-ABC123 thanks",
            price=0,
            reward=0,
            volume=10.0,
            start_location_id=60003760,
            date_issued=dt.datetime(2026, 1, 11, tzinfo=UTC),
        )
        with mock.patch("moontax.providers.contract_items", return_value=[]):
            reconcile.ingest_and_reconcile(None, OTHER_CORP, [wrong_corp_contract])
        self.invoice.refresh_from_db()
        # Invoice must remain EMITTED — the wrong-corp contract must not match it.
        self.assertEqual(self.invoice.status, Invoice.EMITTED)


# Compressed counterpart of ORE_A (same quality tier), per the ESI catalog naming.
ORE_A_COMPRESSED = 62455


class CompressedPaymentTest(TestCase):
    """Players may pay a line in raw ore, the compressed equivalent, or a mix (§6)."""

    def setUp(self):
        make_config()
        self.structure = make_structure()
        self.pop = make_extraction(self.structure, dt.datetime(2026, 1, 10, tzinfo=UTC))
        self.user = make_user("payer")
        link_character(self.user, 90001, "Payer Main")
        self.invoice = Invoice.objects.create(
            code="MT-ABC123",
            user=self.user,
            extraction=self.pop,
            structure=self.structure,
            status=Invoice.EMITTED,
        )
        # Owe 250 → must pay 250 raw, or 250 compressed, or any mix summing to ≥250.
        InvoiceItem.objects.create(
            invoice=self.invoice, ore_type_id=ORE_A, ore_type_name="Brimful Bitumens", units_owed=250
        )
        OreType.objects.create(type_id=ORE_A, name="Brimful Bitumens", base_type_id=45492)
        OreType.objects.create(
            type_id=ORE_A_COMPRESSED, name="Compressed Brimful Bitumens", base_type_id=45492
        )

    def _run(self, offered):
        items = [{"type_id": t, "quantity": q, "is_included": True} for t, q in offered.items()]
        with mock.patch("moontax.providers.contract_items", return_value=items):
            with mock.patch("moontax.notifications.notify_mismatch") as notify:
                reconcile.ingest_and_reconcile(None, CORP, [_contract("MT-ABC123")])
        self.invoice.refresh_from_db()
        return notify

    def test_full_compressed_payment_accepted(self):
        # 250 compressed == 250 raw-equiv (1:1); exact payment, no forgiveness needed.
        notify = self._run({ORE_A_COMPRESSED: 250})
        self.assertEqual(self.invoice.status, Invoice.PAYMENT_SENT)
        notify.assert_not_called()

    def test_mixed_raw_and_compressed_accepted(self):
        # 100 raw + 150 compressed == 250 (exact mix).
        notify = self._run({ORE_A_COMPRESSED: 150, ORE_A: 100})
        self.assertEqual(self.invoice.status, Invoice.PAYMENT_SENT)
        notify.assert_not_called()

    def test_partial_compressed_without_topup_is_mismatch(self):
        # 249 compressed < 250 owed — one unit short.
        notify = self._run({ORE_A_COMPRESSED: 249})
        notify.assert_called_once()

    def test_overpayment_in_compressed_accepted(self):
        # 300 compressed > 250 owed — overpayment is fine.
        notify = self._run({ORE_A_COMPRESSED: 300})
        self.assertEqual(self.invoice.status, Invoice.PAYMENT_SENT)
        notify.assert_not_called()
