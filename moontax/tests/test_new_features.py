"""Tests for Items 2, 3 and 4: ore name resolution, expected-ore column, mark-dead.

Item 2 — UnmatchedMiner ore names use OreType catalog (not EveName or raw id).
Item 3 — _structure_rows includes expected_ore from ore_volume_by_type.
Item 4 — staff_mark_pop_dead view: force-finalizes, sets fracture_time, emits
          invoices; already-finalized pops are left unchanged; non-staff is blocked.
"""

import datetime as dt

from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.utils import timezone

from moontax.core import tax
from moontax.models import (
    Configuration,
    Extraction,
    Invoice,
    MoonPopSummary,
    OreTaxRate,
    OreType,
    Structure,
    UnmatchedMiner,
)
from moontax.tests.helpers import (
    link_character,
    make_config,
    make_extraction,
    make_ledger,
    make_structure,
    make_user,
)
from moontax.views import _structure_rows

UTC = dt.timezone.utc

# Ore type ids used in tests.
ORE_CATALOG = 46312    # has OreType row, NO EveName row
ORE_EVENAME = 45510    # no OreType row, has EveName row (via tax module)
ORE_RAW = 99997        # neither OreType nor EveName

try:
    from allianceauth.authentication.models import UserProfile

    def _set_main_character(user, character):
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.main_character = character
        profile.save()

except ImportError:
    def _set_main_character(user, character):  # pragma: no cover
        pass


def _make_staff_client(username: str = "staffuser", char_id: int = 80001):
    """Create a staff-permissioned user and return (user, Client)."""
    user = make_user(username)
    char = link_character(user, char_id, f"Main {username}")
    _set_main_character(user, char)
    perm = Permission.objects.get(codename="staff_access", content_type__app_label="moontax")
    user.user_permissions.add(perm)
    # Re-fetch to clear the permission cache.
    user = type(user).objects.get(pk=user.pk)
    client = Client()
    client.force_login(user)
    return user, client


def _dt(day: int, hour: int = 0) -> dt.datetime:
    return dt.datetime(2026, 3, day, hour, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# Item 2 — UnmatchedMiner ore name resolution
# --------------------------------------------------------------------------------------


class UnmatchedMinerOreNameTest(TestCase):
    """Ore names in the unmatched-miners table use OreType catalog first."""

    def setUp(self):
        make_config()
        self.structure = make_structure(structure_id=4001, name="Drill Item2")
        # OreType catalog entry but NO EveName entry.
        OreType.objects.create(type_id=ORE_CATALOG, name="Zeolites", group_id=1884)
        self.staff_user, self.client = _make_staff_client("staffuser2", 80002)

    def _make_unmatched(self, ore_type_id: int):
        UnmatchedMiner.objects.create(
            observer_id=4001,
            character_id=77001,
            character_name="Ghost",
            ore_type_id=ore_type_id,
            recorded_date=timezone.now().date(),
            quantity=500,
        )

    def test_catalog_ore_resolves_to_catalog_name_not_id(self):
        """An ore in OreType catalog but NOT in EveName shows catalog name."""
        self._make_unmatched(ORE_CATALOG)
        response = self.client.get("/moontax/staff/")
        self.assertEqual(response.status_code, 200)
        unmatched = response.context["unmatched"]
        self.assertEqual(len(unmatched), 1)
        # Must be the catalog name, not the raw numeric id.
        self.assertEqual(unmatched[0]["ores"][0]["name"], "Zeolites")
        self.assertNotEqual(unmatched[0]["ores"][0]["name"], str(ORE_CATALOG))
        self.assertNotEqual(unmatched[0]["ores"][0]["name"], ORE_CATALOG)

    def test_unknown_ore_falls_back_to_str_id(self):
        """An ore with no catalog and no EveName entry shows str(type_id)."""
        self._make_unmatched(ORE_RAW)
        response = self.client.get("/moontax/staff/")
        unmatched = response.context["unmatched"]
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["ores"][0]["name"], str(ORE_RAW))


# --------------------------------------------------------------------------------------
# Item 3 — Expected ore in _structure_rows / templates
# --------------------------------------------------------------------------------------


