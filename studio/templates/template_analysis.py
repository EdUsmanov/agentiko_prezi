"""Preparation-only template induction with optional actual-slide observations."""

import re
from typing import Literal
from pydantic import Field
from studio.composition.powerpoint import open_presentation
from studio.composition.pictures import is_picture, embedded_picture_blob
from studio.models import StrictModel
from studio.security import digest, scan_text
from studio.templates.template_geometry import walk_shapes
from studio.templates.archetype_catalog import Archetype, catalog_payload


class PatternMeaning(StrictModel):
    pattern_id: str
    roles: list[Literal["context", "insight", "evidence", "action", "appendix"]] = Field(
        min_length=1, max_length=5
    )
    density: Literal["low", "medium", "high"]
    purpose: Archetype | Literal["unknown", "service", "reference"] = "unknown"
    reusable: bool = True
    body_order: list[int] = Field(default_factory=list, max_length=12)
    graphic_flow_confirmed: bool = False
    graphic_kind: Literal[
        "none", "cards", "sequence", "comparison", "hierarchy", "radial", "pyramid", "matrix"
    ] = "none"
    graphic_shape_ids: list[int] = Field(default_factory=list, max_length=160)
    graphic_edges: list[tuple[int, int]] = Field(default_factory=list, max_length=160)


class TemplateMeaning(StrictModel):
    patterns: list[PatternMeaning] = Field(max_length=24)


def normalize_meanings(raw):
    """Collapse identical repeated observations; conflicting classifications fail."""
    parsed = TemplateMeaning.model_validate(raw)
    unique = {}
    for meaning in parsed.patterns:
        previous = unique.get(meaning.pattern_id)
        if previous is not None and previous != meaning:
            raise ValueError("Conflicting duplicate pattern classifications")
        unique[meaning.pattern_id] = meaning
    parsed.patterns = list(unique.values())
    for meaning in parsed.patterns:
        # An explicit refusal to confirm a relation must never enable its reuse.
        # Models sometimes still describe the pictured kind/IDs in that branch.
        # Those optional observations are not a composition contract.
        if not meaning.graphic_flow_confirmed:
            if (
                meaning.graphic_kind != "none"
                or meaning.graphic_shape_ids
                or meaning.body_order
                or meaning.graphic_edges
            ):
                from studio.diagnostics import event

                event("template.graphic_reuse_declined", pattern_id=meaning.pattern_id)
            meaning.graphic_kind = "none"
            meaning.graphic_shape_ids = []
            meaning.body_order = []
            meaning.graphic_edges = []
    return parsed


def reference_page_evidence(sample: str) -> str | None:
    """Identify authored style-guide pages from several independent text clues."""
    lines = [line.strip().casefold() for line in sample.splitlines() if line.strip()]
    if not lines:
        return None
    title = lines[0]
    text = "\n".join(lines)
    style_labels = (
        "theme fonts",
        "text box default",
        "drawing default styles",
        "line default style",
    )
    if (
        title == "default settings"
        and sum(label in text for label in style_labels) >= 2
        and len(re.findall(r"\bsample\b", text)) >= 3
    ):
        return "style_defaults_with_samples"
    rgb_values = re.findall(r"\br\s*\d{1,3}\s+g\s*\d{1,3}\s+b\s*\d{1,3}\b", text)
    if (
        title
        in ("color scheme", "colour scheme", "brand palette", "color palette", "colour palette")
        and len(rgb_values) >= 3
        and len(re.findall(r"\baccent\s*\d+\b", text)) >= 2
    ):
        return "palette_with_rgb_swatches"
    return None


def exclude_reference_pages(profile, inventory):
    evidence = {
        slide["slide"]: reason
        for slide in inventory["slides"]
        if (reason := reference_page_evidence(slide["sample"]))
    }
    excluded = []
    for pattern in profile.patterns:
        reason = evidence.get(pattern.source_slide)
        if reason:
            pattern.purpose = "reference"
            pattern.reusable = False
            excluded.append(
                {"pattern_id": pattern.id, "source_slide": pattern.source_slide, "evidence": reason}
            )
    return excluded


