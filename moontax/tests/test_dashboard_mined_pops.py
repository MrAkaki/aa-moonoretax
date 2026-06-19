"""Tests for the "What you mined (last 60 days)" table context (mined_pops).

Calls ``_build_pop_data`` directly — the private helper extracted from the index
view — so no HTTP layer, template rendering, or AA permission/main-character
decorators are needed.

Verifies:
- One dict per pop (not one per ledger row).
- Per-pop ore breakdown with summed units across characters.
- Ore names resolved from OreType catalog (not raw ids).
- OreType wins over EveName; EveName wins over raw id.
- Pops outside the 60-day cutoff are excluded from mined_pops.
- The pie chart list (pop_charts) still works alongside mined_pops.
"""

import datetime as dt

from django.test import TestCase

from moontax.models import EveName, OreType
from moontax.tests.helpers import (
    link_character,
    make_config,
    make_extraction,
    make_ledger,
    make_structure,
    make_user,
)
from moontax.views import _build_pop_data

UTC = dt.timezone.utc

# Ore type ids used in tests.
ORE_IN_CATALOG = 46312     # has an OreType row → name resolved from catalog
ORE_IN_EVENAME = 45510     # no OreType row but has an EveName row → fallback name
ORE_RAW_ID_ONLY = 99999   # neither OreType nor EveName → falls back to "99999"


def _dt(days_ago: int) -> dt.datetime:
    """Return an aware UTC datetime N days before a fixed reference point."""
    now = dt.datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    return now - dt.timedelta(days=days_ago)


