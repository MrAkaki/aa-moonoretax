"""Tests for the staff-tab pop_ore_details context variable and tax.pop_ore_breakdown.

Covers:
- tax.pop_ore_breakdown returns correct names + summed units (matched + unmatched),
  sorted by units descending, with OreType catalog as primary name source.
- The staff view context contains pop_ore_details with the contracted shape:
  keys are str(pop.pk), values are lists of {"name": str, "units": int}.
"""

import datetime as dt

from django.contrib.auth.models import Permission
from django.test import Client, TestCase

from moontax.core import tax
from moontax.models import EveName, MoonPopSummary, OreType, UnmatchedMiner
from moontax.tests.helpers import (
    link_character,
    make_config,
    make_extraction,
    make_ledger,
    make_structure,
    make_user,
)

# Alliance Auth requires a main character on the user profile for views decorated
# with main_character_required (applied automatically to all UrlHook-registered views).
try:
    from allianceauth.authentication.models import UserProfile

    def _set_main_character(user, character):
        """Assign ``character`` as the user's main character in their AA profile."""
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.main_character = character
        profile.save()

except ImportError:
    def _set_main_character(user, character):  # pragma: no cover — AA always present
        pass

UTC = dt.timezone.utc

# Ore type ids used in tests.
ORE_A = 46312   # has OreType row — primary name source
ORE_B = 46313   # has OreType row
ORE_C = 45510   # EveName only (no OreType row)


def _dt(day: int, hour: int = 0) -> dt.datetime:
    return dt.datetime(2026, 3, day, hour, tzinfo=UTC)


class PopOreBreakdownTest(TestCase):
    """Unit tests for tax.pop_ore_breakdown."""

    def setUp(self):
        make_config()
        self.structure = make_structure(structure_id=2001, name="Athanor Alpha")
        self.user = make_user("miner1")
        link_character(self.user, 90001, "Char A")

        # Two pops on the same structure: window of pop1 is [Mar 5, Mar 10).
        self.pop1 = make_extraction(self.structure, _dt(5))
        self.pop2 = make_extraction(self.structure, _dt(10))

        # OreType catalog rows.
        OreType.objects.create(type_id=ORE_A, name="Zeolites", group_id=1884)
        OreType.objects.create(type_id=ORE_B, name="Coesite", group_id=1884)

        # EveName fallback for ORE_C.
        EveName.objects.create(eve_id=ORE_C, name="Cinnabar", category=EveName.ORE)

    def _date(self, day: int) -> dt.date:
        return dt.date(2026, 3, day)

    def test_matched_ore_summed_correctly(self):
        """MiningLedger rows are aggregated per ore_type_id."""
        make_ledger(2001, 90001, ORE_A, self._date(5), 1000)
        make_ledger(2001, 90001, ORE_A, self._date(6), 500)
        make_ledger(2001, 90001, ORE_B, self._date(5), 200)

        result = tax.pop_ore_breakdown(self.pop1)

        ore_a = next(r for r in result if r["type_id"] == ORE_A)
        ore_b = next(r for r in result if r["type_id"] == ORE_B)
        self.assertEqual(ore_a["units"], 1500)
        self.assertEqual(ore_b["units"], 200)

    def test_unmatched_ore_included(self):
        """UnmatchedMiner rows are included and summed together with matched rows."""
        make_ledger(2001, 90001, ORE_A, self._date(5), 800)
        UnmatchedMiner.objects.create(
            observer_id=2001,
            character_id=99999,
            character_name="Unknown",
            ore_type_id=ORE_A,
            recorded_date=self._date(5),
            quantity=200,
        )

        result = tax.pop_ore_breakdown(self.pop1)

        ore_a = next(r for r in result if r["type_id"] == ORE_A)
        self.assertEqual(ore_a["units"], 1000)  # 800 matched + 200 unmatched

    def test_unmatched_only_ore_type(self):
        """An ore type that only appears in UnmatchedMiner still appears in result."""
        UnmatchedMiner.objects.create(
            observer_id=2001,
            character_id=99999,
            character_name="Ghost",
            ore_type_id=ORE_B,
            recorded_date=self._date(7),
            quantity=333,
        )

        result = tax.pop_ore_breakdown(self.pop1)

        ore_b = next((r for r in result if r["type_id"] == ORE_B), None)
        self.assertIsNotNone(ore_b)
        self.assertEqual(ore_b["units"], 333)

    def test_names_resolved_from_oretype(self):
        """OreType catalog is the primary name source."""
        make_ledger(2001, 90001, ORE_A, self._date(5), 100)

        result = tax.pop_ore_breakdown(self.pop1)

        ore_a = next(r for r in result if r["type_id"] == ORE_A)
        self.assertEqual(ore_a["name"], "Zeolites")

    def test_names_fallback_to_evename(self):
        """EveName is used when there is no OreType row."""
        make_ledger(2001, 90001, ORE_C, self._date(5), 100)

        result = tax.pop_ore_breakdown(self.pop1)

        ore_c = next(r for r in result if r["type_id"] == ORE_C)
        self.assertEqual(ore_c["name"], "Cinnabar")

    def test_names_fallback_to_raw_id_string(self):
        """An ore with neither OreType nor EveName resolves to str(type_id)."""
        unknown_id = 99998
        make_ledger(2001, 90001, unknown_id, self._date(5), 50)

        result = tax.pop_ore_breakdown(self.pop1)

        raw = next((r for r in result if r["type_id"] == unknown_id), None)
        self.assertIsNotNone(raw)
        self.assertEqual(raw["name"], str(unknown_id))

    def test_sorted_by_units_descending(self):
        """Result is sorted by units descending."""
        make_ledger(2001, 90001, ORE_A, self._date(5), 300)   # smaller
        make_ledger(2001, 90001, ORE_B, self._date(5), 1000)  # larger

        result = tax.pop_ore_breakdown(self.pop1)

        units = [r["units"] for r in result]
        self.assertEqual(units, sorted(units, reverse=True))

    def test_window_excludes_next_pop(self):
        """Ledger rows dated >= next pop's chunk_arrival_time.date() are excluded."""
        make_ledger(2001, 90001, ORE_A, self._date(5), 500)   # in pop1 window
        make_ledger(2001, 90001, ORE_A, self._date(10), 9999)  # pop2 window — must be excluded

        result = tax.pop_ore_breakdown(self.pop1)

        ore_a = next(r for r in result if r["type_id"] == ORE_A)
        self.assertEqual(ore_a["units"], 500)

    def test_empty_pop_returns_empty_list(self):
        """A pop with no ledger or unmatched rows returns []."""
        result = tax.pop_ore_breakdown(self.pop1)
        self.assertEqual(result, [])

    def test_returns_list_of_dicts_with_required_keys(self):
        """Each entry has type_id, name, and units keys."""
        make_ledger(2001, 90001, ORE_A, self._date(5), 100)
        result = tax.pop_ore_breakdown(self.pop1)
        self.assertTrue(len(result) > 0)
        for entry in result:
            self.assertIn("type_id", entry)
            self.assertIn("name", entry)
            self.assertIn("units", entry)