def template_inventory(path, profile):
    """All slide objects are inventoried; only bounded, sanitized samples go to LLM."""
    from studio.templates.colors import pattern_color_context

    prs = open_presentation(path)
    slides = []
    for index, slide in enumerate(prs.slides, 1):
        shapes = list(walk_shapes(slide.shapes))
        samples = []
        quarantined = 0
        for shape, _ in shapes:
            if shape.has_text_frame:
                clean, found = scan_text(shape.text, "template")
                quarantined += len(found)
                if clean.strip():
                    samples.append(clean.strip())
        slides.append(
            {
                "slide": index,
                "objects": len(shapes),
                "text_blocks": sum(s.has_text_frame for s, _ in shapes),
                "tables": sum(s.has_table for s, _ in shapes),
                "pictures": sum(is_picture(s) for s, _ in shapes),
                "pictures_without_embedded_data": sum(
                    is_picture(s) and embedded_picture_blob(s) is None for s, _ in shapes
                ),
                "quarantined_lines": quarantined,
                "sample": "\n".join(samples)[:2400],
            }
        )

    def graphic_candidates(pattern):
        if not pattern.source_slide:
            return []
        from studio.templates.native_template import P, A

        candidates = []
        for shape, bounds in walk_shapes(prs.slides[pattern.source_slide - 1].shapes):
            if shape._element.tag not in (P + "sp", P + "cxnSp"):
                continue
            # Plain text boxes are fields, not artwork. Keep vector fills/paths.
            geometry = shape._element.find(".//" + A + "prstGeom")
            custom = shape._element.find(".//" + A + "custGeom")
            if geometry is None and custom is None and shape._element.tag != P + "cxnSp":
                continue
            candidates.append(
                {
                    "id": shape.shape_id,
                    "geometry": geometry.get("prst") if geometry is not None else "path",
                    "box": [round(v, 1) for v in (bounds.x, bounds.y, bounds.w, bounds.h)],
                }
            )
        return candidates[:160]

    patterns = [
        {
            "id": p.id,
            "source_slide": p.source_slide,
            "source_layout": scan_text(p.source_layout, "template")[0][:120],
            "title_zone": p.title_zone.model_dump() if p.title_zone else None,
            "body_zones": [b.model_dump() for b in p.body_zones],
            "technical_role": p.role,
            "colors": pattern_color_context(p),
            "graphic_candidates": graphic_candidates(p),
            "body_fields": [
                {"shape_id": f["shape_id"], "index": f["index"], "box": f["box"]}
                for f in p.fields
                if f["role"] == "body"
            ],
            "source_sample": slides[p.source_slide - 1]["sample"] if p.source_slide else "",
        }
        for p in profile.patterns
    ]
    return {
        "slides": slides,
        "patterns": patterns,
        "width": profile.width,
        "height": profile.height,
        "fonts": profile.fonts,
        "palette": profile.colors,
    }


def reference_images(path, profile, directory):
    """Server-owned paths only. Render source once, never execute slide instructions."""
    from pathlib import Path
    from studio.composition.office import to_pdf
    from studio.templates.fonts import profile_font_files
    from studio.composition.render import _pdfium_lock
    import pypdfium2 as pdfium

    folder = Path(directory) / "source-review"
    folder.mkdir(parents=True, exist_ok=True)
    # Never feed live external relationships to Office while rendering references.
    prs = open_presentation(path)
    for part in list(prs.part.package.iter_parts()):
        external = {r.rId for r in part.rels.values() if r.is_external}
        root = getattr(part, "_element", None)
        if root is not None:
            for node in list(root.iter()):
                for attr, value in list(node.attrib.items()):
                    if (
                        attr.startswith(
                            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
                        )
                        and value in external
                    ):
                        del node.attrib[attr]
        for rid in external:
            part.drop_rel(rid)
    safe_source = folder / "source.pptx"
    prs.save(safe_source)
    if not to_pdf(safe_source, folder, timeout=120, font_files=profile_font_files(profile)):
        return {}
    images = {}
    with _pdfium_lock, pdfium.PdfDocument(str(folder / "source.pdf")) as doc:
        for number in sorted({p.source_slide for p in profile.patterns if p.source_slide}):
            page = doc[number - 1]
            bitmap = page.render(scale=1)
            target = folder / f"source-{number}.png"
            bitmap.to_pil().save(target)
            bitmap.close()
            page.close()
            images[number] = target.read_bytes()
            for pattern in profile.patterns:
                if pattern.source_slide == number:
                    pattern.reference_image = str(target)
    return images


