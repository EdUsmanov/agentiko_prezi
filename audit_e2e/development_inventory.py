"""Auditable inventory for original references, replay analogs, and owned fixtures."""

from collections import defaultdict
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
import xml.etree.ElementTree as ET

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from .corpus import AUDIT_ROOT, load_cases

DEVELOPMENT_ROOT = AUDIT_ROOT / "fixtures" / "development"
_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_PPTX_MAIN = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"


def _sha(data):
    return sha256(data).hexdigest()


def _grouped(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row["id"])
    return [
        {"sha256": value, "case_ids": sorted(case_ids)}
        for value, case_ids in sorted(groups.items())
        if len(case_ids) > 1
    ]


def _presentation(data, file_name):
    """Open PPTX/POTX bytes with python-pptx after normalizing a POTX main type."""
    if Path(file_name).suffix.lower() == ".potx":
        with ZipFile(BytesIO(data)) as source:
            parts = {name: source.read(name) for name in source.namelist()}
        content_types = ET.fromstring(parts["[Content_Types].xml"])
        main = content_types.find(f"{{{_CT}}}Override[@PartName='/ppt/presentation.xml']")
        if main is None:
            raise ValueError(f"POTX has no presentation content type: {file_name}")
        main.set("ContentType", _PPTX_MAIN)
        parts["[Content_Types].xml"] = ET.tostring(
            content_types, encoding="UTF-8", xml_declaration=True
        )
        buffer = BytesIO()
        with ZipFile(buffer, "w", ZIP_DEFLATED, compresslevel=9) as target:
            for name in sorted(parts):
                info = ZipInfo(name, (2020, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                target.writestr(info, parts[name], compress_type=ZIP_DEFLATED, compresslevel=9)
        data = buffer.getvalue()
    return Presentation(BytesIO(data))


def _template_facts(path):
    data = path.read_bytes()
    with ZipFile(BytesIO(data)) as package:
        names = package.namelist()
    prs = _presentation(data, path.name)
    width, height = int(prs.slide_width), int(prs.slide_height)
    shapes = [shape for slide in prs.slides for shape in slide.shapes]
    table_shapes = [shape for shape in shapes if shape.has_table]
    chart_shapes = [shape for shape in shapes if shape.has_chart]
    group_shapes = [shape for shape in shapes if shape.shape_type == MSO_SHAPE_TYPE.GROUP]
    picture_shapes = [shape for shape in shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE]
    text_shapes = [shape for shape in shapes if shape.has_text_frame]
    master_shape_count = sum(len(master.shapes) for master in prs.slide_masters)
    layout_count = sum(len(list(master.slide_layouts)) for master in prs.slide_masters)
    placeholder_count = sum(
        1
        for master in prs.slide_masters
        for layout in master.slide_layouts
        for _ in layout.placeholders
    )
    narrow_regions = sum(
        shape.width / width <= 0.38 and shape.height / height >= 0.20
        for shape in shapes
        if shape.width and shape.height
    )
    content_region_count = sum(
        shape.has_text_frame
        and shape.top / height >= 0.20
        and 0.08 <= shape.width / width <= 0.72
        and shape.height / height >= 0.18
        for shape in shapes
        if shape.width and shape.height
    )
    dark_background = False
    for master in prs.slide_masters:
        try:
            color = master.background.fill.fore_color.rgb
        except (AttributeError, ValueError):
            color = None
        if color is not None:
            red, green, blue = color[0], color[1], color[2]
            if (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255 < 0.42:
                dark_background = True
    table_dimensions = [
        {"rows": len(shape.table.rows), "columns": len(shape.table.columns)}
        for shape in table_shapes
    ]
    chart_types = sorted(str(shape.chart.chart_type) for shape in chart_shapes)
    embedded_workbooks = sorted(
        name
        for name in names
        if name.startswith("ppt/embeddings/") and name.lower().endswith(".xlsx")
    )
    source_notes = any(
        any(word in shape.text.casefold() for word in ("source", "unit", "notes"))
        for shape in text_shapes
    )
    features = {
        "layout_inheritance": layout_count > 0,
        "master_graphics": master_shape_count > 0,
        "narrow_regions": narrow_regions > 0,
        "dark_theme": dark_background,
        "group_shapes": bool(group_shapes),
        "split_regions": sum(shape.has_text_frame for shape in shapes) >= 2,
        "timeline_rail": len(shapes) >= 6,
        "multi_column_layout": content_region_count >= 2,
        "connected_process": len(group_shapes) >= 2,
        "team_layouts": len(prs.slide_masters) > 1,
        "native_tables": bool(table_shapes),
        "wide_canvas": width / height >= 1.70,
        "native_charts": bool(chart_shapes),
        "embedded_workbook": bool(embedded_workbooks),
        "portrait_canvas": width / height < 1.0,
        "source_notes": source_notes,
        "linked_media": bool(picture_shapes),
        "multiple_masters": len(prs.slide_masters) > 1,
        "inherited_placeholders": placeholder_count > 0,
        "four_to_three": abs(width / height - 4 / 3) < 0.02,
    }
    per_slide = []
    for slide in prs.slides:
        rows = []
        for shape in slide.shapes:
            if shape.has_text_frame and not shape.text.strip() and not shape.is_placeholder:
                continue
            if not (
                shape.has_text_frame
                or shape.has_table
                or shape.has_chart
                or shape.shape_type in {MSO_SHAPE_TYPE.GROUP, MSO_SHAPE_TYPE.PICTURE}
            ):
                continue
            rows.append(
                [
                    str(shape.shape_type),
                    int(shape.left),
                    int(shape.top),
                    int(shape.width),
                    int(shape.height),
                    len(shape.table.rows) if shape.has_table else None,
                    len(shape.table.columns) if shape.has_table else None,
                    str(shape.chart.chart_type) if shape.has_chart else None,
                ]
            )
        per_slide.append(rows)
    signature = _sha(
        json.dumps([width, height, per_slide], ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    return {
        "sha256": _sha(data),
        "format": path.suffix.lstrip(".").upper(),
        "canvas_emu": {"width": width, "height": height},
        "aspect_ratio": round(width / height, 4),
        "slide_count": len(prs.slides),
        "master_count": len(prs.slide_masters),
        "layout_count": layout_count,
        "master_shape_count": master_shape_count,
        "top_level_shape_count": len(shapes),
        "text_shape_count": len(text_shapes),
        "table_dimensions": table_dimensions,
        "chart_types": chart_types,
        "group_count": len(group_shapes),
        "picture_count": len(picture_shapes),
        "embedded_workbook_parts": embedded_workbooks,
        "narrow_region_count": narrow_regions,
        "measured_content_region_count": content_region_count,
        "structural_signature_sha256": signature,
        "feature_checks": features,
    }


def _replay_analog_inventory(cases):
    from test_support.inputs import make_template

    analogs = []
    with TemporaryDirectory(prefix="audit-e2e-analog-inventory-") as temporary:
        temp = Path(temporary)
        for case in cases:
            config = case["synthetic_template"]
            suffix = ".potx" if case["template"]["format"] == "POTX" else ".pptx"
            path = temp / f"{case['id']}{suffix}"
            make_template(
                path,
                seed=config["seed"],
                aspect=config["aspect"],
                columns=config["columns"],
                dark=config["dark"],
            )
            analogs.append(
                {
                    "id": case["id"],
                    "origin": "simplified_replay_analog",
                    "sha256": _sha(path.read_bytes()),
                    "profile": {
                        "aspect": config["aspect"],
                        "columns": config["columns"],
                        "dark": config["dark"],
                    },
                    "source_template_sha256": case["template"]["sha256"],
                    "features": ["text_boxes", "seeded_geometry"],
                    "source_reference_features_reproduced": [],
                }
            )
    hash_groups = _grouped(analogs, "sha256")
    profiles = {tuple(sorted(row["profile"].items())) for row in analogs}
    return {
        "cases": analogs,
        "case_count": len(analogs),
        "unique_file_sha256_count": len({row["sha256"] for row in analogs}),
        "duplicate_byte_groups": hash_groups,
        "broad_profile_count": len(profiles),
        "profile_definition": "aspect × content columns × dark/light; seed-only differences are not a distinct profile",
        "limitations": [
            "These seeded analogs are the bytes used for replay; they are not the registered external PPTX/POTX files.",
            "No WPI reference-page feature is present in its simplified replay analog.",
        ],
    }


def inventory_report():
    """Return JSON-safe input and structural coverage evidence for the open suites."""
    extended = load_cases("extended")
    development = load_cases("development")
    owned_cases = development[len(extended) :]
    excluded_registered_inputs = [
        case["id"]
        for case in owned_cases
        if any(
            token in value.casefold()
            for token in ("challenge", "private")
            for value in (case["id"], case["content_source"]["file"])
        )
    ]
    owned_rows = []
    for case in owned_cases:
        template_path = DEVELOPMENT_ROOT / case["synthetic_template"]["asset"]
        facts = _template_facts(template_path)
        source_path = AUDIT_ROOT.parent / case["content_source"]["file"]
        source_data = source_path.read_bytes()
        source_hash = _sha(source_data)
        ledger = case.get("reference", {})
        anchored = all(point["quote"] in case["content"] for point in ledger.get("points", []))
        images = []
        for item in case.get("images", []):
            image_path = DEVELOPMENT_ROOT / "images" / item["file"]
            image_data = image_path.read_bytes()
            with Image.open(BytesIO(image_data)) as image:
                dimensions = {"width": image.width, "height": image.height}
            images.append(
                {
                    "file": item["file"],
                    "sha256": _sha(image_data),
                    "dimensions_px": dimensions,
                    "origin": item.get("origin"),
                    "source": item.get("source"),
                }
            )
        declared = list(case["template"].get("features", []))
        checks = {name: facts["feature_checks"].get(name, False) for name in declared}
        if "screenshot_context" in checks:
            checks["screenshot_context"] = bool(images)
        owned_rows.append(
            {
                "id": case["id"],
                "suite": "development",
                "origin": "owned_native_synthetic",
                "family_id": case["template"]["family_id"],
                "material_id": case["template"]["material_id"],
                "declared_features": declared,
                "declared_feature_checks": checks,
                "coverage": list(case["template"].get("coverage", [])),
                "source": {
                    "file": case["content_source"]["file"],
                    "sha256": source_hash,
                    "registered_sha256": case["content_source"]["sha256"],
                    "matches_registry": source_hash == case["content_source"]["sha256"],
                    "independently_authored_file": case["content_source"]["file"].startswith(
                        "audit_e2e/fixtures/development/sources/"
                    ),
                    "reference_status": ledger.get("status"),
                    "reference_provenance": ledger.get("provenance"),
                    "reference_point_count": len(ledger.get("points", [])),
                    "all_quotes_source_anchored": anchored,
                    "review_status": ledger.get("provenance", {}).get("review_status"),
                },
                "template": {
                    "file": case["template"]["file"],
                    "registered_sha256": case["template"]["sha256"],
                    **facts,
                },
                "images": images,
                "source_groups": len(
                    [line for line in case["content"].splitlines() if line.startswith("## ")]
                ),
                "target_slides": case["slides"],
                "content_characters": len(case["content"]),
            }
        )
    signature_groups = defaultdict(list)
    for row in owned_rows:
        signature_groups[row["template"]["structural_signature_sha256"]].append(row["id"])
    effective_count = len(signature_groups)
    external_rows = []
    for case in extended:
        source = case["template"].get("source", "")
        host = urlsplit(source).hostname or "local-or-unspecified"
        external_rows.append(
            {
                "id": case["id"],
                "source_url": source,
                "source_host": host,
                "template_file": case["template"]["file"],
                "registered_external_sha256": case["template"]["sha256"],
                "format": case["template"]["format"],
                "aspect_ratio": case["template"]["aspect_ratio"],
                "template_family": host,
            }
        )
    external_hash_groups = _grouped(external_rows, "registered_external_sha256")
    external_host_groups = defaultdict(list)
    for row in external_rows:
        external_host_groups[row["source_host"]].append(row["id"])
    base_counts = {
        "core": len(load_cases("core")),
        "extended": len(extended),
        "development": len(development),
    }
    source_hashes = [row["source"]["sha256"] for row in owned_rows]
    template_hashes = [row["template"]["sha256"] for row in owned_rows]
    image_hashes = [image["sha256"] for row in owned_rows for image in row["images"]]
    return {
        "schema_version": 1,
        "inventory_kind": "open_development_corpus",
        "case_counts": base_counts,
        "original_external_templates": {
            "case_count": len(external_rows),
            "unique_registered_file_sha256_count": len(
                {row["registered_external_sha256"] for row in external_rows}
            ),
            "cases": external_rows,
            "identical_registered_byte_groups": external_hash_groups,
            "related_by_source_host": [
                {"source_host": host, "case_ids": sorted(ids)}
                for host, ids in sorted(external_host_groups.items())
                if len(ids) > 1
            ],
            "byte_validation": "registered source hashes are reported as metadata; inventory does not fetch external sites or claim local source-byte verification",
        },
        "simplified_replay_analogs": _replay_analog_inventory(extended),
        "owned_development": {
            "revision": owned_cases[0]["reference"]["version"] if owned_cases else None,
            "license": "CC0-1.0; original synthetic fixtures authored for this corpus",
            "case_count": len(owned_rows),
            "independent_source_file_count": len(set(source_hashes)),
            "unique_source_sha256_count": len(set(source_hashes)),
            "unique_template_sha256_count": len(set(template_hashes)),
            "unique_image_sha256_count": len(set(image_hashes)),
            "declared_family_count": len({row["family_id"] for row in owned_rows}),
            "measured_effective_structural_signature_count": effective_count,
            "signature_caveat": "A structural signature is a measurable fixture fingerprint, not a certification of semantic or visual design independence.",
            "signature_method": "For each source slide, hash slide EMU dimensions and ordered top-level shape type/bounds, table dimensions, and chart type. Omit text bytes, names, color, fonts, unused layouts/master shapes, and empty non-placeholder text boxes.",
            "structural_signature_groups": [
                {
                    "signature_sha256": signature,
                    "case_ids": sorted(case_ids),
                }
                for signature, case_ids in sorted(signature_groups.items())
            ],
            "source_group_counts": sorted(row["source_groups"] for row in owned_rows),
            "source_group_count_range": {
                "minimum": min(row["source_groups"] for row in owned_rows),
                "maximum": max(row["source_groups"] for row in owned_rows),
            },
            "target_slide_counts": sorted(row["target_slides"] for row in owned_rows),
            "cases": owned_rows,
            "identical_template_byte_groups": _grouped(
                [{"id": row["id"], "sha256": row["template"]["sha256"]} for row in owned_rows],
                "sha256",
            ),
        },
        "coverage_evidence": {
            "source_coverage_labels": sorted(
                {label for row in owned_rows for label in row["coverage"]}
            ),
            "source_input_modes": sorted({case["input_mode"] for case in owned_cases}),
            "content_inputs": [
                "short brief",
                "long titles and paragraphs",
                "comparisons",
                "timeline",
                "long and wide tables",
                "labeled units and sources",
                "charts",
                "landscape, portrait, and square screenshots",
            ],
            "structural_inputs": [
                "4:3 and widescreen canvases",
                "light and dark masters",
                "layout inheritance and multiple masters",
                "master background graphics",
                "narrow content regions",
                "native tables and charts with embedded workbooks",
                "grouped shapes and linked media resources",
            ],
            "all_declared_features_present": all(
                all(row["declared_feature_checks"].values()) for row in owned_rows
            ),
            "all_sources_and_quotes_verified": all(
                row["source"]["matches_registry"] and row["source"]["all_quotes_source_anchored"]
                for row in owned_rows
            ),
            "challenge_or_private_registered_cases": excluded_registered_inputs,
            "challenge_or_private_inputs_imported": bool(excluded_registered_inputs),
        },
    }


def render_inventory_markdown(document=None):
    """Render a compact, deterministic human-readable inventory summary."""
    document = document or inventory_report()
    owned = document["owned_development"]
    analogs = document["simplified_replay_analogs"]
    originals = document["original_external_templates"]
    lines = [
        "# Open development corpus inventory",
        "",
        f"Suites contain {document['case_counts']['core']} core cases, {document['case_counts']['extended']} extended cases, and {document['case_counts']['development']} development cases.",
        "",
        "## Original external references and replay inputs",
        "",
        f"The original suite registers {originals['case_count']} cases and {originals['unique_registered_file_sha256_count']} distinct external template hashes. Replay uses simplified seeded analogs: {analogs['case_count']} files, {analogs['unique_file_sha256_count']} byte-unique files, and {analogs['broad_profile_count']} aspect/column/theme profiles.",
        "",
        "Exact repeated source hashes and generated replay-analog hashes are listed separately in the JSON inventory. A shared source host means related branding provenance; it does not mean identical template bytes.",
        "",
        "## Owned development fixtures",
        "",
        f"Revision {owned['revision']} contains {owned['case_count']} independently authored source tasks, {owned['unique_template_sha256_count']} distinct native template files, {owned['unique_image_sha256_count']} distinct screenshot assets, {owned['declared_family_count']} declared families, and {owned['measured_effective_structural_signature_count']} measurable source-slide signatures.",
        "",
        f"Structural signatures omit text, names, color, fonts, unused layouts, master graphics, and empty non-placeholder text boxes; they retain canvas size, top-level source-slide shape types and bounds, native table dimensions, and chart types. {owned['signature_caveat']}",
        "",
        "Every fixture is original CC0-licensed synthetic material. Ledgers are silver and unreviewed, with each quote anchored to its task source. The replay analogs do not reproduce WPI reference pages; those pages remain an original-template reference constraint only.",
        "",
        "See `inventory_report()` or `python -m audit_e2e inventory` for per-case IDs, SHA-256 values, feature checks, family/material IDs, source evidence, image dimensions, exact duplicates, and structural signature groups.",
        "",
    ]
    return "\n".join(lines)
