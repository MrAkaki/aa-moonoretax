"""Reminder cadence: every-other-day, then daily after 7 days (Requirements §7)."""

import datetime as dt
from types import SimpleNamespace

from django.test import SimpleTestCase

from moontax.notifications import _reminder_due

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 2, 1, 12, 0, tzinfo=UTC)


def _inv(age_days, last_reminder_days_ago=None):
    emitted = NOW - dt.timedelta(days=age_days)
    last = None if last_reminder_days_ago is None else NOW - dt.timedelta(days=last_reminder_days_ago)
    return SimpleNamespace(emitted_at=emitted, last_reminder_at=last)


class ReminderCadenceTest(SimpleTestCase):
    EVERY, DAILY_AFTER = 2, 7

    def _due(self, inv):
        return _reminder_due(inv, NOW, self.EVERY, self.DAILY_AFTER)

    def test_day0_no_reminder(self):
        self.assertFalse(self._due(_inv(0)))

    def test_day1_not_yet(self):
        self.assertFalse(self._due(_inv(1)))

    def test_day2_first_reminder(self):
        self.assertTrue(self._due(_inv(2)))

    def test_every_other_day_paced_by_last(self):
        # 4 days old but reminded yesterday -> not due (cadence 2 while young)
        self.assertFalse(self._due(_inv(4, last_reminder_days_ago=1)))
        # 4 days old, reminded 2 days ago -> due
        self.assertTrue(self._due(_inv(4, last_reminder_days_ago=2)))

    def test_daily_after_seven_days(self):
        # 8 days old, reminded 1 day ago -> daily cadence -> due
        self.assertTrue(self._due(_inv(8, last_reminder_days_ago=1)))
        # 8 days old, reminded 12 hours ago -> not yet a full day
        inv = SimpleNamespace(
            emitted_at=NOW - dt.timedelta(days=8),
            last_reminder_at=NOW - dt.timedelta(hours=12),
        )
        self.assertFalse(self._due(inv))
