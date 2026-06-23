"""Compressed-ore payment equivalence (Requirements §6).

Moon ore compresses at a 1 raw unit : 1 compressed unit ratio (no refinery involved;
the game re-stacks the ore in place). A tax line owing N raw units may be paid with N
raw units of that ore, N compressed units, or any mix where the total raw-equivalent
meets or exceeds the debt.
"""

from __future__ import annotations

from moontax.ores import COMPRESSION_RATIO


def compressed_units(owed_raw: int, ratio: int = COMPRESSION_RATIO) -> int:
    """Compressed units equivalent to a raw debt (the "or pay this" amount).

    At the default 1:1 ratio every unit owed maps to exactly one compressed unit.
    Returns 0 only when ``owed_raw`` is 0.
    """
    return owed_raw // ratio


def line_satisfied(
    owed_raw: int,
    offered_raw: int,
    offered_compressed: int,
    ratio: int = COMPRESSION_RATIO,
) -> bool:
    """Whether one ore line is covered by the offered raw + compressed units.

    Satisfied when ``offered_raw + ratio * offered_compressed >= owed_raw``.
    At the default 1:1 ratio each compressed unit is worth exactly one raw unit,
    so any mix of raw and compressed summing to at least the owed amount is accepted.
    """
    return offered_compressed * ratio + offered_raw >= owed_raw
