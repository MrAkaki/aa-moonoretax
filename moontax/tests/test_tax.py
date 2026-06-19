"""Tax engine: attribution windows, floor/zero, idempotency (Requirements §5)."""

import datetime as dt
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from moontax.core import tax
from moontax.models import Invoice, OreTaxRate, OreType
from moontax.tests.helpers import (
    link_character,
    make_config,
    make_extraction,
    make_ledger,
    make_structure,
    make_user,
)

UTC = dt.timezone.utc
ORE_A, ORE_B = 46300, 46301


def _dt(day, hour=0):
    return dt.datetime(2026, 1, day, hour, tzinfo=UTC)


@mock.patch("moontax.notifications.notify_invoice", lambda invoice: None)
class TaxEngineTest(TestCase):
    def setUp(self):
        self.config = make_config(default_tax_rate=Decimal("0.10"), despawn_hours=48)
        self.structure = make_structure(structure_id=1001)
        self.user = make_user("miner")
        link_character(self.user, 90001, "Miner Main")
        # Two pops: window of pop1 is [Jan 10, Jan 15).
        self.pop1 = make_extraction(self.structure, _dt(10))
        self.pop2 = make_extraction(self.structure, _dt(15))

    def test_attribution_window_excludes_other_pop(self):
        # In pop1 window
        make_ledger(1001, 90001, ORE_A, dt.date(2026, 1, 10), 100)
        make_ledger(1001, 90001, ORE_A, dt.date(2026, 1, 14), 200)
        # In pop2 window (must be excluded from pop1)
        make_ledger(1001, 90001, ORE_A, dt.date(2026, 1, 15), 9999)
        units = tax.units_by_character(self.pop1)
        self.assertEqual(units[(90001, ORE_A)], 300)

    def test_floor_and_zero_drop(self):
        make_ledger(1001, 90001, ORE_A, dt.date(2026, 1, 11), 101)  # ->10
        make_ledger(1001, 90001, ORE_B, dt.date(2026, 1, 11), 9)    # ->0 dropped
        owed, _users = tax.compute_owed(self.pop1, self.config)
        self.assertEqual(owed[self.user.pk], {ORE_A: 10})

    def test_zero_total_emits_no_invoice(self):
        make_ledger(1001, 90001, ORE_B, dt.date(2026, 1, 11), 9)  # floors to 0
        tax.finalize_pop(self.pop1, self.config)
        self.assertEqual(Invoice.objects.filter(user=self.user).count(), 0)

    def test_unlinked_ore_is_lost(self):
        make_ledger(1001, 70007, ORE_A, dt.date(2026, 1, 11), 1000)  # unlinked char
        owed, _ = tax.compute_owed(self.pop1, self.config)
        self.assertEqual(owed, {})

    def test_finalize_is_idempotent(self):
        make_ledger(1001, 90001, ORE_A, dt.date(2026, 1, 11), 100)
        tax.finalize_pop(self.pop1, self.config)
        tax.finalize_pop(self.pop1, self.config)
        invoices = Invoice.objects.filter(user=self.user, extraction=self.pop1)
        self.assertEqual(invoices.count(), 1)
        self.assertEqual(invoices.first().items.get(ore_type_id=ORE_A).units_owed, 10)

    def test_finalize_never_recomputes_paid(self):
        make_ledger(1001, 90001, ORE_A, dt.date(2026, 1, 11), 100)
        tax.finalize_pop(self.pop1, self.config)
        inv = Invoice.objects.get(user=self.user, extraction=self.pop1)
        inv.status = Invoice.MARKED_PAID
        inv.save()
        # More ore appears, but a paid invoice must not be recomputed.
        make_ledger(1001, 90001, ORE_A, dt.date(2026, 1, 12), 500)
        tax.finalize_pop(self.pop1, self.config)
        inv.refresh_from_db()
        self.assertEqual(inv.items.get(ore_type_id=ORE_A).units_owed, 10)


class VariantRateInheritanceTest(TestCase):
    """Quality/compressed variants must inherit their base ore's explicit tax rate."""

    # type_id constants matching real EVE IDs for clarity; the test is self-contained.
    BASE_TYPE_ID = 45492    # Bitumens (base ore)
    VARIANT_TYPE_ID = 46282  # Brimful Bitumens (quality variant)
    UNRELATED_TYPE_ID = 99999  # An ore with no OreType row

    def setUp(self):
        self.config = make_config(default_tax_rate=Decimal("0.05"), despawn_hours=48)
        # Base ore — no parent.
        OreType.objects.create(
            type_id=self.BASE_TYPE_ID,
            name="Bitumens",
            group_id=1884,
            base_type_id=None,
        )
        # Quality variant pointing at the base.
        OreType.objects.create(
            type_id=self.VARIANT_TYPE_ID,
            name="Brimful Bitumens",
            group_id=1884,
            base_type_id=self.BASE_TYPE_ID,
        )
        # Explicit rate on the BASE ore only — the variant has no row.
        OreTaxRate.objects.create(
            ore_type_id=self.BASE_TYPE_ID,
            ore_type_name="Bitumens",
            rate=Decimal("0.20"),
        )

    def test_variant_inherits_base_rate(self):
        """_rate_for(variant) must return the base ore's explicit rate (0.20), not the
        config default (0.05)."""
        rate = tax._rate_for(self.VARIANT_TYPE_ID, self.config)
        self.assertEqual(rate, Decimal("0.20"))

    def test_unrelated_ore_falls_back_to_default(self):
        """An ore with no OreType row and no explicit rate must use the default."""
        rate = tax._rate_for(self.UNRELATED_TYPE_ID, self.config)
        self.assertEqual(rate, Decimal("0.05"))
