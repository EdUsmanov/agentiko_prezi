"""Document-scoped font extraction; no installation or global registration.

Supports raw TrueType and uncompressed EOT. Compressed/protected EOT fails
closed. References and font names never become filesystem paths or URLs.
"""
from io import BytesIO
from pathlib import PurePosixPath
import posixpath
import struct
from zipfile import ZipFile
from defusedxml import ElementTree as ET
from fontTools.ttLib import TTFont
from .fonts import font_key
from .security import digest, InputRejected

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MAX_FONT_BYTES = 16 * 1024 * 1024


def unpack_font(raw):
    if len(raw) > MAX_FONT_BYTES:
        raise ValueError("встроенный шрифт превышает 16 МБ")
    if raw.startswith(b"\x00\x01\x00\x00"):
        return raw, "TrueType"
    if len(raw) < 82 or struct.unpack_from("<H", raw, 34)[0] != 0x504C:
        raise ValueError("формат встроенного шрифта не поддерживается")
    total, size, version, flags = struct.unpack_from("<4I", raw)
    if total != len(raw) or not 12 <= size <= len(raw)-82:
        raise ValueError("повреждённый контейнер EOT")
    if flags & (4 | 0x20 | 0x10000000):
        raise ValueError("сжатый MTX, EUDC или защищённый EOT пока не поддерживается")
    if struct.unpack_from("<H", raw, 32)[0]:
        raise ValueError("ограничения встраивания EOT не допускают текущий набор экспортов")
    end = len(raw)-size
    offset = 80
    # Four variable-length strings, each preceded by padding and byte count.
    for _ in range(4):
        if offset+4 > end:
            raise ValueError("повреждённые строки EOT")
        padding, length = struct.unpack_from("<2H", raw, offset)
        if padding or length % 2 or offset+4+length > end:
            raise ValueError("повреждённые строки EOT")
        offset += 4+length
    if version in (0x20001, 0x20002):
        if offset+4 > end:
            raise ValueError("повреждённый RootString EOT")
        padding, root_length = struct.unpack_from("<2H", raw, offset)
        if padding or root_length:
            raise ValueError("EOT с ограничением домена не используется для нового документа")
        offset += 4
    elif version != 0x10000:
        raise ValueError("неподдерживаемая версия EOT")
    if version == 0x20002:
        # Empty RootString checksum, EUDC codepage, padding, empty signature,
        # EUDC flags and size. Extended data is deliberately unsupported.
        if offset+20 != end:
            raise ValueError("расширенные данные EOT пока не поддерживаются")
        checksum, codepage, padding, signature, eudc_flags, eudc_size = struct.unpack_from("<IIHHII", raw, offset)
        if checksum != 0x50475342 or any((codepage,padding,signature,eudc_flags,eudc_size)):
            raise ValueError("ограничения или расширенные данные EOT не поддерживаются")
        offset += 20
    if offset != end or raw[end:end+4] != b"\x00\x01\x00\x00":
        raise ValueError("некорректная граница или неподдерживаемые контуры EOT")
    return raw[end:], "EOT"


def inspect_font(raw):
    with TTFont(BytesIO(raw), lazy=False) as font:
        if "glyf" not in font or "fvar" in font:
            raise ValueError("нужен статический TrueType-шрифт")
        if "OS/2" not in font or font["OS/2"].fsType:
            raise ValueError("ограничения встраивания не допускают текущий набор экспортов")
        if len(font.getGlyphOrder()) > 65535:
            raise ValueError("слишком много глифов")
        names = font["name"]
        aliases = {font_key(n.toUnicode()) for n in names.names if n.nameID in (4,6)}
        family = names.getBestFamilyName()
        style = names.getBestSubFamilyName()
        aliases.add(font_key(family+" "+style))
        if style.casefold() in ("regular","normal","book","roman"):
            aliases.add(font_key(family))
        return aliases, set((font.getBestCmap() or {}).keys())


def extract_embedded_font(pptx, directory, requested):
    issues = []
    with ZipFile(pptx) as z:
        root = ET.fromstring(z.read("ppt/presentation.xml"))
        font_list = root.find(P+"embeddedFontLst")
        if font_list is None:
            return "", {"kind":"not_embedded"}, issues
        if len(font_list) > 128:
            raise InputRejected("Слишком много встроенных шрифтов")
        rels = {r.get("Id"):r for r in ET.fromstring(z.read("ppt/_rels/presentation.xml.rels"))}
        for item in font_list:
            face = item.find(P+"font")
            if face is None:
                continue
            declared = face.get("typeface", "")
            for tag, suffix in (("regular",""),("bold"," Bold"),("italic"," Italic"),("boldItalic"," Bold Italic")):
                node = item.find(P+tag)
                if node is None or font_key(declared+suffix) != font_key(requested):
                    continue
                try:
                    rid = node.get(R+"id")
                    rel = rels.get(rid)
                    if rel is None or rel.get("TargetMode") == "External" or not rel.get("Type","").endswith("/font"):
                        raise ValueError("небезопасная ссылка встроенного шрифта")
                    target = rel.get("Target", "")
                    if "\\" in target or ":" in target or "?" in target or "#" in target:
                        raise ValueError("небезопасный путь встроенного шрифта")
                    part = posixpath.normpath("ppt/"+target) if not target.startswith("/") else target[1:]
                    if not part.startswith("ppt/fonts/") or ".." in PurePosixPath(target).parts:
                        raise ValueError("встроенный шрифт находится вне ppt/fonts")
                    if z.getinfo(part).file_size > MAX_FONT_BYTES:
                        raise ValueError("встроенный шрифт превышает 16 МБ")
                    raw, fmt = unpack_font(z.read(part))
                    aliases, _ = inspect_font(raw)
                    if font_key(requested) not in aliases:
                        raise ValueError("название встроенного шрифта не совпадает с его данными")
                    folder = directory / "embedded-fonts"
                    folder.mkdir(exist_ok=True)
                    path = folder / (digest(raw)+".ttf")
                    path.write_bytes(raw)
                    path.chmod(0o600)
                    return str(path), {"kind":"embedded","part":part,"relationship_id":rid,
                        "sha256":digest(raw),"format":fmt}, issues
                except Exception as exc:
                    reason = str(exc) if type(exc) is ValueError else type(exc).__name__
                    issues.append(f"Встроенный шрифт {requested} не использован: {reason}")
    return "", {"kind":"embedded_unavailable"}, issues


def check_glyphs(path, text):
    with TTFont(path, lazy=True) as font:
        supported = set((font.getBestCmap() or {}).keys())
    missing = sorted({ord(c) for c in text if not c.isspace()}-supported)
    if missing:
        examples = ", ".join(f"U+{c:04X}" for c in missing[:8])
        raise InputRejected("В шрифте нет символов нового текста ("+examples+"). Возможно, в PPTX встроена только часть символов. Сохраните шаблон с встраиванием всех символов или предоставьте полный TTF.")
