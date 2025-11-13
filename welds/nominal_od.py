"""Utilities for mapping actual outer diameters to nominal pipe sizes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)

THREE_DECIMAL = Decimal("0.001")
MM_PER_INCH = Decimal("25.4")

# Template of standard nominal pipe size labels mapped to their actual ODs in inches.
# Values follow the ASME B36.10M table for common line pipe sizes.
_STANDARD_NOMINAL_PIPE_DATA: Sequence[tuple[str, str]] = (
    ("1/8\"", "0.405"),
    ("1/4\"", "0.540"),
    ("3/8\"", "0.675"),
    ("1/2\"", "0.840"),
    ("3/4\"", "1.050"),
    ("1\"", "1.315"),
    ("1-1/4\"", "1.660"),
    ("1-1/2\"", "1.900"),
    ("2\"", "2.375"),
    ("2-1/2\"", "2.875"),
    ("3\"", "3.500"),
    ("3-1/2\"", "4.000"),
    ("4\"", "4.500"),
    ("5\"", "5.563"),
    ("6\"", "6.625"),
    ("8\"", "8.625"),
    ("10\"", "10.750"),
    ("12\"", "12.750"),
    ("14\"", "14.000"),
    ("16\"", "16.000"),
    ("18\"", "18.000"),
    ("20\"", "20.000"),
    ("22\"", "22.000"),
    ("24\"", "24.000"),
    ("26\"", "26.000"),
    ("28\"", "28.000"),
    ("30\"", "30.000"),
    ("32\"", "32.000"),
    ("34\"", "34.000"),
    ("36\"", "36.000"),
    ("38\"", "38.000"),
    ("40\"", "40.000"),
    ("42\"", "42.000"),
    ("44\"", "44.000"),
    ("46\"", "46.000"),
    ("48\"", "48.000"),
)


@dataclass(frozen=True)
class NominalPipeSize:
    """Denormalized representation of a nominal pipe size."""

    label: str
    actual_od: Decimal
    tolerance: Decimal


def _get_default_tolerance() -> Decimal:
    """Read the configured matching tolerance from Django settings."""

    try:
        from django.conf import settings
    except Exception:  # pragma: no cover - settings should be available
        return Decimal("0.010")

    value = getattr(settings, "NOMINAL_OD_MATCH_TOLERANCE_IN", Decimal("0.010"))
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        logger.warning(
            "Invalid NOMINAL_OD_MATCH_TOLERANCE_IN setting; falling back to 0.010", 
            extra={"value": value},
        )
        return Decimal("0.010")


def get_standard_nominal_pipe_sizes(tolerance: Decimal | None = None) -> tuple[NominalPipeSize, ...]:
    """Return the standard nominal pipe sizes with the provided tolerance."""

    tolerance = tolerance or _get_default_tolerance()
    return tuple(
        NominalPipeSize(
            label=label,
            actual_od=Decimal(actual).quantize(THREE_DECIMAL, rounding=ROUND_HALF_UP),
            tolerance=tolerance,
        )
        for label, actual in _STANDARD_NOMINAL_PIPE_DATA
    )


def build_nominal_pipe_sizes(
    overrides: Iterable[object] | None,
    *,
    tolerance: Decimal | None = None,
) -> tuple[NominalPipeSize, ...]:
    """Merge configured overrides with the standard mapping.

    Overrides that cannot be parsed are ignored to avoid breaking automatic
    matching. When overrides collide on actual OD they replace the standard
    entry so that organizations can customise labels or tolerances for
    specific sizes.
    """

    default_tolerance = tolerance or _get_default_tolerance()
    base_map: dict[Decimal, NominalPipeSize] = {
        size.actual_od: size for size in get_standard_nominal_pipe_sizes(default_tolerance)
    }
    if not overrides:
        return tuple(base_map[actual] for actual in sorted(base_map))

    for override in overrides:
        try:
            label = str(override.label)
        except AttributeError:
            continue
        try:
            actual = Decimal(override.actual_od)
        except (AttributeError, InvalidOperation, TypeError, ValueError):
            continue
        normalized_actual = actual.quantize(THREE_DECIMAL, rounding=ROUND_HALF_UP)
        try:
            tolerance_value = Decimal(getattr(override, "tolerance", default_tolerance))
        except (InvalidOperation, TypeError, ValueError):
            tolerance_value = default_tolerance
        base_map[normalized_actual] = NominalPipeSize(
            label=label,
            actual_od=normalized_actual,
            tolerance=tolerance_value,
        )
    return tuple(base_map[actual] for actual in sorted(base_map))


def normalize_actual_od(value) -> Decimal | None:
    """Normalize an actual OD to inches with three-decimal precision."""

    if value is None:
        return None
    try:
        actual = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if actual <= 0:
        return None
    # Values that are unrealistically large for inches are assumed to be mm.
    if actual > Decimal("40"):
        actual = actual / MM_PER_INCH
    return actual.quantize(THREE_DECIMAL, rounding=ROUND_HALF_UP)


def match_nominal_pipe_size(
    actual_value,
    pipe_sizes: Sequence[NominalPipeSize],
    *,
    tolerance: Decimal | None = None,
) -> NominalPipeSize | None:
    """Return the best nominal pipe size for the supplied actual OD.

    The function first searches for an exact match (after normalization) and
    then falls back to the nearest match that is within the configured
    tolerance. Ties prefer the larger nominal size which is consistent with
    how mismatched measurements are typically rounded in the field.
    """

    normalized_actual = normalize_actual_od(actual_value)
    if normalized_actual is None:
        return None

    # Try exact match first
    for size in pipe_sizes:
        if normalized_actual == size.actual_od:
            return size

    match_tolerance = tolerance or _get_default_tolerance()
    best: tuple[Decimal, NominalPipeSize] | None = None
    for size in pipe_sizes:
        size_tolerance = tolerance or size.tolerance or match_tolerance
        diff = abs(normalized_actual - size.actual_od)
        if diff <= size_tolerance:
            if best is None:
                best = (diff, size)
            else:
                best_diff, best_size = best
                if diff < best_diff or (
                    diff == best_diff and size.actual_od > best_size.actual_od
                ):
                    best = (diff, size)
    if best:
        return best[1]
    return None
