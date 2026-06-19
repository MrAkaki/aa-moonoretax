"""Pure tests for the moon-notification YAML parsers (no Django needed)."""

import datetime as dt
import unittest

from moontax.core.notifications_parse import (
    parse_extraction_started,
    parse_fracture,
)
from moontax.core.timeutils import _EPOCH_DIFF


def _ticks(when: dt.datetime) -> int:
    return int((when.timestamp() + _EPOCH_DIFF) * 10_000_000)


class ExtractionStartedTest(unittest.TestCase):
    def test_parses_fields_and_filetime(self):
        ready = dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc)
        auto = dt.datetime(2026, 1, 12, tzinfo=dt.timezone.utc)
        text = (
            f"structureID: 1001\n"
            f"moonID: 4001\n"
            f"readyTime: {_ticks(ready)}\n"
            f"autoTime: {_ticks(auto)}\n"
            f"oreVolumeByType:\n"
            f"  46300: 1000.0\n"
            f"  46301: 500.0\n"
        )
        parsed = parse_extraction_started(text)
        self.assertEqual(parsed["structure_id"], 1001)
        self.assertEqual(parsed["moon_id"], 4001)
        self.assertEqual(parsed["chunk_arrival_time"], ready)
        self.assertEqual(parsed["auto_fracture_time"], auto)
        self.assertEqual(parsed["ore_volume_by_type"], {46300: 1000.0, 46301: 500.0})

    def test_empty_body(self):
        parsed = parse_extraction_started("")
        self.assertIsNone(parsed["chunk_arrival_time"])
        self.assertEqual(parsed["ore_volume_by_type"], {})


class FractureTest(unittest.TestCase):
    def test_parses_structure(self):
        parsed = parse_fracture("structureID: 1001\nmoonID: 4001\n")
        self.assertEqual(parsed["structure_id"], 1001)
        self.assertEqual(parsed["moon_id"], 4001)


if __name__ == "__main__":
    unittest.main()
