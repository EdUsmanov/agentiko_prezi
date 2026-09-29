"""Visual QA of actual PPTX renders. No model-provided paths or executable fixes."""

import asyncio
import json
import time
import hashlib
import httpx
from typing import Literal
from pydantic import Field
from studio.models import StrictModel
from studio.security import scan_text
from dataclasses import dataclass
from studio.models import JsonObject


@dataclass(frozen=True)
class VisualReviewInput:
    payload: JsonObject
    images: list[bytes]


class VisualFinding(StrictModel):
    slide: int = Field(ge=1, le=30)
    code: Literal[
        "overflow",
        "overlap",
        "title",
        "readability",
        "hierarchy",
        "template_artwork",
        "layout_purpose",
        "duplicate_title",
        "chart_type",
    ]
    severity: Literal["warning", "error"]
    message: str = Field(min_length=1, max_length=600)


class VisualBatch(StrictModel):
    checked_slides: list[int] = Field(min_length=1, max_length=10)
    findings: list[VisualFinding] = Field(max_length=40)


def authored_title_position(scene, patterns):
    selected = patterns.get(scene.get("pattern_id"))
    if not selected or not selected.source_slide:
        return False
    source = patterns.get(f"native-slide-{selected.source_slide}", selected)
    box = source.title_zone
    title = next((e["box"] for e in scene["elements"] if e.get("role") == "title"), None)
    return bool(
        box
        and title
        and abs(title["x"] - box.x) <= 2
        and abs(title["y"] - box.y) <= 2
        and title["w"] <= box.w + 2
        and title["h"] <= box.h + 2
    )


def visual_payload(numbers, scenes, variant, package, directory) -> VisualReviewInput:
    """Build read-only evidence, keeping model output away from filesystem selection."""
    images = [(directory / variant["key"] / f"slide-{i}.png").read_bytes() for i in numbers]
    payload = {
        "slides": [{"slide": i, "title": scenes[i - 1]["title"]} for i in numbers],
        "image_order": numbers,
        "checks": [
            "text inside containers",
            "readable titles",
            "paragraph hierarchy",
            "unintended overlaps",
        ],
    }
    if package:
        from pathlib import Path

        by_id = {p.id: p for p in package.template.patterns}
        refs = []
        for i in numbers:
            pattern = by_id.get(
                scenes[i - 1].get("background_pattern_id") or scenes[i - 1].get("pattern_id")
            )
            # Sample wording in a raw template page was repeatedly
            # mistaken for output text. Compare only sanitized art.
            reference = pattern.background_image if pattern else ""
            if reference and Path(reference).is_file():
                images.append(Path(reference).read_bytes())
                refs.append(
                    {
                        "image_position": len(images),
                        "for_slide": i,
                        "purpose": pattern.purpose,
                        "kind": "sanitized_template_artwork_not_output",
                        "source_slide": pattern.source_slide,
                        "derived_divider": pattern.id.startswith("derived-divider-"),
                    }
                )
        # Show an eligible source exemplar as well as the chosen background.
        # Otherwise a plain master is compared only with itself.
        for i in numbers:
            selected = by_id.get(
                scenes[i - 1].get("background_pattern_id") or scenes[i - 1].get("pattern_id")
            )
            if selected is not None and selected.source_slide:
                continue  # Exact authored reference suffices; alternatives confused image ownership.
            alternatives = [
                p
                for p in package.template.patterns
                if p.reusable
                and p.source_slide
                and p.background_image
                and Path(p.background_image).is_file()
                and p.purpose in (scenes[i - 1].get("purpose"), "content", "unknown")
                and (selected is None or p.id != selected.id)
                and (p.role in ("cover", "divider"))
                == (scenes[i - 1].get("purpose") in ("cover", "divider"))
            ]
            if alternatives:
                exemplar = max(
                    alternatives,
                    key=lambda p: (
                        p.purpose == scenes[i - 1].get("purpose"),
                        p.graphic_count,
                    ),
                )
                images.append(Path(exemplar.background_image).read_bytes())
                refs.append(
                    {
                        "image_position": len(images),
                        "for_slide": i,
                        "purpose": exemplar.purpose,
                        "kind": "alternative_sanitized_style_reference_not_required_layout",
                        "source_slide": exemplar.source_slide,
                    }
                )
        payload["reference_images"] = refs
        payload["slides"] = [
            {
                "slide": i,
                "title": scenes[i - 1]["title"],
                "authored_title_position": authored_title_position(scenes[i - 1], by_id),
                "layout": scenes[i - 1]["layout"],
                "purpose": scenes[i - 1].get("purpose", "auto"),
                "field_safety": by_id[scenes[i - 1]["pattern_id"]].safe_text_zone.get(
                    "field_checks", []
                )
                if scenes[i - 1].get("pattern_id") in by_id
                else [],
                "text_sizes_pt": [
                    e.get("size")
                    for e in scenes[i - 1]["elements"]
                    if e["kind"] in ("text", "table", "chart")
                ],
                "expected_chart_types": [
                    e.get("chart_type") for e in scenes[i - 1]["elements"] if e["kind"] == "chart"
                ],
            }
            for i in numbers
        ]
        from studio.contents.uploads import assign_images

        plan = (
            next(
                (v for v in package.prepared_plans.variants if v.key == variant["key"]),
                None,
            )
            if package.prepared_plans
            else None
        )
        assigned = assign_images(package, plan) if plan else []
        for row in payload["slides"]:
            row["supplied_images"] = (
                [{"id": a.id, "caption": a.caption} for a in assigned[row["slide"] - 1]]
                if assigned
                else []
            )
    return VisualReviewInput(payload, images)


