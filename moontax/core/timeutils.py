"""Time helpers, including the EVE-notification FILETIME→UTC conversion."""

from __future__ import annotations

import datetime as dt

# EVE notification body timestamps are LDAP/Windows FILETIME: 100-ns ticks since
# 1601-01-01. _EPOCH_DIFF is the seconds between 1601-01-01 and the Unix epoch.
_EPOCH_DIFF = 11644473600


def eve_now() -> dt.datetime:
    """Timezone-aware current UTC time."""
    return dt.datetime.now(tz=dt.timezone.utc)


def ldap_to_datetime(value) -> dt.datetime:
    """Convert an LDAP/FILETIME integer to a whole-second, UTC-aware ``datetime``."""
    seconds = round(int(value) / 10_000_000 - _EPOCH_DIFF)
    return dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc)
