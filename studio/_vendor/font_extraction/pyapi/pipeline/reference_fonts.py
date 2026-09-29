"""Resolve font *files* for a PPTX/POTX reference, not just its OOXML font names.

PowerPoint may embed EOT/MTX binaries, or merely name a font installed on the
author's computer. Only the former can always be recovered from the PPTX.
"""

import asyncio
import base64
import logging
import posixpath
import struct
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

from studio._vendor.font_extraction.pyapi.application.ports import FontDecoder, FontResolver
from studio._vendor.font_extraction.pyapi.domain.archive_safety import validate_archive

logger = logging.getLogger(__name__)

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
VARIANTS = {
    "regular": (400, "normal"),
    "bold": (700, "normal"),
    "italic": (400, "italic"),
    "boldItalic": (700, "italic"),
}
SFNT_FORMATS = {b"\x00\x01\x00\x00": ("ttf", "font/ttf"), b"OTTO": ("otf", "font/otf")}


def _tables(data: bytes) -> dict[bytes, tuple[int, int]]:
    if len(data) < 12 or data[:4] not in SFNT_FORMATS:
        raise ValueError("Unsupported font binary")
    table_count = struct.unpack_from(">H", data, 4)[0]
    if 12 + table_count * 16 > len(data):
        raise ValueError("Truncated font table directory")
    tables = {}
    for index in range(table_count):
        offset = 12 + index * 16
        tag, _, table_offset, table_size = struct.unpack_from(">4sIII", data, offset)
        if table_offset + table_size > len(data):
            raise ValueError("Truncated font table")
        tables[tag] = (table_offset, table_size)
    return tables


def _font_permissions(data: bytes) -> int:
    """Read OS/2.fsType deterministically from a TTF/OTF table directory."""
    offset, size = _tables(data).get(b"OS/2", (0, 0))
    if size < 10:
        raise ValueError("Font has no OS/2 embedding permissions")
    return struct.unpack_from(">H", data, offset + 8)[0]


def _binary_variant(data: bytes, declared_style: str) -> tuple[int, str]:
    """Use the decoded face's weight, not the PPTX regular/bold slot name."""
    tables = _tables(data)
    os2_offset, os2_size = tables.get(b"OS/2", (0, 0))
    if os2_size < 6:
        raise ValueError("Font has no OS/2 weight")
    weight = struct.unpack_from(">H", data, os2_offset + 4)[0]
    weight = max(100, min(900, round(weight / 100) * 100))
    head_offset, head_size = tables.get(b"head", (0, 0))
    italic = declared_style == "italic"
    if head_size >= 46:
        italic |= bool(struct.unpack_from(">H", data, head_offset + 44)[0] & 2)
    if os2_size >= 64:
        italic |= bool(struct.unpack_from(">H", data, os2_offset + 62)[0] & 1)
    return weight, "italic" if italic else "normal"


def _may_repackage(permissions: int) -> bool:
    # Installable (0) or editable embedding (0x0008) is required because the
    # output is a self-contained, editable HTML/PPTX, not a print-only preview.
    return not (permissions & (0x0002 | 0x0004 | 0x0200))


def _embedded_records(data: bytes) -> list[tuple[str, int, str, str, bytes]]:
    with zipfile.ZipFile(BytesIO(data)) as package:
        validate_archive(package)
        names = set(package.namelist())
        rels_path = "ppt/_rels/presentation.xml.rels"
        if rels_path not in names:
            return []
        relationships = ET.fromstring(package.read(rels_path))
        targets = {
            node.get("Id"): node.get("Target")
            for node in relationships
            if node.get("Type", "").endswith("/font")
        }
        presentation = ET.fromstring(package.read("ppt/presentation.xml"))
        records = []
        for entry in presentation.findall(f".//{{{P}}}embeddedFont"):
            descriptor = entry.find(f"{{{P}}}font")
            family = descriptor.get("typeface", "").strip() if descriptor is not None else ""
            if not family:
                continue
            for variant, (weight, style) in VARIANTS.items():
                node = entry.find(f"{{{P}}}{variant}")
                target = targets.get(node.get(f"{{{R}}}id")) if node is not None else None
                if not target:
                    continue
                member = (
                    target.lstrip("/")
                    if target.startswith("/")
                    else posixpath.normpath(posixpath.join("ppt", target))
                )
                if not member.startswith("ppt/") or member not in names:
                    continue
                records.append((family, weight, style, member, package.read(member)))
        return records


