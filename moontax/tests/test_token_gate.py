"""Tests for the two-token gate (_require_both_ready) and "block everything" behaviour.

When either the mining or payment token is missing/broken, ALL ESI-collection tasks
must bail out, mark that role's TokenConfig invalid, and call notify_token_broken.
Both tokens must be present for any task to proceed.

These tests mock at the ``tasks.providers.*`` and ``moontax.notifications.*``
boundaries only — no real ESI calls are made.
"""

from unittest import mock

from django.test import TestCase

from moontax import tasks
from moontax.models import Configuration, TokenConfig
from moontax.tests.helpers import make_config, make_structure


class TokenGatePaymentBrokenTest(TestCase):
    """Broken payment token halts mining-collection tasks ("block everything")."""

    def setUp(self):
        make_config(mining_corporation_id=2001, payment_corporation_id=2002)
        make_structure(structure_id=1001)
        # Create a valid payment TokenConfig row so _mark_token_broken can flip it.
        TokenConfig.objects.create(
            role=TokenConfig.PAYMENT,
            corporation_id=2002,
            is_valid=True,
        )

    def test_broken_payment_token_halts_update_structures(self):
        """update_structures() bails when payment token is None.

        Specifically:
        - providers.corp_structures must NOT be called (no mining ESI call made).
        - The PAYMENT TokenConfig row must become is_valid=False.
        - notifications.notify_token_broken must be called.
        """
        mining_token = object()
        with mock.patch.object(
            tasks.providers, "get_mining_token", return_value=mining_token
        ), mock.patch.object(
            tasks.providers, "get_payment_token", return_value=None
        ), mock.patch.object(
            tasks.providers, "corp_structures"
        ) as mock_structures, mock.patch(
            "moontax.notifications.notify_token_broken"
        ) as mock_notify:
            tasks.update_structures()

        # Mining ESI call must NOT have been made.
        mock_structures.assert_not_called()

        # Payment TokenConfig must have been marked invalid.
        tc = TokenConfig.objects.filter(role=TokenConfig.PAYMENT).first()
        self.assertIsNotNone(tc)
        self.assertFalse(tc.is_valid)

        # Admin notification must have been sent.
        mock_notify.assert_called_once()


class TokenGateMiningBrokenTest(TestCase):
    """Broken mining token halts payment-collection tasks ("block everything")."""

    def setUp(self):
        make_config(mining_corporation_id=2001, payment_corporation_id=2002)
        # Create a valid mining TokenConfig row so _mark_token_broken can flip it.
        TokenConfig.objects.create(
            role=TokenConfig.MINING,
            corporation_id=2001,
            is_valid=True,
        )

    def test_broken_mining_token_halts_update_contracts(self):
        """update_contracts() bails when mining token is None.

        Specifically:
        - providers.corp_contracts must NOT be called (no payment ESI call made).
        - The MINING TokenConfig row must become is_valid=False.
        - notifications.notify_token_broken must be called.
        """
        payment_token = object()
        with mock.patch.object(
            tasks.providers, "get_mining_token", return_value=None
        ), mock.patch.object(
            tasks.providers, "get_payment_token", return_value=payment_token
        ), mock.patch.object(
            tasks.providers, "corp_contracts"
        ) as mock_contracts, mock.patch(
            "moontax.notifications.notify_token_broken"
        ) as mock_notify:
            tasks.update_contracts()

        # Payment ESI call must NOT have been made.
        mock_contracts.assert_not_called()

        # Mining TokenConfig must have been marked invalid.
        tc = TokenConfig.objects.filter(role=TokenConfig.MINING).first()
        self.assertIsNotNone(tc)
        self.assertFalse(tc.is_valid)

        # Admin notification must have been sent.
        mock_notify.assert_called_once()


class RequireBothReadyTest(TestCase):
    """_require_both_ready() returns the correct 4-tuple when both tokens are present."""

    def setUp(self):
        make_config(mining_corporation_id=2001, payment_corporation_id=2002)
        TokenConfig.objects.create(
            role=TokenConfig.MINING,
            corporation_id=2001,
            is_valid=True,
        )
        TokenConfig.objects.create(
            role=TokenConfig.PAYMENT,
            corporation_id=2002,
            is_valid=True,
        )

    def test_returns_4_tuple_with_correct_corp_ids(self):
        """Both tokens ready: _require_both_ready returns (m_tok, m_id, p_tok, p_id)."""
        mining_sentinel = object()
        payment_sentinel = object()
        with mock.patch.object(
            tasks.providers, "get_mining_token", return_value=mining_sentinel
        ), mock.patch.object(
            tasks.providers, "get_payment_token", return_value=payment_sentinel
        ):
            result = tasks._require_both_ready()

        self.assertIsNotNone(result)
        m_token, m_corp_id, p_token, p_corp_id = result
        self.assertIs(m_token, mining_sentinel)
        self.assertEqual(m_corp_id, 2001)
        self.assertIs(p_token, payment_sentinel)
        self.assertEqual(p_corp_id, 2002)
