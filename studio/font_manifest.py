"""Independent role/asset report inspired by the supplied extraction contract.

No Agentico code dependency. No slide text in the report. Latin theme and
placeholder inheritance are resolved; script-specific shaping is not claimed.
"""

from collections import Counter, defaultdict
from pathlib import Path
import json
import os
import tempfile
from fontTools.ttLib import TTFont, TTLibError
from defusedxml import ElementTree as ET
from .fonts import resolve_font
from .embedded_fonts import extract_embedded_font, inspect_font, MAX_FONT_BYTES
from .security import digest, InputRejected

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"


def _atomic_write_json(path, value):
    """Publish a complete JSON document or leave the previous one untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_manifest(report):
    """Fail closed when font uses, assets and unresolved entries disagree."""
    assets = report["assets"]
    by_id = {asset["id"]: asset for asset in assets}
    if len(by_id) != len(assets):
        raise ValueError("Идентификаторы файлов шрифтов не уникальны")
    for asset in assets:
        path = Path(asset["path"])
        if not path.is_file():
            raise ValueError("Файл начертания шрифта отсутствует после анализа")
        raw = path.read_bytes()
        if len(raw) != asset["bytes"] or digest(raw) != asset["sha256"]:
            raise ValueError("Размер или SHA-256 файла шрифта не совпадает с моделью")
    if any(asset_id not in by_id for asset_id in report["roles"].values()):
        raise ValueError("Роль ссылается на отсутствующий файл шрифта")
    missing = set()
    for slide in report["slides"]:
        for use in slide["uses"]:
            asset_id = use.get("asset_id")
            if use["status"] == "missing":
                if asset_id is not None:
                    raise ValueError("Недоступное начертание содержит ссылку на файл")
                missing.add(use["requested"])
            elif asset_id not in by_id or by_id[asset_id]["status"] != use["status"]:
                raise ValueError("Использование шрифта не согласовано с реестром файлов")
    unresolved = {item["requested"] for item in report["unresolved"]}
    if missing != unresolved:
        raise ValueError("Список недоступных начертаний не согласован с использованием на слайдах")


def shapes(items):
    for shape in items:
        yield shape
        if hasattr(shape, "shapes"):
            yield from shapes(shape.shapes)


def theme_fonts(surface):
    master = getattr(surface, "slide_master", None)
    if master is None and hasattr(surface, "slide_layout"):
        master = surface.slide_layout.slide_master
    master = master or surface
    for rel in master.part.rels.values():
        if rel.reltype.endswith("/theme") and not rel.is_external:
            root = ET.fromstring(rel.target_part.blob)
            return {
                key: root.find(".//" + A + tag + "/" + A + "latin").get("typeface", "")
                for key, tag in (("+mj-lt", "majorFont"), ("+mn-lt", "minorFont"))
                if root.find(".//" + A + tag + "/" + A + "latin") is not None
            }
    return {}


def role_of(shape, height):
    if shape.has_table:
        return "table", "observed"
    if shape.is_placeholder:
        kind = str(shape.placeholder_format.type)
        if "TITLE" in kind and "SUBTITLE" not in kind:
            return "title", "placeholder"
        if any(x in kind for x in ("FOOTER", "DATE", "SLIDE_NUMBER")):
            return "footer", "placeholder"
        return "body", "placeholder"
    return (
        ("title", "geometry-heuristic")
        if shape.top < height * 0.23
        else ("body", "geometry-heuristic")
    )


def properties(shape, paragraph, run, surface, role, themes=None):
    from ._vendor.color_extraction.pipeline.reference_font_usage_styles import (
        _parent_shape,
        _source_properties,
        _effective_font,
    )

    chain = [("slide", shape._element)]
    previous = shape._element
    layout = getattr(surface, "slide_layout", None)
    master = getattr(layout or surface, "slide_master", None)
    for origin, owner in (("layout", layout), ("master", master)):
        if owner is not None and owner is not surface:
            parent = _parent_shape(previous, owner._element, master=origin == "master")
            if parent is not None:
                chain.append((origin, parent))
                previous = parent
    # Explicit direct run properties, inheritance by placeholder type, and presentation defaults.
    presentation = next(
        (
            p._element
            for p in surface.part.package.iter_parts()
            if str(p.partname) == "/ppt/presentation.xml"
        ),
        ET.fromstring(
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
        ),
    )
    props = _source_properties(
        paragraph._p,
        run._r if run is not None else None,
        chain,
        master._element if master is not None else None,
        presentation,
        role,
        "slide",
    )
    themes = theme_fonts(surface) if themes is None else themes
    theme = {
        ("major", "latin"): themes.get("+mj-lt", ""),
        ("minor", "latin"): themes.get("+mn-lt", ""),
    }
    resolved = _effective_font(props, "latin", role, theme)
    if not resolved:
        return {"family": ""}
    family, source, size, weight, italic = resolved
    result = {"family": family, "source": source}
    if size is not None:
        result["sz"] = str(size)
    if weight is not None:
        result["b"] = "1" if weight == 700 else "0"
    if italic is not None:
        result["i"] = "1" if italic else "0"
    return result


def build_font_manifest(prs, path, directory, allow_download=False, progress=None):
    directory.mkdir(parents=True, exist_ok=True)
    uses, counts, slides = [], defaultdict(Counter), []
    surfaces = list(prs.slides) or [layout for m in prs.slide_masters for layout in m.slide_layouts]
    for index, surface in enumerate(surfaces, 1):
        entries = []
        themes = theme_fonts(surface)
        for shape in shapes(surface.shapes):
            role, evidence = role_of(shape, prs.slide_height)
            frames = (
                [shape.text_frame]
                if shape.has_text_frame
                else [c.text_frame for row in shape.table.rows for c in row.cells]
                if shape.has_table
                else []
            )
            for frame in frames:
                for paragraph in frame.paragraphs:
                    for run in list(paragraph.runs) or [None]:
                        props = properties(shape, paragraph, run, surface, role, themes)
                        family = props["family"]
                        if not family:
                            continue
                        requested = family
                        for attr, suffix in (("b", "Bold"), ("i", "Italic")):
                            if (
                                props.get(attr) in ("1", "true")
                                and suffix.lower() not in requested.lower()
                            ):
                                requested += " " + suffix
                        entry = {
                            "family": family,
                            "requested": requested,
                            "role": role,
                            "source": props.get("source", "theme"),
                            "evidence": evidence,
                            "shape_id": shape.shape_id,
                            "characters": len(run.text) if run is not None else 0,
                            "size_pt": int(props["sz"]) / 100
                            if props.get("sz", "").isdigit()
                            else None,
                        }
                        entries.append(entry)
                        counts[role][requested] += max(1, entry["characters"])
        slides.append(
            {"number": index, "surface": "slide" if len(prs.slides) else "layout", "uses": entries}
        )
        uses.extend(entries)
    requested_faces = sorted({u["requested"] for u in uses})
    required_faces = {counter.most_common(1)[0][0] for counter in counts.values()}
    if len(requested_faces) > 64:
        raise InputRejected("В шаблоне более 64 начертаний шрифта")
    assets, missing, warnings = [], [], []
    for requested in requested_faces:
        if progress:
            progress("Проверяем начертание шрифта: " + requested)
        file, origin, issues = extract_embedded_font(path, directory, requested)
        warnings.extend(issues)
        status = "embedded"
        if not file:
            file = resolve_font(requested)
            status = "installed"
            origin = {"kind": "local"}
            if file and "/local-fonts/google/" in Path(file).as_posix():
                metadata_path = Path(file).with_suffix(".json")
                if metadata_path.is_file():
                    cached = json.loads(metadata_path.read_text())
                    if cached.get("sha256") == digest(Path(file).read_bytes()):
                        status = "downloaded"
                        origin = {
                            "kind": "downloaded",
                            "provider": "google-fonts",
                            "license": "OFL-1.1",
                            "source": cached["source"],
                        }
        # Only role-selected faces can block generation. Decorative/sample faces
        # that are not selected for title/body/table/footer must not trigger a
        # network lookup or user-facing warning.
        if not file and allow_download and requested in required_faces:
            if progress:
                progress("Ищем открытый шрифт в Google Fonts: " + requested)
            from .open_fonts import download_face

            file, issue = download_face(requested)
            status = "downloaded"
            origin = {"kind": "downloaded", "provider": "google-fonts", "license": "OFL-1.1"}
            # A failed lookup is represented by the structured unresolved entry
            # below. Duplicating it as a generic warning made optional faces look
            # like generation failures.
        if not file:
            missing.append({"requested": requested, "reason": "Точное начертание недоступно"})
            continue
        try:
            font_path = Path(file)
            if font_path.stat().st_size > MAX_FONT_BYTES:
                raise ValueError("TTF превышает 16 МБ")
            raw = font_path.read_bytes()
            if status == "downloaded" and font_path.with_suffix(".json").is_file():
                cached = json.loads(font_path.with_suffix(".json").read_text())
                if cached.get("sha256") != digest(raw):
                    raise ValueError("Hash кэшированного шрифта не совпадает с записью загрузки")
                origin["source"] = cached["source"]
            with TTFont(file) as font:
                if "glyf" not in font or "fvar" in font:
                    raise ValueError("Нужен статический TrueType")
                fs_type = font["OS/2"].fsType
                # Installed restricted faces can render locally without shipping
                # their font programs. This applies to system Arial too, not just
                # Office's DFonts directory. Never relax embedded/downloaded checks
                # or rewrite fsType. Aptos stays local-only even when fsType is zero.
                local_only = (status == "installed" and bool(fs_type)) or (
                    font["name"].getBestFamilyName().startswith("Aptos")
                    or "Contents/Resources/DFonts" in font_path.as_posix()
                )
                if fs_type and not local_only:
                    raise ValueError("Ограничения встраивания несовместимы с экспортом")
                if not local_only:
                    inspect_font(raw)
                asset = {
                    "id": digest(raw),
                    "requested": requested,
                    "path": file,
                    "sha256": digest(raw),
                    "bytes": len(raw),
                    "status": status,
                    "weight": font["OS/2"].usWeightClass,
                    "italic": bool(font["head"].macStyle & 2),
                    "redistributable": not local_only,
                    "fs_type": fs_type,
                    "origin": {**origin, "sha256": digest(raw)},
                }
                assets.append(asset)
        except (ValueError, OSError, TTLibError) as exc:
            missing.append({"requested": requested, "reason": str(exc)})
    by_name = {a["requested"]: a for a in assets}
    bindings = {}
    for role, counter in counts.items():
        primary = counter.most_common(1)[0][0]
        if primary in by_name:
            bindings[role] = by_name[primary]["id"]
    for item in missing:
        item["required_for_generation"] = item["requested"] in required_faces
    for use in uses:
        asset = by_name.get(use["requested"])
        use.update(
            status=asset["status"] if asset else "missing", asset_id=asset["id"] if asset else None
        )
    report = {
        "schema_version": 1,
        "slides": slides,
        "assets": assets,
        "roles": bindings,
        "unresolved": missing,
        "warnings": warnings,
        "limitations": [
            "Latin theme/placeholder inheritance; role selection per deck, not per text run",
            "MTX requires Node.js; no protected EOT, variable fonts or script-specific font routing",
        ]
        + (
            ["Local-only font assets are not embedded into PPTX/HTML/PDF"]
            if any(not a["redistributable"] for a in assets)
            else []
        ),
    }
    _validate_manifest(report)
    # Report deliberately omits local filesystem paths.
    public = {**report, "assets": [{k: v for k, v in a.items() if k != "path"} for a in assets]}
    _atomic_write_json(directory / "font-model.json", public)
    return report