def _asset(family: str, weight: int, style: str, source: str, data: bytes) -> dict:
    extension, mime = SFNT_FORMATS[data[:4]]
    return {
        "family": family,
        "weight": weight,
        "style": style,
        "format": extension,
        "mime": mime,
        "source": source,
        "data": base64.b64encode(data).decode("ascii"),
    }


def uploaded_font_asset(data: bytes) -> dict:
    """Read a user-supplied TTF/OTF name table and attach only permitted fonts."""
    tables = _tables(data)
    if not _may_repackage(_font_permissions(data)):
        raise ValueError("Font embedding permissions do not allow editable export")
    name_offset, name_size = tables.get(b"name", (0, 0))
    if name_size < 6:
        raise ValueError("Font has no name table")
    _, count, strings_offset = struct.unpack_from(">HHH", data, name_offset)
    if name_size < 6 + count * 12:
        raise ValueError("Truncated font name table")
    names: dict[int, list[tuple[int, str]]] = {}
    for index in range(count):
        record = name_offset + 6 + index * 12
        platform, _, language, name_id, length, offset = struct.unpack_from(">HHHHHH", data, record)
        start = name_offset + strings_offset + offset
        if start + length > name_offset + name_size:
            continue
        try:
            label = (
                data[start : start + length]
                .decode("utf-16-be" if platform in (0, 3) else "mac_roman")
                .strip()
            )
        except UnicodeError:
            logger.debug(
                "Font name record could not be decoded",
                extra={"event": "pipeline.font.name_decode_failed", "stage": "reference-fonts"},
                exc_info=True,
            )
            continue
        if label:
            priority = 0 if platform == 3 and language == 0x0409 else 1 if platform in (0, 3) else 2
            names.setdefault(name_id, []).append((priority, label))

    def best(*ids: int) -> str:
        for name_id in ids:
            if names.get(name_id):
                return min(names[name_id])[1]
        return ""

    family, subfamily = best(16, 1), best(17, 2)
    if not family:
        raise ValueError("Font family name is missing")
    os2_offset, _ = tables[b"OS/2"]
    weight = struct.unpack_from(">H", data, os2_offset + 4)[0]
    weight = max(100, min(900, round(weight / 100) * 100))
    return _asset(
        family, weight, "italic" if "italic" in subfamily.casefold() else "normal", "uploaded", data
    )


def _style_weight(style: str) -> tuple[int, str]:
    label = style.lower().replace(" ", "")
    if "thin" in label:
        weight = 100
    elif "extralight" in label or "ultralight" in label:
        weight = 200
    elif "light" in label:
        weight = 300
    elif "medium" in label and "semi" not in label:
        weight = 500
    elif "semibold" in label or "demibold" in label:
        weight = 600
    elif "extrabold" in label or "ultrabold" in label:
        weight = 800
    elif "black" in label or "heavy" in label:
        weight = 900
    elif "bold" in label:
        weight = 700
    else:
        weight = 400
    return weight, "italic" if "italic" in label or "oblique" in label else "normal"