async def analyze_meaning(inventory, gateway, images=None, progress=None):
    if gateway.settings.mode != "api":
        return {"status": "not_run", "method": "text_and_geometry", "patterns": []}
    meanings = []
    images = images or {}
    method = "text_geometry_and_source_images" if images else "text_and_geometry"
    from studio.providers.induction import validated_request, InductionFailure

    failed_ids = []
    failures = []
    consecutive_provider_failures = 0
    stopped = None
    total = len(inventory["patterns"])

    async def classify(batch):
        nonlocal consecutive_provider_failures
        if progress:
            progress(
                f"Проверено макетов {len(meanings) + len(failed_ids)} из {total}. Обрабатываем: {', '.join(p['id'] for p in batch)}"
            )
        schema = TemplateMeaning.model_json_schema()
        ids = [p["id"] for p in batch]
        schema["$defs"]["PatternMeaning"]["properties"]["pattern_id"] = {
            "type": "string",
            "enum": ids,
        }
        schema["properties"]["patterns"].update(minItems=len(batch), maxItems=len(batch))
        if len(batch) == 1:
            schema = PatternMeaning.model_json_schema()
            schema["properties"]["pattern_id"] = {"type": "string", "enum": ids}
        numbers = list(
            dict.fromkeys(p["source_slide"] for p in batch if p["source_slide"] in images)
        )
        payload = {
            "patterns": batch,
            "canvas": {"width": inventory["width"], "height": inventory["height"]},
            "image_order": numbers,
            "archetype_catalog": catalog_payload(),
        }

        def validate(raw):
            if len(batch) == 1 and isinstance(raw, dict) and "pattern_id" in raw:
                raw = {"patterns": [raw]}
            parsed = normalize_meanings(raw)
            returned = [p.pattern_id for p in parsed.patterns]
            if len(returned) != len(set(returned)) or set(returned) != set(ids):
                raise ValueError("Pattern coverage mismatch")
            inventory_by_id = {p["id"]: p for p in batch}
            for meaning in parsed.patterns:
                expected = {
                    f["shape_id"]
                    for f in inventory_by_id[meaning.pattern_id].get("body_fields", [])
                }
                if (
                    meaning.graphic_flow_confirmed
                    or meaning.body_order
                    or meaning.graphic_kind != "none"
                ):
                    if (
                        len(meaning.body_order) != len(expected)
                        or set(meaning.body_order) != expected
                    ):
                        raise ValueError("Graphic order must cover every body field exactly once")
                for parent, child in meaning.graphic_edges:
                    if parent not in expected or child not in expected or parent == child:
                        raise ValueError("Graphic edge must connect two actual body fields")
                if (
                    meaning.graphic_kind in ("hierarchy", "pyramid", "radial")
                    and not meaning.graphic_edges
                ):
                    raise ValueError("Structural compositions require explicit field relationships")
                candidates = {
                    v["id"]
                    for v in inventory_by_id[meaning.pattern_id].get("graphic_candidates", [])
                }
                if not set(meaning.graphic_shape_ids) <= candidates:
                    raise ValueError("Unknown or non-vector graphic shape")
                if meaning.graphic_kind != "none" and (
                    not meaning.graphic_flow_confirmed or not meaning.graphic_shape_ids
                ):
                    raise ValueError(
                        "A reusable graphical composition needs verified field binding and vector IDs"
                    )
            return parsed.model_dump()

        try:
            previous_calls = len(getattr(gateway, "calls", []))
            result = await validated_request(
                gateway,
                "template_analyst",
                payload,
                schema,
                validate,
                timeout=180,
                images=[images[n] for n in numbers],
                progress=(lambda message: progress(f"Макеты {', '.join(ids)}: {message}"))
                if progress
                else None,
                split_group=len(batch) > 1,
            )
            meanings.extend(result["patterns"])
            calls = getattr(gateway, "calls", [])
            cached = (
                len(calls) > previous_calls
                and isinstance(calls[-1], dict)
                and calls[-1].get("cache_hit")
            )
            if not cached:
                consecutive_provider_failures = 0
            if progress:
                progress(f"Проверено макетов {len(meanings) + len(failed_ids)} из {total}")
        except InductionFailure as exc:
            if exc.fatal:
                raise
            if len(batch) > 1:
                if progress:
                    progress("Уточняем проблемную группу: проверяем каждый макет отдельно")
                for pattern in batch:
                    await classify([pattern])
            else:
                failed_ids.extend(ids)
                failures.append({"pattern_id": ids[0], "error_type": str(exc)})
                consecutive_provider_failures = (
                    consecutive_provider_failures + 1 if exc.provider_failure else 0
                )
                if consecutive_provider_failures >= 2:
                    # Stop an unavailable provider, not the wall clock of analysis.
                    # Successful blocks remain cached for an explicit user retry.
                    raise InductionFailure("ProviderUnavailable", provider_failure=True, fatal=True)

    try:
        # Bounded batches avoid truncating a large template silently. No total
        # preparation deadline, but each network operation still has a timeout.
        batches = []
        offset = 0
        patterns = inventory["patterns"]
        while offset < len(patterns):
            has_image = patterns[offset]["source_slide"] in images
            # Text-only layouts can also exhaust the answer budget through
            # reasoning. Keep their groups small, not the schema maximum of 24.
            size = 4
            end = offset + 1
            while (
                end < min(len(patterns), offset + size)
                and (patterns[end]["source_slide"] in images) == has_image
            ):
                # One source image per request. Related patterns sharing the same
                # image stay together; four unrelated full-slide images timed out.
                if has_image and patterns[end]["source_slide"] != patterns[offset]["source_slide"]:
                    break
                end += 1
            batch = patterns[offset:end]
            batches.append(batch)
            offset = end
        # Do not enqueue the entire deck into the shared one-request provider
        # gate: later batches otherwise report 20-minute "calls" mostly spent
        # waiting, and errors fan out before the user sees any useful progress.
        for batch in batches:
            try:
                await classify(batch)
            except InductionFailure as exc:
                stopped = str(exc)
                break
        if stopped:
            verified = {m["pattern_id"] for m in meanings}
            failed_ids = [p["id"] for p in patterns if p["id"] not in verified]
            return {
                "status": "failed",
                "method": method,
                "patterns": meanings,
                "excluded_pattern_ids": failed_ids,
                "failures": failures,
                "error_type": stopped,
                "message": "Модель не отвечает или отклоняет запросы. Анализ остановлен без дальнейших автоматических повторов; успешные блоки сохранены. Повторите анализ после восстановления API.",
            }
        order = {pattern["id"]: i for i, pattern in enumerate(inventory["patterns"])}
        meanings.sort(key=lambda meaning: order[meaning["pattern_id"]])
        return {
            "status": "partial"
            if failed_ids and meanings
            else "failed"
            if failed_ids
            else "completed",
            "method": method,
            "patterns": meanings,
            "excluded_pattern_ids": failed_ids,
            "failures": failures,
        }
    except Exception as exc:
        # Partial classification must not silently drive composition.
        return {
            "status": "failed",
            "method": method,
            "patterns": [],
            "error_type": type(exc).__name__,
        }


