"""Regression: poll_notifications must process each ESI notification once.

ESI's notifications endpoint returns a rolling window of recent notifications, so
the same MoonminingExtractionStarted comes back on every hourly poll. Without a
process-once guard *before* applying, the moon-pop ping (notify_moon_pop) was
re-sent every hour. These tests pin the guard in place.
"""

import datetime as dt
from unittest import mock

from django.test import TestCase

from moontax import tasks
from moontax.core.notifications_parse import STARTED
from moontax.core.timeutils import _EPOCH_DIFF
from moontax.models import Extraction, ProcessedNotification
from moontax.tests.helpers import make_config, make_structure

UTC = dt.timezone.utc
STRUCTURE_ID = 1001
NOTE_ID = 9001


def _ticks(when: dt.datetime) -> int:
    return int((when.timestamp() + _EPOCH_DIFF) * 10_000_000)


def _started_note(note_id=NOTE_ID, structure_id=STRUCTURE_ID):
    ready = dt.datetime(2026, 1, 10, tzinfo=UTC)
    auto = dt.datetime(2026, 1, 12, tzinfo=UTC)
    text = (
        f"structureID: {structure_id}\n"
        f"moonID: 4001\n"
        f"readyTime: {_ticks(ready)}\n"
        f"autoTime: {_ticks(auto)}\n"
        f"oreVolumeByType:\n"
        f"  46300: 1000.0\n"
    )
    return {
        "notification_id": note_id,
        "type": STARTED,
        "timestamp": dt.datetime(2026, 1, 9, 12, 0, tzinfo=UTC),
        "text": text,
    }


class PollNotificationsDedupTest(TestCase):
    def setUp(self):
        make_config(mining_corporation_id=2001, payment_corporation_id=2002)
        make_structure(structure_id=STRUCTURE_ID)

    def _poll(self, notes):
        """Run poll_notifications with collection mocked; return the moon-pop mock."""
        _mining_token = object()
        _payment_token = object()
        with mock.patch.object(
            tasks.providers, "get_mining_token", return_value=_mining_token
        ), mock.patch.object(
            tasks.providers, "get_payment_token", return_value=_payment_token
        ), mock.patch.object(
            tasks.providers, "character_notifications", return_value=notes
        ), mock.patch.object(
            tasks, "finalize_pops"
        ), mock.patch(
            "moontax.notifications.notify_moon_pop"
        ) as notify:
            tasks.poll_notifications()
        return notify

    def test_started_applied_and_pinged_once(self):
        notes = [_started_note()]

        first = self._poll(notes)
        self.assertEqual(first.call_count, 1)
        self.assertEqual(Extraction.objects.count(), 1)
        self.assertTrue(ProcessedNotification.objects.filter(notification_id=NOTE_ID).exists())

        # Same notification returns from ESI again next hour — must NOT re-ping.
        second = self._poll(notes)
        self.assertEqual(second.call_count, 0)
        self.assertEqual(Extraction.objects.count(), 1)

    def test_unclaimed_notification_is_retried(self):
        """A STARTED for an unknown structure stays unclaimed and is retried."""
        note = _started_note(structure_id=999999)  # no such Structure

        first = self._poll([note])
        # Deferred: not applied, not pinged, not recorded as processed.
        self.assertEqual(first.call_count, 0)
        self.assertFalse(ProcessedNotification.objects.filter(notification_id=NOTE_ID).exists())

        # Structure shows up; the retry now applies and pings exactly once.
        make_structure(structure_id=999999, name="LateDrill")
        second = self._poll([note])
        self.assertEqual(second.call_count, 1)
        self.assertTrue(ProcessedNotification.objects.filter(notification_id=NOTE_ID).exists())
