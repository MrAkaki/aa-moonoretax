"""Ledger upsert must overwrite (never double-count across polls)."""

import datetime as dt

from django.test import TestCase

from moontax.models import MiningLedger

UTC = dt.timezone.utc
KEY = dict(observer_id=1001, character_id=90001, ore_type_id=46300, recorded_date=dt.date(2026, 1, 10))


class LedgerUpsertTest(TestCase):
    def test_second_poll_overwrites_not_sums(self):
        MiningLedger.objects.upsert_row(quantity=1000, **KEY)
        MiningLedger.objects.upsert_row(quantity=1500, **KEY)  # cumulative-to-date
        rows = MiningLedger.objects.filter(**KEY)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().quantity, 1500)

    def test_distinct_dates_are_distinct_rows(self):
        MiningLedger.objects.upsert_row(quantity=1000, **KEY)
        other = dict(KEY, recorded_date=dt.date(2026, 1, 11))
        MiningLedger.objects.upsert_row(quantity=200, **other)
        self.assertEqual(MiningLedger.objects.count(), 2)
