"""Match a requested face to binary metadata, never to an OOXML slot weight."""
from io import BytesIO
from functools import lru_cache
from pathlib import Path
import re
from fontTools.ttLib import TTFont
from .fonts import font_key

WEIGHTS = {"thin":100,"extralight":200,"ultralight":200,"light":300,
           "regular":400,"normal":400,"book":400,"roman":400,"medium":500,
           "semibold":600,"demibold":600,"bold":700,"extrabold":800,
           "ultrabold":800,"black":900,"heavy":900}
SUFFIX = re.compile(r"(?:^|[\s_-])((?:extra|ultra)[ -]?(?:light|bold)|(?:semi|demi)[ -]?bold|"
                    r"thin|light|regular|normal|book|roman|medium|bold|black|heavy|italic|oblique)$",re.I)


def requested_face(name):
    family=name.strip(); weight=None; italic=False
    while (match:=SUFFIX.search(family)) and match.start()>0:
        token=re.sub(r"[\s_-]","",match[1]).lower()
        if token in ("italic","oblique"):
            italic=True
        elif weight is None:
            weight=WEIGHTS[token]
        family=family[:match.start()].strip()
    return family, weight or 400, italic


def binary_identity(raw):
    with TTFont(BytesIO(raw),lazy=False) as font:
        names=font["name"]
        return {"family":names.getBestFamilyName(),"style":names.getBestSubFamilyName(),
                "weight":font["OS/2"].usWeightClass,
                "italic":bool(font["OS/2"].fsSelection & 1 or font["head"].macStyle & 2)}


def matches_face(raw, requested):
    family,weight,italic=requested_face(requested)
    actual=binary_identity(raw)
    actual_family=requested_face(actual["family"])[0]
    return (font_key(actual_family)==font_key(family) and actual["weight"]==weight
            and actual["italic"]==italic)


def ooxml_face(path):
    """Office uses the legacy family (name ID 1) plus separate B/I flags.

    A full face such as 'Calibri Bold' is not a family. Using it as one can
    silently select an unrelated fallback. ID 16 is also unsuitable here:
    it would collapse 'Calibri Light' or 'Aptos Display' to another face.
    """
    stat = Path(path).stat()
    return _ooxml_face(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=128)
def _ooxml_face(path, stamp, size):
    with TTFont(path, lazy=True) as font:
        names = font['name']
        family = names.getDebugName(1) or names.getBestFamilyName()
        if not family:
            raise ValueError('В шрифте отсутствует название семейства Office')
        flags = font['head'].macStyle
        return family, bool(flags & 1), bool(flags & 2)


def apply_ooxml_font(target, path, bold=False):
    family, face_bold, italic = ooxml_face(path)
    target.name = family
    target.bold = bool(bold or face_bold)
    target.italic = italic
