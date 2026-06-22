"""Regression: ESI results are pydantic models, not dicts (use attribute access)."""

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from moontax import providers


class AccessorTest(SimpleTestCase):
    def test_g_reads_pydantic_style_object(self):
        obj = SimpleNamespace(corporation_id=2001, name="Director Bob")
        self.assertEqual(providers._g(obj, "corporation_id"), 2001)
        self.assertEqual(providers._g(obj, "name"), "Director Bob")
        self.assertEqual(providers._g(obj, "missing", "d"), "d")

    def test_g_reads_dict(self):
        self.assertEqual(providers._g({"name": "x"}, "name"), "x")
        self.assertEqual(providers._g({}, "name", "d"), "d")


class ValidateTokenTest(SimpleTestCase):
    def test_validate_token_handles_model_objects(self):
        token = SimpleNamespace(character_id=90001)
        char = SimpleNamespace(corporation_id=2001, name="Director Bob")
        corp = SimpleNamespace(name="Corp", ceo_id=90001)
        with mock.patch("moontax.providers.character_info", return_value=char), \
             mock.patch("moontax.providers.corporation_info", return_value=corp), \
             mock.patch("moontax.providers.corp_structures", return_value=[]):
            result = providers.validate_token(token, expected_corporation_id=2001)
        self.assertTrue(result.ok)
        self.assertEqual(result.corporation_id, 2001)
        self.assertEqual(result.character_name, "Director Bob")
        self.assertTrue(result.is_ceo)

    def test_validate_token_rejects_wrong_corp(self):
        token = SimpleNamespace(character_id=90001)
        char = SimpleNamespace(corporation_id=9999, name="Bob")
        with mock.patch("moontax.providers.character_info", return_value=char):
            result = providers.validate_token(token, expected_corporation_id=2001)
        self.assertFalse(result.ok)

    def test_validate_token_payment_role_uses_corp_contracts(self):
        """Payment role: decisive check is corp_contracts, not corp_structures."""
        token = SimpleNamespace(character_id=90001)
        char = SimpleNamespace(corporation_id=2001, name="Director Bob")
        corp = SimpleNamespace(name="Corp", ceo_id=90001)
        with mock.patch("moontax.providers.character_info", return_value=char), \
             mock.patch("moontax.providers.corporation_info", return_value=corp), \
             mock.patch("moontax.providers.corp_contracts", return_value=[]) as mock_contracts:
            result = providers.validate_token(
                token, expected_corporation_id=2001, role="payment"
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.corporation_id, 2001)
        mock_contracts.assert_called_once()

    def test_validate_token_payment_role_fails_on_403(self):
        """Payment role: a 403-like error from corp_contracts returns ok=False."""
        token = SimpleNamespace(character_id=90001)
        char = SimpleNamespace(corporation_id=2001, name="Director Bob")
        corp = SimpleNamespace(name="Corp", ceo_id=90001)
        forbidden = Exception("403 Forbidden")
        with mock.patch("moontax.providers.character_info", return_value=char), \
             mock.patch("moontax.providers.corporation_info", return_value=corp), \
             mock.patch("moontax.providers.corp_contracts", side_effect=forbidden):
            result = providers.validate_token(
                token, expected_corporation_id=2001, role="payment"
            )
        self.assertFalse(result.ok)