class ExpectedOreColumnTest(TestCase):
    """_structure_rows includes expected_ore from ore_volume_by_type."""

    def setUp(self):
        make_config()
        OreType.objects.create(type_id=ORE_CATALOG, name="Zeolites", group_id=1884)
        self.structure = make_structure(structure_id=5001, name="Drill Item3")

    def test_expected_ore_populated_from_extraction(self):
        """expected_ore is a list of {name, amount} when ore_volume_by_type is set."""
        config = Configuration.get_solo()
        # JSON keys are strings in the DB.
        ore_vol = {str(ORE_CATALOG): 100000.0, str(ORE_RAW): 50000.0}
        Extraction.objects.create(
            structure=self.structure,
            chunk_arrival_time=timezone.now() + dt.timedelta(days=3),
            ore_volume_by_type=ore_vol,
            # fracture_time is None → this is the "next extraction"
        )
        rows = _structure_rows(config)
        self.assertEqual(len(rows), 1)
        expected_ore = rows[0]["expected_ore"]
        self.assertIsInstance(expected_ore, list)
        self.assertTrue(len(expected_ore) > 0)
        # Check the catalog ore resolves to name, not id.
        names = [o["name"] for o in expected_ore]
        self.assertIn("Zeolites", names)
        self.assertNotIn(str(ORE_CATALOG), names)
        # Check raw-id fallback.
        self.assertIn(str(ORE_RAW), names)
        # Sorted by amount desc.
        amounts = [o["amount"] for o in expected_ore]
        self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_expected_ore_empty_when_no_extraction(self):
        """expected_ore is [] when the structure has no scheduled extraction."""
        config = Configuration.get_solo()
        rows = _structure_rows(config)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["expected_ore"], [])

    def test_expected_ore_empty_when_ore_volume_empty(self):
        """expected_ore is [] when ore_volume_by_type is {}."""
        config = Configuration.get_solo()
        Extraction.objects.create(
            structure=self.structure,
            chunk_arrival_time=timezone.now() + dt.timedelta(days=3),
            ore_volume_by_type={},
        )
        rows = _structure_rows(config)
        self.assertEqual(rows[0]["expected_ore"], [])

    def test_expected_ore_rendered_in_staff_template(self):
        """Staff template renders expected ore in the Structures table."""
        Extraction.objects.create(
            structure=self.structure,
            chunk_arrival_time=timezone.now() + dt.timedelta(days=3),
            ore_volume_by_type={str(ORE_CATALOG): 75000.0},
        )
        _staff_user, client = _make_staff_client("staffuser3", 80003)
        response = client.get("/moontax/staff/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Zeolites")

    def test_expected_ore_rendered_in_dashboard_template(self):
        """Dashboard template renders expected ore in the Moons table."""
        from allianceauth.authentication.models import UserProfile
        Extraction.objects.create(
            structure=self.structure,
            chunk_arrival_time=timezone.now() + dt.timedelta(days=3),
            ore_volume_by_type={str(ORE_CATALOG): 75000.0},
        )
        user = make_user("basicuser3")
        char = link_character(user, 70003, "Basic3")
        _set_main_character(user, char)
        from django.contrib.auth.models import Permission
        perm = Permission.objects.get(codename="basic_access", content_type__app_label="moontax")
        user.user_permissions.add(perm)
        user = type(user).objects.get(pk=user.pk)
        client = Client()
        client.force_login(user)
        response = client.get("/moontax/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Zeolites")


# --------------------------------------------------------------------------------------
# Item 4 — staff_mark_pop_dead view
# --------------------------------------------------------------------------------------