class StaffViewPopOreDetailsTest(TestCase):
    """The staff view context contains pop_ore_details with the contracted shape."""

    def setUp(self):
        make_config()

        # Structure + extractions.
        self.structure = make_structure(structure_id=3001, name="Athanor Beta")

        # OreType catalog rows.
        OreType.objects.create(type_id=ORE_A, name="Zeolites", group_id=1884)
        OreType.objects.create(type_id=ORE_B, name="Coesite", group_id=1884)

        # A user with staff_access permission (scoped by app_label to avoid collision).
        self.staff_user = make_user("staffuser")
        # AA wraps all UrlHook-registered views with main_character_required, which
        # redirects to /dashboard/ if the user has no main character.  Assign one so
        # the test request reaches the actual view.
        staff_char = link_character(self.staff_user, 80001, "Staff Main")
        _set_main_character(self.staff_user, staff_char)

        perm = Permission.objects.get(
            codename="staff_access",
            content_type__app_label="moontax",
        )
        self.staff_user.user_permissions.add(perm)

        self.client = Client()
        # Re-fetch user to clear Django's in-memory permission cache before force_login.
        self.staff_user = type(self.staff_user).objects.get(pk=self.staff_user.pk)
        self.client.force_login(self.staff_user)

    def _date(self, day: int) -> dt.date:
        return dt.date(2026, 3, day)

    def _make_pop_summary(self, extraction):
        """Create a MoonPopSummary for the given extraction."""
        return MoonPopSummary.objects.create(
            extraction=extraction,
            structure=self.structure,
            moon=None,
            ore_mined_units=0,
        )

    def test_staff_view_returns_200(self):
        """The staff view returns HTTP 200 for a user with staff_access."""
        response = self.client.get("/moontax/staff/")
        self.assertEqual(response.status_code, 200)

    def test_pop_ore_details_in_context(self):
        """pop_ore_details is present in the staff view context."""
        response = self.client.get("/moontax/staff/")
        self.assertIn("pop_ore_details", response.context)

    def test_pop_ore_details_keys_are_str_pks(self):
        """pop_ore_details keys are str(pop.pk), not ints."""
        pop1_ext = make_extraction(self.structure, _dt(5))
        pop1 = self._make_pop_summary(pop1_ext)

        response = self.client.get("/moontax/staff/")
        details = response.context["pop_ore_details"]

        self.assertIn(str(pop1.pk), details)
        # Keys must be strings.
        for key in details:
            self.assertIsInstance(key, str)

    def test_pop_ore_details_value_shape(self):
        """Each value in pop_ore_details is a list of {"name": str, "units": int}."""
        pop1_ext = make_extraction(self.structure, _dt(5))
        # Seed a second extraction to close the attribution window.
        make_extraction(self.structure, _dt(10))
        make_ledger(3001, 90001, ORE_A, self._date(5), 1200)
        make_ledger(3001, 90001, ORE_B, self._date(6), 300)
        pop1 = self._make_pop_summary(pop1_ext)

        response = self.client.get("/moontax/staff/")
        details = response.context["pop_ore_details"]
        entries = details[str(pop1.pk)]

        self.assertIsInstance(entries, list)
        for entry in entries:
            self.assertIn("name", entry)
            self.assertIn("units", entry)
            # Contracted shape must NOT expose type_id to the template.
            self.assertNotIn("type_id", entry)
            self.assertIsInstance(entry["name"], str)
            self.assertIsInstance(entry["units"], int)

    def test_pop_ore_details_correct_units_and_names(self):
        """Units are summed correctly and names resolved from OreType catalog."""
        pop1_ext = make_extraction(self.structure, _dt(5))
        make_extraction(self.structure, _dt(10))  # closes window
        make_ledger(3001, 90001, ORE_A, self._date(5), 2000)
        make_ledger(3001, 90001, ORE_A, self._date(7), 500)   # same ore, different day
        make_ledger(3001, 90001, ORE_B, self._date(5), 800)
        # Also an unmatched miner row.
        UnmatchedMiner.objects.create(
            observer_id=3001,
            character_id=88888,
            character_name="Stranger",
            ore_type_id=ORE_B,
            recorded_date=self._date(6),
            quantity=200,
        )
        pop1 = self._make_pop_summary(pop1_ext)

        response = self.client.get("/moontax/staff/")
        details = response.context["pop_ore_details"]
        entries = details[str(pop1.pk)]

        entries_by_name = {e["name"]: e["units"] for e in entries}
        self.assertEqual(entries_by_name.get("Zeolites"), 2500)   # 2000 + 500
        self.assertEqual(entries_by_name.get("Coesite"), 1000)    # 800 matched + 200 unmatched

    def test_pop_ore_details_sorted_desc(self):
        """Entries within each pop are sorted by units descending."""
        pop1_ext = make_extraction(self.structure, _dt(5))
        make_extraction(self.structure, _dt(10))
        make_ledger(3001, 90001, ORE_A, self._date(5), 100)
        make_ledger(3001, 90001, ORE_B, self._date(5), 900)
        pop1 = self._make_pop_summary(pop1_ext)

        response = self.client.get("/moontax/staff/")
        details = response.context["pop_ore_details"]
        entries = details[str(pop1.pk)]

        units = [e["units"] for e in entries]
        self.assertEqual(units, sorted(units, reverse=True))

    def test_pop_with_no_ore_maps_to_empty_list(self):
        """A pop with no ledger rows maps to [] in pop_ore_details."""
        pop1_ext = make_extraction(self.structure, _dt(5))
        pop1 = self._make_pop_summary(pop1_ext)

        response = self.client.get("/moontax/staff/")
        details = response.context["pop_ore_details"]
        self.assertEqual(details[str(pop1.pk)], [])

    def test_multiple_pops_all_keyed(self):
        """All MoonPopSummary rows appear as keys in pop_ore_details."""
        ext1 = make_extraction(self.structure, _dt(1))
        ext2 = make_extraction(self.structure, _dt(5))
        pop1 = self._make_pop_summary(ext1)
        pop2 = self._make_pop_summary(ext2)

        response = self.client.get("/moontax/staff/")
        details = response.context["pop_ore_details"]
        self.assertIn(str(pop1.pk), details)
        self.assertIn(str(pop2.pk), details)
