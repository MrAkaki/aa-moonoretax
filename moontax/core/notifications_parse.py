"""Pure parsers for corp moon-mining notification bodies (YAML + FILETIME).

Kept side-effect free so they are trivially unit-testable. ESI delivers the body as a
YAML string in the notification's ``text`` field; embedded timestamps are FILETIME.
"""

from __future__ import annotations

import yaml

from moontax.core.timeutils import ldap_to_datetime

STARTED = "MoonminingExtractionStarted"
LASER_FIRED = "MoonminingLaserFired"
AUTO_FRACTURE = "MoonminingAutomaticFracture"

FRACTURE_TYPES = {LASER_FIRED, AUTO_FRACTURE}


def parse_extraction_started(text: str) -> dict:
    """Parse ``MoonminingExtractionStarted``.

    Returns ``structure_id``, ``moon_id``, ``chunk_arrival_time`` (readyTime),
    ``auto_fracture_time`` (autoTime), and ``ore_volume_by_type`` ({type_id: volume}).
    """
    data = yaml.safe_load(text) or {}
    ore = {
        int(k): float(v) for k, v in (data.get("oreVolumeByType") or {}).items()
    }
    return {
        "structure_id": data.get("structureID"),
        "moon_id": data.get("moonID"),
        "chunk_arrival_time": (
            ldap_to_datetime(data["readyTime"]) if data.get("readyTime") else None
        ),
        "auto_fracture_time": (
            ldap_to_datetime(data["autoTime"]) if data.get("autoTime") else None
        ),
        "ore_volume_by_type": ore,
    }


def parse_fracture(text: str) -> dict:
    """Parse a ``MoonminingLaserFired`` / ``MoonminingAutomaticFracture`` body.

    These carry the structure (and usually the moon); the **pop time** is the
    notification's own ``timestamp`` (handled by the caller), not a body field.
    """
    data = yaml.safe_load(text) or {}
    return {
        "structure_id": data.get("structureID"),
        "moon_id": data.get("moonID"),
    }