class MinedPopsContextTest(TestCase):
    """_build_pop_data populates pop_charts and mined_pops correctly."""

    def setUp(self):
        make_config()

        # User with two linked characters.
        self.user = make_user("miner")
        self.char1 = link_character(self.user, 90001, "Alpha")
        self.char2 = link_character(self.user, 90002, "Beta")

        # Structure.
        self.structure = make_structure(structure_id=1001, name="Drill Alpha")

        # OreType catalog entry (reliable source for name resolution).
        OreType.objects.create(
            type_id=ORE_IN_CATALOG,
            name="Zeolites",
            group_id=1884,
            base_type_id=None,
        )

        # EveName entry (fallback when OreType row absent).
        EveName.objects.create(
            eve_id=ORE_IN_EVENAME,
            name="Cinnabar",
            category=EveName.ORE,
        )

        # Two pops: one within 60-day window, one outside.
        self.pop_recent = make_extraction(self.structure, _dt(10))    # 10 days ago → in window
        self.pop_old = make_extraction(self.structure, _dt(90))       # 90 days ago → outside window

    def _seed_recent_pop(self):
        """Seed ledger rows for self.pop_recent (two ore types, two chars)."""
        base_date = self.pop_recent.chunk_arrival_time.date()
        # char1 mines both ore types on day 0.
        make_ledger(1001, 90001, ORE_IN_CATALOG, base_date, 1000)
        make_ledger(1001, 90001, ORE_IN_EVENAME, base_date, 500)
        # char2 mines ORE_IN_CATALOG on day 1.
        day1 = base_date + dt.timedelta(days=1)
        make_ledger(1001, 90002, ORE_IN_CATALOG, day1, 300)

    def _build(self):
        pop_charts, mined_pops = _build_pop_data(self.user)
        return pop_charts, mined_pops

    def test_one_entry_per_pop(self):
        """mined_pops has exactly one entry per pop (not one per ledger row)."""
        self._seed_recent_pop()
        _charts, pops = self._build()
        self.assertEqual(len(pops), 1, f"Expected 1 pop entry, got {len(pops)}: {pops}")

    def test_ore_names_from_oretype_catalog(self):
        """OreType catalog is the primary name source."""
        self._seed_recent_pop()
        _charts, pops = self._build()
        ore_names = {o["name"] for o in pops[0]["ores"]}
        # ORE_IN_CATALOG must resolve to "Zeolites", not the raw id.
        self.assertIn("Zeolites", ore_names)
        self.assertNotIn(str(ORE_IN_CATALOG), ore_names)

    def test_ore_names_fallback_to_evename(self):
        """EveName is used when OreType row is absent."""
        self._seed_recent_pop()
        _charts, pops = self._build()
        ore_names = {o["name"] for o in pops[0]["ores"]}
        self.assertIn("Cinnabar", ore_names)
        self.assertNotIn(str(ORE_IN_EVENAME), ore_names)

    def test_ore_units_summed_across_characters(self):
        """Units for the same ore across multiple characters are summed."""
        self._seed_recent_pop()
        _charts, pops = self._build()
        pop = pops[0]
        # ORE_IN_CATALOG: char1=1000 + char2=300 = 1300.
        catalog_ore = next(o for o in pop["ores"] if o["name"] == "Zeolites")
        self.assertEqual(catalog_ore["units"], 1300)
        # ORE_IN_EVENAME: char1=500 only.
        evename_ore = next(o for o in pop["ores"] if o["name"] == "Cinnabar")
        self.assertEqual(evename_ore["units"], 500)

    def test_total_units_is_sum_of_all_ores(self):
        """total_units is the sum of all ore units in the pop."""
        self._seed_recent_pop()
        _charts, pops = self._build()
        self.assertEqual(pops[0]["total_units"], 1800)  # 1300 + 500

    def test_old_pop_excluded_from_mined_pops(self):
        """Pops older than 60 days must not appear in mined_pops."""
        old_date = self.pop_old.chunk_arrival_time.date()
        make_ledger(1001, 90001, ORE_IN_CATALOG, old_date, 999)
        _charts, pops = self._build()
        self.assertEqual(pops, [])

    def test_pop_includes_required_keys(self):
        """Each pop entry carries date, structure, moon, ores, and total_units."""
        self._seed_recent_pop()
        _charts, pops = self._build()
        pop = pops[0]
        for key in ("date", "structure", "moon", "ores", "total_units"):
            self.assertIn(key, pop)
        self.assertEqual(pop["structure"], "Drill Alpha")

    def test_raw_id_fallback_when_no_name_source(self):
        """An ore with neither OreType nor EveName resolves to its raw id string."""
        base_date = self.pop_recent.chunk_arrival_time.date()
        make_ledger(1001, 90001, ORE_RAW_ID_ONLY, base_date, 200)
        _charts, pops = self._build()
        ore_names = {o["name"] for o in pops[0]["ores"]}
        self.assertIn(str(ORE_RAW_ID_ONLY), ore_names)

    def test_pie_chart_still_populated(self):
        """pop_charts (pie chart) is not broken by the new table logic."""
        self._seed_recent_pop()
        charts, _pops = self._build()
        self.assertGreater(len(charts), 0)
        chart = charts[0]
        self.assertIn("labels", chart)
        self.assertIn("data", chart)
        self.assertGreater(sum(chart["data"]), 0)

    def test_user_with_no_mining_gets_empty_lists(self):
        """A user whose characters have no ledger rows sees empty lists."""
        charts, pops = self._build()
        self.assertEqual(pops, [])
        self.assertEqual(charts, [])

    def test_old_pop_appears_in_pie_chart_not_table(self):
        """Pops 60-180 days ago appear in pop_charts but not in mined_pops."""
        old_date = self.pop_old.chunk_arrival_time.date()
        make_ledger(1001, 90001, ORE_IN_CATALOG, old_date, 500)
        charts, pops = self._build()
        # Not in mined_pops (> 60 days).
        self.assertEqual(pops, [])
        # Is in pop_charts (within 180-day pie window).
        self.assertEqual(len(charts), 1)

    def test_oretype_wins_over_evename(self):
        """When both OreType and EveName exist for an id, OreType name is used."""
        # Add an EveName for ORE_IN_CATALOG (which already has an OreType row).
        EveName.objects.create(
            eve_id=ORE_IN_CATALOG,
            name="ShouldNotAppear",
            category=EveName.ORE,
        )
        base_date = self.pop_recent.chunk_arrival_time.date()
        make_ledger(1001, 90001, ORE_IN_CATALOG, base_date, 100)
        _charts, pops = self._build()
        ore_names = {o["name"] for o in pops[0]["ores"]}
        self.assertIn("Zeolites", ore_names)           # OreType name
        self.assertNotIn("ShouldNotAppear", ore_names)  # EveName must not win
