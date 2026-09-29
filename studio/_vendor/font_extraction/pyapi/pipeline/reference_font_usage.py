"""Inventory effective fonts and empty-placeholder hints in a PPTX/POTX reference."""

from __future__ import annotations

import zipfile
from collections import defaultdict
from io import BytesIO
from xml.etree import ElementTree as ET

from studio._vendor.font_extraction.pyapi.domain.archive_safety import validate_archive
from studio._vendor.font_extraction.pyapi.pipeline.ooxml_resolution import effective_theme, inheritance_parts
from studio._vendor.font_extraction.pyapi.pipeline.reference_font_usage_styles import _effective_font, _parent_shape, _placeholder, _resolve_name, _role, _script_counts, _source_properties, _theme_fonts
from studio._vendor.font_extraction.pyapi.pipeline.reference_profile import _ordered_slides, _relationship_rows

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SCRIPTS = ("latin", "ea", "cs", "sym")
TEXT_RUNS = {f"{{{A}}}r", f"{{{A}}}fld"}


def _shape_id(shape: ET.Element, source: str) -> str:
    node = shape.find(f"{{{P}}}nvSpPr/{{{P}}}cNvPr")
    return f"{source}:{node.get('id', '?') if node is not None else '?'}"


def _paragraphs(shape: ET.Element) -> list[ET.Element]:
    body = shape.find(f"{{{P}}}txBody")
    return body.findall(f"{{{A}}}p") if body is not None else []


def _embedded_names(package: zipfile.ZipFile, presentation: ET.Element) -> dict[str, list[str]]:
    names: dict[str, list[str]] = defaultdict(list)
    font_parts = {
        identifier: target
        for identifier, kind, target in _relationship_rows(package, "ppt/presentation.xml")
        if kind.endswith("/font") and target in package.namelist()
    }
    for entry in presentation.findall(f".//{{{P}}}embeddedFont"):
        descriptor = entry.find(f"{{{P}}}font")
        family = descriptor.get("typeface", "").strip() if descriptor is not None else ""
        if family:
            variants = [
                variant
                for variant in ("regular", "bold", "italic", "boldItalic")
                if (node := entry.find(f"{{{P}}}{variant}")) is not None
                and node.get(f"{{{R}}}id") in font_parts
            ]
            if variants:
                names[family].extend(variants)
    return dict(names)


def _declared_names(roots: list[ET.Element], theme: dict[tuple[str, str], str]) -> set[str]:
    result = set(theme.values())
    for root in roots:
        for node in root.iter():
            script = node.tag.rsplit("}", 1)[-1]
            if script in SCRIPTS or script == "font":
                name = (node.get("typeface") or "").strip()
                if name:
                    result.add(_resolve_name(name, script, theme))
    return result


def _record(
    uses: dict[tuple, dict],
    hints: dict[tuple, dict],
    found: tuple,
    script: str,
    role: str,
    shape_id: str,
    characters: int,
) -> None:
    family, source, size, weight, italic = found
    key = (family, script, role, source, weight, italic)
    target = uses if characters else hints
    item = target.setdefault(
        key,
        {
            "family": family,
            "script": script,
            "role": role,
            "source": source,
            "weight": weight,
            "italic": italic,
            "sizesPt": set(),
            "characters": 0,
            "runCount": 0,
            "shapeIds": set(),
        },
    )
    if size is not None:
        item["sizesPt"].add(size / 100)
    item["characters"] += characters
    item["runCount"] += bool(characters)
    item["shapeIds"].add(shape_id)


def _finalize(items: dict[tuple, dict]) -> list[dict]:
    return [
        {**item, "sizesPt": sorted(item["sizesPt"]), "shapeIds": sorted(item["shapeIds"])}
        for _, item in sorted(items.items(), key=lambda entry: str(entry[0]))
    ]


