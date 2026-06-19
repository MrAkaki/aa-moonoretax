"""Compressed-ore payment equivalence math (Requirements §6)."""

from django.test import SimpleTestCase

from moontax.core import compression


class CompressedUnitsTest(SimpleTestCase):
    def test_rounds_down_to_whole_compressed_units(self):
        self.assertEqual(compression.compressed_units(250), 2)
        self.assertEqual(compression.compressed_units(1000), 10)

    def test_under_one_unit_rounds_to_zero(self):
        self.assertEqual(compression.compressed_units(99), 0)
        self.assertEqual(compression.compressed_units(0), 0)


class LineSatisfiedTest(SimpleTestCase):
    def test_full_payment_in_raw(self):
        self.assertTrue(compression.line_satisfied(250, 250, 0))

    def test_underpayment_in_raw_rejected(self):
        # All-raw shortfall is never forgiven (only the compressed remainder is).
        self.assertFalse(compression.line_satisfied(250, 200, 0))
        self.assertFalse(compression.line_satisfied(250, 249, 0))

    def test_compressed_only_forgives_sub_ratio_remainder(self):
        # 2 compressed == 200 raw covers a 250 debt; the 50 remainder is forgiven.
        self.assertTrue(compression.line_satisfied(250, 0, 2))

    def test_compressed_overpayment_ok(self):
        self.assertTrue(compression.line_satisfied(250, 0, 3))

    def test_mix_raw_and_compressed(self):
        self.assertTrue(compression.line_satisfied(250, 150, 1))  # 100 + 150 == 250
        self.assertTrue(compression.line_satisfied(250, 50, 2))  # 200 + 50 == 250

    def test_partial_compressed_below_whole_count_needs_raw_topup(self):
        # 1 compressed (=100) on a 250 debt is short and below the 2 whole units owed.
        self.assertFalse(compression.line_satisfied(250, 0, 1))
        self.assertFalse(compression.line_satisfied(250, 100, 1))  # 200 < 250, comp 1 < 2

    def test_small_line_has_no_compressed_forgiveness(self):
        # Under one whole compressed unit: must be paid in raw (can't be forgiven away).
        self.assertFalse(compression.line_satisfied(50, 0, 0))
        self.assertTrue(compression.line_satisfied(50, 50, 0))
        self.assertTrue(compression.line_satisfied(50, 0, 1))  # 100 >= 50 (overpay)

    def test_exact_multiple_of_ratio(self):
        self.assertTrue(compression.line_satisfied(200, 0, 2))
        self.assertFalse(compression.line_satisfied(200, 0, 1))
