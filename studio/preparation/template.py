"""Shared technical, cached, raster, background and semantic template preparation."""

import json
from copy import deepcopy
from pathlib import Path

from studio.config import Settings
from studio.providers.gateway import ModelGateway
from studio.templates.native_template import compile_backgrounds
from studio.security import digest
from studio.security_gate import PromptInjectionDetected, check_template, check_text_fields
from studio.contents.parsing import parse_content
from studio.templates.template_analysis import prepare_template_analysis
from studio.jobs.store import Store
from studio.preparation.contracts import (
    PreparationRequest,
    PreparationServices,
    TemplateAnalysisResult,
    TemplatePreparation,
)


async def analyze_template_only(
    result: TemplateAnalysisResult, path, gateway, progress, cached=None
):
    if cached is not None:
        result.analysis = deepcopy(cached)
        result.analysis["template_cache"] = {"hit": True, "scope": "validated_template_only"}
        progress("Используем проверенный анализ неизменного шаблона", 78)
    else:
        await prepare_template_analysis(result, path, gateway, progress)
        from studio.templates.template_cache import TemplateCache

        cache = TemplateCache(gateway.settings)
        saved = cache.save(result.profile, path.parent, result.analysis)
        result.analysis["template_cache"] = {
            "hit": False,
            "saved": saved,
            "scope": "validated_template_only",
        }
    # The original upload is already held by the job; do not duplicate it in
    # template cache just to retain a native resource source pointer.
    result.profile.resource_source = str(Path(path).resolve())
    return result


def load_template(path, directory, settings, analyze, progress) -> TemplatePreparation:
    """Restore verified template artifacts or perform their technical analysis once."""
    from studio.templates.template_cache import TemplateCache

    cached = TemplateCache(settings).restore(path, directory)
    profile = (
        cached[0]
        if cached
        else analyze(
            path, directory, allow_download=settings.download_fonts, font_progress=progress
        )
    )
    return TemplatePreparation(profile, cached[1] if cached else None)


def prepare_template(
    store: Store,
    job_id: str,
    request: PreparationRequest,
    settings: Settings,
    services: PreparationServices,
    template_only: bool = False,
) -> TemplatePreparation | None:
    directory = store.directory(job_id)
    text, audience, instructions, slides = (
        request.text,
        request.audience,
        request.instructions,
        request.slides,
    )
    content_model, base_constraints = request.content_model, request.base_constraints
    if (
        request.input_mode not in ("content", "brief")
        or request.draft
        and request.input_mode != "brief"
    ):
        raise ValueError("Неизвестный режим подготовки")
    path = directory / "input.pptx"
    if not template_only:
        check_text_fields(
            content=text,
            audience=audience,
            instructions=instructions,
            canonical_content=content_model.model_dump_json() if content_model is not None else "",
            draft=request.draft.model_dump_json() if request.draft is not None else "",
        )
        if content_model is not None and content_model.quarantined:
            raise PromptInjectionDetected(content_model.quarantined)
        from studio.cache_version import atomic_json

        atomic_json(
            directory / "analysis-input.json",
            {
                "text": text,
                "audience": audience,
                "instructions": instructions,
                "slides": slides,
                "content_model": content_model.model_dump() if content_model is not None else None,
                "base_constraints": base_constraints.model_dump()
                if base_constraints is not None
                else None,
                "input_mode": request.input_mode,
                "draft": request.draft.model_dump() if request.draft is not None else None,
                "allowed_fact_ids": request.allowed_fact_ids,
            },
        )
        (directory / "analysis-input.json").chmod(0o600)
    check_template(path)
    technical = load_template(
        path,
        directory,
        settings,
        services.analyze_template,
        lambda phase: store.update(job_id, phase=phase, progress=30),
    )
    template = technical.profile
    if (
        any(f.get("required_for_generation", True) for f in template.missing_fonts)
        or not template.font_file
    ):
        (directory / "technical-profile.json").write_text(template.model_dump_json(indent=2))
        if template_only:
            store.update(
                job_id,
                "waiting_fonts",
                phase="Нужны файлы шрифтов",
                progress=40,
                missing_fonts=[
                    f for f in template.missing_fonts if f.get("required_for_generation", True)
                ],
                template_hash=template.sha256,
                error=(
                    "Добавьте точные TTF в fonts/ или data/local-fonts/. "
                    "После этого отправьте материалы: шаблон будет проверен повторно."
                ),
            )
            return None
        # Preserve technical analysis and canonical input; no LLM or rendering yet.
        content = (
            content_model.model_copy(deep=True)
            if content_model is not None
            else parse_content(text)
        )
        resume = {
            "text": text,
            "audience": audience,
            "instructions": instructions,
            "slides": slides,
            "content_model": content.model_dump(),
            "base_constraints": base_constraints.model_dump()
            if base_constraints is not None
            else None,
            "input_mode": request.input_mode,
            "draft": request.draft.model_dump() if request.draft is not None else None,
            "allowed_fact_ids": request.allowed_fact_ids,
        }
        raw = json.dumps(resume, ensure_ascii=False)
        (directory / "font-resume.json").write_text(raw)
        (directory / "font-resume.json").chmod(0o600)
        store.update(
            job_id,
            "waiting_fonts",
            phase="Нужны файлы шрифтов",
            progress=40,
            missing_fonts=[
                f for f in template.missing_fonts if f.get("required_for_generation", True)
            ],
            resume_hash=digest(raw.encode()),
            template_hash=template.sha256,
            error="Добавьте точные TTF в fonts/ или data/local-fonts/ и нажмите «Проверить шрифты повторно». Анализ шаблона сохранён.",
        )
        return None
    return technical


async def prepare_template_result(
    path: Path,
    technical: TemplatePreparation,
    settings: Settings,
    gateway: ModelGateway,
    progress,
) -> TemplateAnalysisResult:
    """Run the same raster, background, semantic and cache path for both entry points."""
    directory = path.parent
    profile = technical.profile
    if (
        technical.cached_analysis is None
        and settings.mode == "api"
        and settings.visual_review
        and any(pattern.title_zone for pattern in profile.patterns)
    ):
        from studio.checks.raster_review import review_template_rasters

        progress("VL-проверка растровых фонов", 38)
        await review_template_rasters(path, gateway, directory)
    if technical.cached_analysis is None and any(p.title_zone for p in profile.patterns):
        progress("Подготовка исходных макетов и фирменной графики", 40)
        compile_backgrounds(profile, path, directory)
    result = await analyze_template_only(
        TemplateAnalysisResult(profile, {}, {}),
        path,
        gateway,
        progress,
        technical.cached_analysis,
    )
    layers = [p.background_image for p in result.profile.patterns if p.background_image]
    layers.extend(p.reference_image for p in result.profile.patterns if p.reference_image)
    layers.extend(resource.preview_path for resource in result.profile.resources)
    if result.profile.background_source:
        layers.append(result.profile.background_source)
    result.template_layers.update(
        (str(Path(layer).relative_to(directory)), digest(Path(layer).read_bytes()))
        for layer in layers
    )
    return result
