"""Pure tests for FILETIME→UTC (no Django needed)."""

import datetime as dt
import unittest

from moontax.core.timeutils import _EPOCH_DIFF, ldap_to_datetime


class FiletimeTest(unittest.TestCase):
    def _ticks(self, when: dt.datetime) -> int:
        return int((when.timestamp() + _EPOCH_DIFF) * 10_000_000)

    def test_known_epoch_roundtrip(self):
        target = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
        self.assertEqual(ldap_to_datetime(self._ticks(target)), target)

    def test_is_utc_aware(self):
        got = ldap_to_datetime(self._ticks(dt.datetime(2025, 6, 19, 12, 30, tzinfo=dt.timezone.utc)))
        self.assertIsNotNone(got.tzinfo)
        self.assertEqual(got.utcoffset(), dt.timedelta(0))

    def test_accepts_string_input(self):
        target = dt.datetime(2030, 3, 3, 3, 3, 3, tzinfo=dt.timezone.utc)
        self.assertEqual(ldap_to_datetime(str(self._ticks(target))), target)


if __name__ == "__main__":
    unittest.main()