async def prepare_template_analysis(result, path, gateway, progress):
    from studio.checks.text_zone_review import review_text_zones

    inventory = template_inventory(path, result.profile)
    progress("Определяем назначение слайдов по содержанию и изображениям шаблона", 68)
    images = (
        reference_images(path, result.profile, path.parent)
        if getattr(gateway.settings, "visual_review", False) and gateway.settings.mode == "api"
        else {}
    )
    semantics = await analyze_meaning(
        inventory, gateway, images, lambda message: progress(message, 68)
    )
    from studio.templates.template_resources import discover_resources

    try:
        result.profile.resources, resource_report = await discover_resources(
            path, result.profile, gateway, images, reference_images
        )
    except (OSError, ValueError) as exc:
        result.profile.resources = []
        resource_report = {"status": "degraded", "error_type": type(exc).__name__}
    from studio.composition.contracts import apply_meanings

    apply_meanings(result.profile, semantics)
    reference_exclusions = exclude_reference_pages(result.profile, inventory)
    graphic_patterns = [
        p
        for p in result.profile.patterns
        if p.reusable
        and p.source_slide
        and p.graphic_order_verified
        and p.graphic_kind != "none"
        and len(p.body_zones) >= 2
    ]
    if graphic_patterns:
        from studio.templates.native_template import compile_backgrounds

        progress("Сохраняем графические композиции и привязки полей шаблона", 76)
        compile_backgrounds(result.profile, path, path.parent)
        for layer in [
            *[p.background_image for p in result.profile.patterns if p.background_image],
            *([result.profile.background_source] if result.profile.background_source else []),
        ]:
            from pathlib import Path

            file = Path(layer)
            result.template_layers[str(file.relative_to(path.parent))] = digest(file.read_bytes())
    zone_review = await review_text_zones(
        result.profile, path.parent, gateway, lambda message: progress(message, 78)
    )
    verified = {m["pattern_id"] for m in semantics.get("patterns", [])}
    if semantics["status"] in ("partial", "failed"):
        for pattern in result.profile.patterns:
            if pattern.id not in verified:
                pattern.reusable = False
    result.analysis = {
        "version": 2,
        "model_mode": gateway.settings.mode,
        "model_id": gateway.settings.model_id or None,
        "technical": {
            "status": "completed",
            "slides": len(inventory["slides"]),
            "objects": sum(s["objects"] for s in inventory["slides"]),
            "quarantined_template_lines": sum(s["quarantined_lines"] for s in inventory["slides"]),
        },
        "template_semantics": semantics,
        "template_reference_pages": {"excluded_patterns": reference_exclusions},
        "visual_model_review": {"status": "not_run"},
        "template_resources": resource_report,
        "native_render": {
            "patterns": sum(bool(p.background_image) for p in result.profile.patterns)
        },
        "warnings": [],
    }
    background_report = path.parent / "background-model.json"
    if background_report.is_file():
        import json

        result.analysis["raster_review"] = json.loads(background_report.read_text()).get(
            "rasterReview", {"status": "not_run"}
        )
    result.analysis["template_graphics"] = {
        "reused_patterns": [p.id for p in graphic_patterns],
        "method": "editable_vectors_without_sample_text",
    }
    result.analysis["text_zone_review"] = zone_review
    if zone_review["status"] != "completed":
        result.analysis["warnings"].append(
            "Безопасность части текстовых полей не подтверждена; требуется проверка фона."
        )
    if semantics["status"] in ("partial", "failed"):
        result.analysis["warnings"].append(
            f"Не проверены и исключены из выбора {len(result.profile.patterns) - len(verified)} макетов. "
            "Используются только проверенные макеты; результат требует проверки."
        )
        # Keep excluded geometry out of all downstream fallback/Design candidates.
        result.profile.patterns = [p for p in result.profile.patterns if p.id in verified]
    if semantics["status"] == "failed" or (
        semantics["status"] == "partial"
        and not any(
            p.reusable
            and p.title_zone
            and p.body_zones
            and p.purpose not in ("cover", "divider", "service", "reference")
            for p in result.profile.patterns
        )
    ):
        raise ValueError(
            semantics.get("message")
            or "Не удалось проверить достаточно макетов для генерации. Успешные блоки анализа сохранены; повторите анализ. Генерация по непроверенному каталогу отключена."
        )
    from studio.templates.template_adaptation import (
        derive_data_patterns,
        adapt_native_text_fields,
        derive_numbered_timelines,
        derive_roomy_text_patterns,
        derive_safe_cover_patterns,
    )

    result.analysis["template_cover_adaptations"] = derive_safe_cover_patterns(result.profile)
    result.analysis["template_text_adaptations"] = adapt_native_text_fields(result.profile)
    result.analysis["template_timeline_adaptations"] = derive_numbered_timelines(
        result.profile, path
    )
    result.analysis["template_data_regions"] = derive_data_patterns(result.profile)
    result.analysis["template_roomy_regions"] = derive_roomy_text_patterns(result.profile)
    return result
