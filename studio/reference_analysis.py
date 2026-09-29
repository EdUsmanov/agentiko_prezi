"""Explicit template preanalysis through the same pipeline used by uploaded templates."""

import asyncio
from datetime import datetime, timezone
import shutil
import time
from .cache_version import atomic_json
from .examples import sources
from .models import PreparedPackage, ContentModel, Constraints
from .template_cache import TemplateCache


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
    from .security_gate import check_template
    from .template import analyze_template
    from .native_template import compile_backgrounds
    from .template_analysis import prepare_template_analysis
    from .gateway import ModelGateway, validate_model_policy

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
        profile = await asyncio.to_thread(
            analyze_template,
            path,
            directory,
            allow_download=settings.download_fonts,
            font_progress=lambda message: progress(message, 25),
        )
        profile.name = reference["name"]
        if not profile.font_file or any(
            f.get("required_for_generation", True) for f in profile.missing_fonts
        ):
            raise ValueError("Для анализа нужны доступные шрифты шаблона")
        if any(p.title_zone for p in profile.patterns):
            await asyncio.to_thread(compile_backgrounds, profile, path, directory)
        package = PreparedPackage(
            id=reference_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            template=profile,
            content=ContentModel(title="", facts=[]),
            constraints=Constraints(),
            manifest={},
        )
        await prepare_template_analysis(package, path, gateway, progress)
        atomic_json(directory / "analysis.json", package.analysis)
        atomic_json(directory / "profile.json", package.template.model_dump())
        if not cache.save(package.template, directory, package.analysis):
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
