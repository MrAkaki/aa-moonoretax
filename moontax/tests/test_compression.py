"""Compressed-ore payment equivalence math (Requirements §6)."""

from django.test import SimpleTestCase

from moontax.core import compression


class CompressedUnitsTest(SimpleTestCase):
    def test_one_to_one_mapping(self):
        # At 1:1 every raw unit maps to exactly one compressed unit.
        self.assertEqual(compression.compressed_units(220), 220)
        self.assertEqual(compression.compressed_units(1000), 1000)

    def test_zero_owed_is_zero(self):
        self.assertEqual(compression.compressed_units(0), 0)

    def test_small_line_has_compressed_option(self):
        # Even a line of 5 has a compressed alternative of 5 (the old "under 100 → none" rule is gone).
        self.assertEqual(compression.compressed_units(5), 5)


class LineSatisfiedTest(SimpleTestCase):
    def test_full_payment_in_raw(self):
        self.assertTrue(compression.line_satisfied(220, 220, 0))

    def test_underpayment_in_raw_rejected(self):
        self.assertFalse(compression.line_satisfied(220, 200, 0))
        self.assertFalse(compression.line_satisfied(220, 219, 0))

    def test_full_payment_in_compressed(self):
        # 220 compressed units exactly covers 220 owed.
        self.assertTrue(compression.line_satisfied(220, 0, 220))

    def test_compressed_overpayment_ok(self):
        self.assertTrue(compression.line_satisfied(220, 0, 300))

    def test_mix_raw_and_compressed(self):
        # 100 raw + 120 compressed == 220 (exact).
        self.assertTrue(compression.line_satisfied(220, 100, 120))
        # 100 raw + 119 compressed == 219 < 220 (one short).
        self.assertFalse(compression.line_satisfied(220, 100, 119))

    def test_small_line_compressed_option_exists(self):
        # Owed 5 → can pay 5 compressed (1:1, no minimum threshold).
        self.assertTrue(compression.line_satisfied(5, 0, 5))
        self.assertTrue(compression.line_satisfied(5, 5, 0))
        self.assertFalse(compression.line_satisfied(5, 0, 4))

    def test_zero_owed_always_satisfied(self):
        self.assertTrue(compression.line_satisfied(0, 0, 0))

    def test_exact_payment(self):
        self.assertTrue(compression.line_satisfied(1, 1, 0))
        self.assertTrue(compression.line_satisfied(1, 0, 1))
        self.assertFalse(compression.line_satisfied(1, 0, 0))
