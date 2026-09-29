"""Explicit template preanalysis through the same pipeline used by uploaded templates."""

import asyncio
from datetime import datetime, timezone
import shutil
import time
from studio.cache_version import atomic_json
from studio.templates.examples import sources
from studio.preparation.template import load_template, prepare_template_result
from studio.templates.template_cache import TemplateCache


def reference_profile(settings, reference_id):
    reference = next((r for r in sources(settings) if r["id"] == reference_id), None)
    if reference is None:
        raise KeyError("Неизвестный шаблон")
    source = settings.data_dir / "references" / reference_id / "input.pptx"
    cached = TemplateCache(settings).inspect(source)
    if cached is None:
        return {**reference, "status": "not_ready"}
    profile, analysis = cached
    semantic = analysis.get("template_semantics", {})
    return {
        **reference,
        "status": "completed" if semantic.get("status") == "completed" else "technical_only",
        "template": {
            "name": reference["name"],
            "sha256": profile.sha256,
            "slide_count": profile.slide_count,
            "pattern_count": len(profile.patterns),
            "layout_count": profile.layout_count,
            "font": profile.font,
            "colors": profile.colors,
            "title_size": profile.title_size,
            "body_size": profile.body_size,
            "purposes": sorted({p.purpose for p in profile.patterns if p.reusable}),
        },
        "analysis": {
            "template_semantics": {k: semantic[k] for k in ("status", "method") if k in semantic},
            "text_zone_review": {"status": analysis.get("text_zone_review", {}).get("status")},
            "warnings": analysis.get("warnings", []),
        },
    }


async def preanalyze_reference(settings, reference_id, progress=lambda message, percent: None):
    from studio.security_gate import check_template
    from studio.templates.parsing import analyze_template
    from studio.providers.gateway import ModelGateway, validate_model_policy

    reference = next((r for r in sources(settings) if r["id"] == reference_id), None)
    if reference is None:
        raise ValueError("Неизвестный шаблон")
    validate_model_policy(settings)
    root = settings.data_dir / "references" / reference_id
    source = root / "input.pptx"
    check_template(source)
    cache = TemplateCache(settings)
    if cache.inspect(source) is not None:
        return reference_profile(settings, reference_id)
    # Keep real pipeline artifacts and model-call provenance for inspection.
    directory = root / "analysis" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "input.pptx"
    shutil.copyfile(source, path)
    started = time.monotonic()
    gateway = ModelGateway(settings)
    try:
        technical = await asyncio.to_thread(
            load_template,
            path,
            directory,
            settings,
            analyze_template,
            lambda message: progress(message, 25),
        )
        profile = technical.profile
        profile.name = reference["name"]
        if not profile.font_file or any(
            f.get("required_for_generation", True) for f in profile.missing_fonts
        ):
            raise ValueError("Для анализа нужны доступные шрифты шаблона")
        result = await prepare_template_result(path, technical, settings, gateway, progress)
        atomic_json(directory / "analysis.json", result.analysis)
        atomic_json(directory / "profile.json", result.profile.model_dump())
        if not result.analysis["template_cache"].get(
            "saved", result.analysis["template_cache"]["hit"]
        ):
            raise ValueError(
                "Анализ сохранён для диагностики, но не прошёл требования повторного использования"
            )
        return reference_profile(settings, reference_id)
    finally:
        atomic_json(
            directory / "run.json",
            {
                "method": "vk_forma_template_pipeline",
                "model_id": settings.model_id,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "calls": gateway.calls,
            },
        )
        await gateway.aclose()