class MarkPopDeadTest(TestCase):
    """POST to staff_mark_pop_dead force-finalizes a non-finalized extraction."""

    def setUp(self):
        make_config(default_tax_rate="0.1")
        self.structure = make_structure(structure_id=6001, name="Drill Item4")
        OreType.objects.create(type_id=ORE_CATALOG, name="Zeolites", group_id=1884)
        # A tax rate so invoices are non-zero.
        OreTaxRate.objects.create(ore_type_id=ORE_CATALOG, ore_type_name="Zeolites", rate="0.1")
        # The miner who will owe tax.
        self.miner = make_user("miner4")
        self.miner_char = link_character(self.miner, 90004, "Miner4")
        # A future extraction (not finalized, no fracture_time).
        self.extraction = Extraction.objects.create(
            structure=self.structure,
            chunk_arrival_time=timezone.now() - dt.timedelta(days=2),
            ore_volume_by_type={str(ORE_CATALOG): 50000.0},
        )
        # Ledger row so finalize_pop produces an invoice.
        make_ledger(
            6001,
            90004,
            ORE_CATALOG,
            self.extraction.chunk_arrival_time.date(),
            10000,
        )
        self.staff_user, self.staff_client = _make_staff_client("staffuser4", 80004)

    def _url(self, extraction_id=None):
        eid = extraction_id or self.extraction.pk
        return f"/moontax/staff/pop/{eid}/mark-dead/"

    def test_sets_fracture_time_when_missing(self):
        """fracture_time is set to now if it was None."""
        before = timezone.now()
        self.staff_client.post(self._url())
        self.extraction.refresh_from_db()
        self.assertIsNotNone(self.extraction.fracture_time)
        self.assertGreaterEqual(self.extraction.fracture_time, before)

    def test_sets_fracture_type_auto(self):
        """fracture_type is set to AUTO when fracture_time was missing."""
        self.staff_client.post(self._url())
        self.extraction.refresh_from_db()
        self.assertEqual(self.extraction.fracture_type, Extraction.AUTO)

    def test_extraction_becomes_finalized(self):
        """The extraction's finalized flag is True after mark-dead."""
        self.staff_client.post(self._url())
        self.extraction.refresh_from_db()
        self.assertTrue(self.extraction.finalized)

    def test_invoice_emitted(self):
        """An Invoice is created for the miner after mark-dead."""
        self.staff_client.post(self._url())
        invoices = Invoice.objects.filter(extraction=self.extraction, user=self.miner)
        self.assertEqual(invoices.count(), 1)

    def test_redirects_to_staff(self):
        """POST redirects to moontax:staff."""
        response = self.staff_client.post(self._url())
        self.assertRedirects(response, "/moontax/staff/", fetch_redirect_response=False)

    def test_already_finalized_is_left_unchanged(self):
        """Posting to a finalized extraction does not re-finalize or duplicate."""
        # First finalize normally (sets fracture_time).
        self.extraction.fracture_time = timezone.now()
        self.extraction.fracture_type = Extraction.AUTO
        self.extraction.save()
        tax.finalize_pop(self.extraction)
        invoice_count_before = Invoice.objects.filter(extraction=self.extraction).count()
        finalized_at_before = Extraction.objects.get(pk=self.extraction.pk).finalized_at

        # Second POST — should be a no-op.
        self.staff_client.post(self._url())
        self.extraction.refresh_from_db()
        self.assertTrue(self.extraction.finalized)
        self.assertEqual(self.extraction.finalized_at, finalized_at_before)
        self.assertEqual(
            Invoice.objects.filter(extraction=self.extraction).count(), invoice_count_before
        )

    def test_non_staff_is_blocked(self):
        """A user without staff_access gets a redirect (access denied)."""
        plain_user = make_user("plainuser4")
        plain_char = link_character(plain_user, 70004, "Plain4")
        _set_main_character(plain_user, plain_char)
        perm = Permission.objects.get(codename="basic_access", content_type__app_label="moontax")
        plain_user.user_permissions.add(perm)
        plain_user = type(plain_user).objects.get(pk=plain_user.pk)
        client = Client()
        client.force_login(plain_user)

        response = client.post(self._url())
        # Should redirect away (access denied), NOT finalize.
        self.assertNotEqual(response.status_code, 200)
        self.extraction.refresh_from_db()
        self.assertFalse(self.extraction.finalized)

    def test_get_request_redirects_to_staff(self):
        """GET method (not POST) just redirects to staff page."""
        response = self.staff_client.get(self._url())
        self.assertRedirects(response, "/moontax/staff/", fetch_redirect_response=False)

    def test_does_not_overwrite_existing_fracture_time(self):
        """If fracture_time is already set, it is NOT overwritten."""
        existing_ft = timezone.now() - dt.timedelta(hours=5)
        self.extraction.fracture_time = existing_ft
        self.extraction.save(update_fields=["fracture_time", "updated_at"])

        self.staff_client.post(self._url())
        self.extraction.refresh_from_db()
        # fracture_time should stay the same (not re-set to now).
        self.assertEqual(self.extraction.fracture_time, existing_ft)

    def test_moon_pop_summary_created_after_mark_dead(self):
        """A MoonPopSummary is created after mark-dead (via finalize_pop)."""
        self.staff_client.post(self._url())
        self.assertTrue(
            MoonPopSummary.objects.filter(extraction=self.extraction).exists()
        )


# --------------------------------------------------------------------------------------
# Shared helper unit tests
# --------------------------------------------------------------------------------------


class ResolveOreNamesTest(TestCase):
    """Unit tests for tax.resolve_ore_names."""

    def setUp(self):
        OreType.objects.create(type_id=ORE_CATALOG, name="Zeolites", group_id=1884)

    def test_empty_input_returns_empty_dict(self):
        self.assertEqual(tax.resolve_ore_names([]), {})

    def test_catalog_name_returned(self):
        result = tax.resolve_ore_names([ORE_CATALOG])
        self.assertEqual(result[ORE_CATALOG], "Zeolites")

    def test_raw_id_fallback(self):
        result = tax.resolve_ore_names([ORE_RAW])
        self.assertEqual(result[ORE_RAW], str(ORE_RAW))

    def test_string_ids_cast_to_int(self):
        """JSON keys come in as strings; the helper must handle them."""
        result = tax.resolve_ore_names([str(ORE_CATALOG)])
        self.assertIn(ORE_CATALOG, result)
        self.assertEqual(result[ORE_CATALOG], "Zeolites")

    def test_catalog_wins_over_evename(self):
        """OreType catalog takes priority over EveName."""
        from moontax.models import EveName
        EveName.objects.create(eve_id=ORE_CATALOG, name="ShouldNotWin", category=EveName.ORE)
        result = tax.resolve_ore_names([ORE_CATALOG])
        self.assertEqual(result[ORE_CATALOG], "Zeolites")
