"""Compressed-ore payment equivalence (Requirements §6).

Moon ore compresses at a fixed ratio (``ores.COMPRESSION_RATIO`` = 100 raw units : 1
compressed unit). A tax line owing raw ore may be paid in the mined (raw) ore, in the
compressed equivalent, or any mix of both — each compressed unit settles ``ratio`` raw
units of the debt.

The compressed amount is **rounded down**: a line owing ``N`` raw units rounds to
``N // ratio`` whole compressed units, and the sub-``ratio`` remainder (``N % ratio``) is
forgiven once the whole-``ratio`` part is covered in compressed (operator policy). A line
owing fewer than ``ratio`` units has no compressed option and must be paid in raw — this
keeps small lines from being forgiven outright.
"""

from __future__ import annotations

from moontax.ores import COMPRESSION_RATIO


def compressed_units(owed_raw: int, ratio: int = COMPRESSION_RATIO) -> int:
    """Whole compressed units a raw debt rounds down to (the "or pay this" amount)."""
    return owed_raw // ratio


def line_satisfied(
    owed_raw: int,
    offered_raw: int,
    offered_compressed: int,
    ratio: int = COMPRESSION_RATIO,
) -> bool:
    """Whether one ore line is covered by the offered raw + compressed units.

    Satisfied when **either** the offered raw-equivalent (``raw + ratio*compressed``)
    meets the debt in full, **or** the whole-``ratio`` part of the debt is covered by
    compressed units (``compressed >= owed // ratio``, with at least one whole unit owed),
    in which case the sub-``ratio`` remainder is forgiven.
    """
    if offered_compressed * ratio + offered_raw >= owed_raw:
        return True
    whole = owed_raw // ratio
    return whole >= 1 and offered_compressed >= whole