async def _installed_fonts(families: set[str]) -> tuple[list[dict], set[str]]:
    if not families:
        return [], set()
    try:
        process = await asyncio.create_subprocess_exec(
            "fc-list",
            "-f",
            "%{family}|%{style}|%{file}\\n",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        logger.warning(
            "Installed font lookup is unavailable",
            extra={"event": "pipeline.font.lookup_unavailable", "stage": "reference-fonts"},
            exc_info=True,
        )
        return [], set()
    output, errors = await process.communicate()
    if process.returncode:
        logger.error(
            "Installed font lookup failed",
            extra={
                "event": "pipeline.font.lookup_failed",
                "stage": "reference-fonts",
                "status_code": process.returncode,
                "error": errors.decode(errors="replace")[-500:],
            },
        )
        return [], set()
    wanted = {family.casefold(): family for family in families}
    assets, resolved, seen = [], set(), set()
    for line in output.decode(errors="replace").splitlines():
        fields = line.split("|", 2)
        if len(fields) != 3:
            continue
        names, style, file_name = fields
        family = next(
            (
                wanted[name.strip().casefold()]
                for name in names.split(",")
                if name.strip().casefold() in wanted
            ),
            None,
        )
        path = Path(file_name)
        if family is None or path.suffix.lower() not in (".ttf", ".otf") or not path.is_file():
            continue
        weight, slant = _style_weight(style)
        key = (family.casefold(), weight, slant)
        if key in seen:
            continue
        try:
            data = await asyncio.to_thread(path.read_bytes)
            if _may_repackage(_font_permissions(data)):
                assets.append(_asset(family, weight, slant, "installed", data))
                resolved.add(family)
                seen.add(key)
        except (OSError, ValueError, KeyError):
            logger.warning(
                "Installed font could not be used",
                extra={"event": "pipeline.font.installed_failed", "stage": "reference-fonts"},
                exc_info=True,
            )
            continue
    return assets, resolved


async def reference_font_assets(
    data: bytes,
    families: set[str],
    decoder: FontDecoder,
    resolver: FontResolver | None = None,
    required_families: set[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """Extract embedded fonts first, then exact installed matches; report missing files."""
    assets, warnings, resolved, seen = [], [], set(), set()
    for family, weight, style, member, payload in _embedded_records(data):
        try:
            if payload[:4] in SFNT_FORMATS:
                font = payload
                permitted = _may_repackage(_font_permissions(font))
            else:
                decoded = await decoder.decode_eot(payload)
                font = base64.b64decode(decoded["data"]) if decoded.get("embeddable") else b""
                permitted = bool(decoded.get("embeddable"))
            if not permitted:
                warnings.append(
                    f"Шрифт {family}: PPTX не разрешает повторное встраивание этого начертания"
                )
                logger.warning(
                    "Embedded font cannot be repackaged",
                    extra={
                        "event": "pipeline.font.restricted",
                        "stage": "reference-fonts",
                        "item_key": family,
                    },
                )
                continue
            _font_permissions(font)
            weight, style = _binary_variant(font, style)
            key = (family.casefold(), weight, style)
            if key not in seen:
                assets.append(_asset(family, weight, style, "pptx", font))
                seen.add(key)
                resolved.add(family)
        except (KeyError, ValueError, OSError, RuntimeError) as exc:
            logger.exception(
                "Embedded font extraction failed",
                extra={
                    "event": "pipeline.font.embedded_failed",
                    "stage": "reference-fonts",
                    "item_key": family,
                },
            )
            warnings.append(f"Шрифт {family}: не удалось извлечь {member} ({exc})")
    installed, installed_names = await _installed_fonts(families - resolved)
    assets.extend(installed)
    resolved.update(installed_names)
    if resolver and families - resolved:
        requested = {family.casefold(): family for family in families - resolved}
        for record in await resolver.resolve(set(requested.values())):
            family = requested.get(str(record.get("family") or "").casefold())
            font = record.get("data")
            if family is None or not isinstance(font, bytes):
                continue
            try:
                if font[:4] not in SFNT_FORMATS or not _may_repackage(_font_permissions(font)):
                    continue
                key = (
                    family.casefold(),
                    int(record.get("weight") or 400),
                    str(record.get("style") or "normal"),
                )
                if key in seen:
                    continue
                assets.append(
                    _asset(
                        family,
                        key[1],
                        key[2],
                        str(record.get("source") or "open-font"),
                        font,
                    )
                )
                seen.add(key)
                resolved.add(family)
            except (KeyError, ValueError):
                logger.warning(
                    "Resolved open font failed binary validation",
                    extra={
                        "event": "pipeline.font.open_invalid",
                        "stage": "reference-fonts",
                        "item_key": family,
                    },
                    exc_info=True,
                )
    required_missing = (required_families or set()) - resolved
    for family in sorted(required_missing):
        logger.warning(
            "Primary reference font is unavailable; controlled fallback will be used",
            extra={
                "event": "pipeline.font.primary_missing",
                "stage": "reference-fonts",
                "item_key": family,
            },
        )
    for family in sorted(families - resolved):
        warnings.append(
            f"Шрифт {family} указан в PPTX, но его файла нет в шаблоне или доступных шрифтах; "
            "в экспорте будет запасной шрифт. Приложите TTF/OTF для точного результата"
        )
        logger.warning(
            "Reference font file missing",
            extra={
                "event": "pipeline.font.missing",
                "stage": "reference-fonts",
                "item_key": family,
            },
        )
    return assets, warnings