async def review_visuals(results, directory, gateway, timeout, progress=None, package=None):
    total = sum(v["slides"] for v in results)
    report = {
        "status": "not_run",
        "checked": 0,
        "total": total,
        "findings": [],
        "batches": [],
        "model": gateway.settings.model_id,
        "method": "rendered_pptx_images",
    }
    if gateway.settings.mode != "api" or not gateway.settings.visual_review:
        report["reason"] = "Визуальная проверка отключена или модель не подключена"
        return report
    if not all(v["rendering"]["native_render"] for v in results):
        report["reason"] = "Нет подтверждённой отрисовки самого PPTX"
        return report
    started = time.monotonic()
    batch_size = 2 if package else 4  # With a reference: at most four images.
    report["request_adjustments"] = []
    verified_batches = {}
    from studio.checks import review_cache

    # Local slide checks may be reused only with identical pixels, scene, source
    # facts, constraints and template contracts. Global story checks remain separate.
    slide_scope = None
    if package:
        from pathlib import Path

        refs = {
            p.id: hashlib.sha256(Path(p.background_image).read_bytes()).hexdigest()
            for p in package.template.patterns
            if p.background_image and Path(p.background_image).is_file()
        }
        slide_scope = {
            "template": package.template.model_dump(),
            "reference_sha256": refs,
            "source": package.content.model_dump(),
            "constraints": package.constraints.model_dump(),
            "uploaded_images": [a.model_dump() for a in package.images],
        }
    try:
        async with asyncio.timeout(timeout):
            for variant_index, variant in enumerate(results, 1):
                start = 1
                scenes = json.loads((directory / variant["key"] / "slides.json").read_text())
                while start <= variant["slides"]:
                    slots = list(range(start, min(start + batch_size, variant["slides"] + 1)))
                    numbers = list(slots)
                    slide_keys = {}
                    reused = []
                    if slide_scope is not None:
                        numbers = []
                        for i in slots:
                            pixels = (directory / variant["key"] / f"slide-{i}.png").read_bytes()
                            key = review_cache.identity(
                                gateway,
                                "visual_critic",
                                {
                                    "context": slide_scope,
                                    "scene": scenes[i - 1],
                                    "pixels": hashlib.sha256(pixels).hexdigest(),
                                    "slide": i,
                                },
                            )
                            slide_keys[i] = key

                            def validate_one(raw):
                                batch = VisualBatch.model_validate(raw)
                                if batch.checked_slides != [i] or any(
                                    f.slide != i or scan_text(f.message, "visual_review")[1]
                                    for f in batch.findings
                                ):
                                    raise ValueError("Invalid cached visual coverage")
                                return batch

                            cached = review_cache.read(gateway, key, validate_one)
                            if cached is None:
                                numbers.append(i)
                            else:
                                reused.append((i, cached))
                        if not numbers:
                            for i, cached in reused:
                                report["checked"] += 1
                                report["findings"].extend(
                                    {"variant": variant["key"], **f.model_dump()}
                                    for f in cached.findings
                                )
                                report["batches"].append(
                                    {
                                        "variant": variant["key"],
                                        "slides": [i],
                                        "status": "completed",
                                        "cache_hit": True,
                                    }
                                )
                            start = max(slots) + 1
                            continue
                    if progress:
                        progress(
                            f"Рассматриваем вариант {variant_index} из {len(results)}: слайды {numbers[0]}–{numbers[-1]} из {variant['slides']}"
                        )
                    evidence = visual_payload(numbers, scenes, variant, package, directory)
                    payload, images = evidence.payload, evidence.images
                    if package:
                        report["method"] = "rendered_pptx_and_sanitized_template_artwork"
                    image_hashes = [hashlib.sha256(image).hexdigest() for image in images]
                    cache_key = json.dumps(
                        [payload, image_hashes], sort_keys=True, ensure_ascii=False
                    )
                    cache_hit = cache_key in verified_batches
                    try:
                        if cache_hit:
                            raw = verified_batches[cache_key]
                            from studio.diagnostics import event

                            event(
                                "model.cache_hit",
                                stage="visual_critic",
                                reason="identical_render_and_context",
                            )
                        else:
                            raw = await gateway.json_request(
                                "visual_critic",
                                payload,
                                timeout=180
                                if timeout is None
                                else min(45, max(0.01, timeout - (time.monotonic() - started))),
                                schema=VisualBatch.model_json_schema(),
                                images=images,
                            )
                    except httpx.HTTPStatusError as exc:
                        # Retry a rejected attachment batch in smaller pieces, within
                        # the SAME deadline. Never retry authentication failures.
                        if exc.response.status_code not in (400, 413) or len(numbers) == 1:
                            raise
                        batch_size = max(1, len(numbers) // 2)
                        report["request_adjustments"].append(
                            {
                                "http_status": exc.response.status_code,
                                "rejected_images": len(numbers),
                                "next_batch_size": batch_size,
                            }
                        )
                        continue
                    batch = VisualBatch.model_validate(raw)
                    if any(scan_text(f.message, "visual_review")[1] for f in batch.findings):
                        raise ValueError("Instruction-like content in visual audit response")
                    if sorted(batch.checked_slides) != numbers or any(
                        f.slide not in numbers for f in batch.findings
                    ):
                        raise ValueError("Visual report does not cover the supplied images")
                    for i, cached in reused:
                        report["checked"] += 1
                        report["findings"].extend(
                            {"variant": variant["key"], **f.model_dump()} for f in cached.findings
                        )
                        report["batches"].append(
                            {
                                "variant": variant["key"],
                                "slides": [i],
                                "status": "completed",
                                "cache_hit": True,
                            }
                        )
                    verified_batches[cache_key] = batch.model_dump()
                    for i, key in slide_keys.items():
                        if i in numbers:
                            review_cache.write(
                                gateway,
                                key,
                                {
                                    "checked_slides": [i],
                                    "findings": [
                                        f.model_dump() for f in batch.findings if f.slide == i
                                    ],
                                },
                            )
                    report["checked"] += len(numbers)
                    report["batches"].append(
                        {
                            "variant": variant["key"],
                            "slides": numbers,
                            "status": "completed",
                            "image_sha256": image_hashes,
                            "cache_hit": cache_hit,
                        }
                    )
                    report["findings"].extend(
                        {"variant": variant["key"], **f.model_dump()} for f in batch.findings
                    )
                    start = max(slots) + 1
            report["status"] = "completed"
    except Exception as exc:
        report["status"] = "failed"
        report["reason"] = "Визуальная проверка не завершена: " + type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            report["http_status"] = exc.response.status_code
    report["seconds"] = round(time.monotonic() - started, 3)
    (directory / "visual-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report
