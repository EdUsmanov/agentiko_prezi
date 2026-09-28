"""Canonical typography for numeric comparison; never rewrite source evidence."""

import re
import math


_GROUPED_NUMBER = re.compile(r"(?<![\d.,])\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?:[.,]\d+)?(?!\d)")
_CELL = re.compile(
    r"\s*([−\-+]?(?:\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+|\d+)(?:[.,]\d+)?)"
    r"\s*(%|₽|руб\.?|млн|тыс\.?)?\s*"
)


def normalize_numeric_typography(text):
    """Unify thousands spacing and minus glyphs, retaining signs and units.

    Only exact groups of three digits are joined. Dates, ranges, decimal
    separators and arbitrary adjacent numbers are not converted.
    """
    return _GROUPED_NUMBER.sub(lambda m: re.sub(r"\s", "", m[0]), text).replace("−", "-")


def numeric_cell(text):
    """Parse a finite cell value and unit; ambiguous grouping stays textual."""
    match = _CELL.fullmatch(text)
    if not match:
        return None
    value = float(normalize_numeric_typography(match[1]).replace(",", "."))
    return (value, match[2] or "") if math.isfinite(value) else None
