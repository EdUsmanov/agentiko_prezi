"""Normalize font families whose style is encoded in the family label."""

import re

STYLE_SUFFIX = re.compile(
    r"(?:\s+(?:normal|extra\s*light|ultra\s*light|semi\s*bold|demi\s*bold|"
    r"extra\s*bold|ultra\s*bold|thin|light|regular|medium|bold|black|heavy|italic)"
    r"(?:\s*italic)?)+$",
    re.IGNORECASE,
)


def base_font_family(family: str) -> str:
    return STYLE_SUFFIX.sub("", family).strip() or family