def _recommend(
    role: str,
    font_uses: list[dict],
    font_hints: list[dict],
    theme: dict[tuple[str, str], str],
) -> tuple[str | None, str]:
    exact = [item for item in [*font_uses, *font_hints] if item["role"] == role]
    if exact:
        chosen = max(exact, key=lambda item: (item["characters"], max(item["sizesPt"], default=0)))
        return chosen["family"], "observed-role" if chosen["characters"] else "placeholder"
    other = [item for item in font_uses if item["role"] in {"other", "subtitle"}]
    if other:
        if role == "title":
            chosen = max(
                other, key=lambda item: (max(item["sizesPt"], default=0), item["characters"])
            )
            return chosen["family"], "size-heuristic"
        chosen = max(other, key=lambda item: (item["characters"], max(item["sizesPt"], default=0)))
        return chosen["family"], "text-heuristic"
    return theme.get(("major" if role == "title" else "minor", "latin")), "theme"


def extract_reference_font_usage(data: bytes, presentation_name: str) -> dict:
    """Return observed run fonts, placeholder hints, and declared families without font binaries."""
    with zipfile.ZipFile(BytesIO(data)) as package:
        validate_archive(package)
        if "ppt/presentation.xml" not in package.namelist():
            raise ValueError("Invalid PPTX/POTX reference")
        presentation = ET.fromstring(package.read("ppt/presentation.xml"))
        slides = []
        catalog: dict[str, dict] = {}
        embedded = _embedded_names(package, presentation)
        for number, part in enumerate(
            _ordered_slides(package, set(package.namelist()), presentation), 1
        ):
            chain = inheritance_parts(package, part)
            roots = [ET.fromstring(package.read(item)) for item in chain]
            theme_root, _ = effective_theme(package, part)
            theme = _theme_fonts(theme_root)
            master = roots[-1] if len(roots) == 3 else None
            uses: dict[tuple, dict] = {}
            hints: dict[tuple, dict] = {}

            for index, root in enumerate(roots):
                shape_source = ("slide", "layout", "master")[index]
                for shape in root.findall(f".//{{{P}}}sp"):
                    if index and _placeholder(shape) is not None:
                        continue  # Placeholder samples are hints for the slide shape, not displayed text.
                    ancestors = [(shape_source, shape)]
                    if index == 0:
                        previous = shape
                        for ancestor_index, parent in enumerate(roots[1:], 1):
                            matched = _parent_shape(previous, parent, master=ancestor_index > 1)
                            if matched is not None:
                                ancestors.append(
                                    (("layout", "master")[ancestor_index - 1], matched)
                                )
                                previous = matched
                    role = _role(shape, "shape")
                    shape_id = _shape_id(shape, shape_source)
                    paragraphs = _paragraphs(shape)
                    if index == 0 and _placeholder(shape) is not None and not paragraphs:
                        paragraphs = [ET.Element(f"{{{A}}}p")]
                    for paragraph in paragraphs:
                        runs = [run for run in paragraph if run.tag in TEXT_RUNS]
                        if not any(run.findtext(f"{{{A}}}t") for run in runs) and index == 0:
                            properties = _source_properties(
                                paragraph,
                                runs[0] if runs else None,
                                ancestors,
                                master,
                                presentation,
                                role,
                                shape_source,
                            )
                            found = _effective_font(properties, "latin", role, theme)
                            if found:
                                _record(uses, hints, found, "latin", role, shape_id, 0)
                        for run in runs:
                            value = run.findtext(f"{{{A}}}t") or ""
                            if not value:
                                continue
                            properties = _source_properties(
                                paragraph,
                                run,
                                ancestors,
                                master,
                                presentation,
                                role,
                                shape_source,
                            )
                            for script, count in _script_counts(value).items():
                                found = _effective_font(properties, script, role, theme)
                                if found:
                                    _record(uses, hints, found, script, role, shape_id, count)
                if index != 0:
                    continue
                for frame in root.findall(f".//{{{P}}}graphicFrame"):
                    frame_id = frame.find(f"{{{P}}}nvGraphicFramePr/{{{P}}}cNvPr")
                    shape_id = f"slide:{frame_id.get('id', '?') if frame_id is not None else '?'}"
                    paragraphs = [
                        paragraph
                        for table in frame.findall(f".//{{{A}}}tbl")
                        for paragraph in table.findall(f".//{{{A}}}p")
                    ]
                    for paragraph in paragraphs:
                        runs = [run for run in paragraph if run.tag in TEXT_RUNS]
                        if not any(run.findtext(f"{{{A}}}t") for run in runs):
                            properties = _source_properties(
                                paragraph,
                                runs[0] if runs else None,
                                [],
                                master,
                                presentation,
                                "table",
                                "slide",
                            )
                            found = _effective_font(properties, "latin", "table", theme)
                            if found:
                                _record(uses, hints, found, "latin", "table", shape_id, 0)
                        for run in runs:
                            if not (value := run.findtext(f"{{{A}}}t")):
                                continue
                            properties = _source_properties(
                                paragraph, run, [], master, presentation, "table", "slide"
                            )
                            for script, count in _script_counts(value).items():
                                found = _effective_font(properties, script, "table", theme)
                                if found:
                                    _record(uses, hints, found, script, "table", shape_id, count)

            for _, kind, target in _relationship_rows(package, part):
                if (
                    not kind.endswith(("/chart", "/diagramData"))
                    or target not in package.namelist()
                ):
                    continue
                context = "chart" if kind.endswith("/chart") else "diagram"
                linked_root = ET.fromstring(package.read(target))
                for paragraph in linked_root.findall(f".//{{{A}}}p"):
                    runs = [run for run in paragraph if run.tag in TEXT_RUNS]
                    if not any(run.findtext(f"{{{A}}}t") for run in runs):
                        properties = _source_properties(
                            paragraph,
                            runs[0] if runs else None,
                            [],
                            master,
                            presentation,
                            context,
                            context,
                        )
                        found = _effective_font(properties, "latin", context, theme)
                        if found:
                            _record(uses, hints, found, "latin", context, target, 0)
                    for run in runs:
                        if not (value := run.findtext(f"{{{A}}}t")):
                            continue
                        properties = _source_properties(
                            paragraph, run, [], master, presentation, context, context
                        )
                        for script, count in _script_counts(value).items():
                            found = _effective_font(properties, script, context, theme)
                            if found:
                                _record(uses, hints, found, script, context, target, count)

            font_uses, font_hints = _finalize(uses), _finalize(hints)
            title, title_basis = _recommend("title", font_uses, font_hints, theme)
            body, body_basis = _recommend("body", font_uses, font_hints, theme)
            for kind, entries in (("usedOnSlides", font_uses), ("hintedOnSlides", font_hints)):
                for item in entries:
                    catalog.setdefault(
                        item["family"],
                        {"usedOnSlides": set(), "hintedOnSlides": set(), "declaredOnSlides": set()},
                    )[kind].add(number)
            for family in _declared_names(
                [*roots, *([theme_root] if theme_root is not None else [])], theme
            ):
                catalog.setdefault(
                    family,
                    {"usedOnSlides": set(), "hintedOnSlides": set(), "declaredOnSlides": set()},
                )["declaredOnSlides"].add(number)
            slides.append(
                {
                    "number": number,
                    "part": part,
                    "fontUses": font_uses,
                    "fontHints": font_hints,
                    "recommendedFonts": {"title": title, "body": body},
                    "recommendationBasis": {"title": title_basis, "body": body_basis},
                }
            )
        for family in embedded:
            catalog.setdefault(
                family, {"usedOnSlides": set(), "hintedOnSlides": set(), "declaredOnSlides": set()}
            )
        return {
            "schemaVersion": 1,
            "presentation": presentation_name,
            "slideCount": len(slides),
            "slides": slides,
            "fontCatalog": [
                {
                    "family": family,
                    "usedOnSlides": sorted(item["usedOnSlides"]),
                    "hintedOnSlides": sorted(item["hintedOnSlides"]),
                    "declaredOnSlides": sorted(item["declaredOnSlides"]),
                    "embeddedVariants": sorted(set(embedded.get(family, []))),
                }
                for family, item in sorted(catalog.items(), key=lambda entry: entry[0].casefold())
            ],
        }
