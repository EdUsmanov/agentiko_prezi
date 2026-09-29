"""Analysis stages: validate inputs, prepare materials, run intelligence, seal a package."""

import asyncio
from dataclasses import dataclass
from collections.abc import Callable, Awaitable
from datetime import datetime, timezone
from pathlib import Path
import json
import time
from pydantic import ValidationError
from .models import PreparedPackage, TemplateProfile, ContentModel, Constraints, UploadedImage
from .config import Settings
from .gateway import ModelGateway
from .store import Store
from .security import digest, InputRejected
from .security_gate import PromptInjectionDetected, check_text_fields, check_template
from .content import parse_content, parse_constraints
from .fonts import role_font, check_glyphs
from .font_coverage import content_text, ensure_text_coverage
from .native_template import compile_backgrounds
from .composer import compose_variant
from .audit import repair_scenes
from .diversity import ensure_diversity
from .opendesign import export_design, provenance
from .stage_results import JsonObject


@dataclass(frozen=True)
class PreparationRequest:
    text: str
    audience: str
    instructions: str
    slides: int | None
    content_model: ContentModel | None = None
    base_constraints: Constraints | None = None


@dataclass(frozen=True)
class TemplatePreparation:
    profile: TemplateProfile
    cached_analysis: JsonObject | None


@dataclass(frozen=True)
class PreparationServices:
    analyze_template: Callable[..., TemplateProfile]
    prepare_intelligence: Callable[..., Awaitable[PreparedPackage]]
    gateway_factory: Callable[[Settings], ModelGateway]
    versions: Callable[[], dict[str, str]]
    revision: Callable[[], str]


def preparation_diagnostics(template, warnings):
    notices = []
    for message in warnings:
        if message.startswith("Внешняя ссылка исключена"):
            notices.append(
                {
                    "code": "external_link_blocked",
                    "severity": "info",
                    "message": "Внешние ссылки отключены политикой безопасности. Их содержимое не загружалось. Связанные внешние ресурсы, если они были, не попадут в результат.",
                }
            )
        else:
            notices.append(
                {"code": "preparation_warning", "severity": "warning", "message": message}
            )
    return notices


def prepare_template(
    store: Store,
    job_id: str,
    request: PreparationRequest,
    settings: Settings,
    services: PreparationServices,
) -> TemplatePreparation | None:
    directory = store.directory(job_id)
    text, audience, instructions, slides = (
        request.text,
        request.audience,
        request.instructions,
        request.slides,
    )
    content_model, base_constraints = request.content_model, request.base_constraints
    path = directory / "input.pptx"
    check_text_fields(
        content=text,
        audience=audience,
        instructions=instructions,
        canonical_content=content_model.model_dump_json() if content_model is not None else "",
    )
    if content_model is not None and content_model.quarantined:
        raise PromptInjectionDetected(content_model.quarantined)
    from .cache_version import atomic_json

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
        },
    )
    (directory / "analysis-input.json").chmod(0o600)
    check_template(path)
    from .template_cache import TemplateCache

    template_cache = TemplateCache(settings)
    cached_template = template_cache.restore(path, directory)
    template = (
        cached_template[0]
        if cached_template
        else services.analyze_template(
            path,
            directory,
            allow_download=settings.download_fonts,
            font_progress=lambda phase: store.update(job_id, phase=phase, progress=30),
        )
    )
    if (
        any(f.get("required_for_generation", True) for f in template.missing_fonts)
        or not template.font_file
    ):
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
        }
        raw = json.dumps(resume, ensure_ascii=False)
        (directory / "font-resume.json").write_text(raw)
        (directory / "font-resume.json").chmod(0o600)
        (directory / "technical-profile.json").write_text(template.model_dump_json(indent=2))
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
    return TemplatePreparation(template, cached_template[1] if cached_template else None)


def prepare_materials(
    store: Store,
    job_id: str,
    request: PreparationRequest,
    technical: TemplatePreparation,
    services: PreparationServices,
    started: float,
    timings: dict[str, float],
) -> PreparedPackage:
    directory = store.directory(job_id)
    path = directory / "input.pptx"
    template = technical.profile
    text, audience, instructions, slides = (
        request.text,
        request.audience,
        request.instructions,
        request.slides,
    )
    content_model, base_constraints = request.content_model, request.base_constraints
    if technical.cached_analysis is None and any(p.title_zone for p in template.patterns):
        store.update(job_id, phase="Подготовка исходных макетов и фирменной графики", progress=40)
        compile_backgrounds(template, path, directory)
    timings["technical_template_seconds"] = round(time.monotonic() - started, 3)
    # Keep original user-visible filename, never use it as a filesystem path.
    template.name = store.get(job_id).get("template_name", path.name)
    store.update(job_id, phase="Факты, таблицы и ограничения", progress=65)
    # Revision reuses trusted immutable canonical content, not lossy Markdown.
    content = (
        content_model.model_copy(deep=True) if content_model is not None else parse_content(text)
    )
    ensure_text_coverage(template, content_text(content))
    check_glyphs(role_font(template, "title")[1], content.title)
    check_glyphs(
        template.font_file,
        "\n".join(f.text for f in content.facts)
        + "\n"
        + "\n".join(cell for t in content.tables for row in [t.headers] + t.rows for cell in row),
    )
    constraints = parse_constraints(slides, audience, instructions)
    if base_constraints is not None and constraints.count_mode == "default":
        constraints.slides = base_constraints.slides
        constraints.count_mode = base_constraints.count_mode
    if base_constraints is not None:
        constraints.size_preset = base_constraints.size_preset
        constraints.summarize = base_constraints.summarize
        constraints.confirm_plan = base_constraints.confirm_plan
        constraints.include_cover = base_constraints.include_cover
    if not template.font_file:
        raise InputRejected(template.warnings[-1])
    manifest = {
        "template_sha256": template.sha256,
        "content_sha256": digest(
            content.model_dump_json().encode() if content_model is not None else text.encode()
        ),
        "content_hash_format": "canonical_json" if content_model is not None else "original_text",
        "template_layers": {
            str(p.relative_to(directory)): digest(p.read_bytes())
            for p in [
                *[Path(p.background_image) for p in template.patterns if p.background_image],
                *([Path(template.background_source)] if template.background_source else []),
            ]
        },
        "font": {"name": template.font, **template.font_origin},
        "constraints_sha256": digest(constraints.model_dump_json().encode()),
        "versions": services.versions(),
        "git_commit": services.revision(),
        "opendesign": provenance(),
        "analysis_seconds": round(time.monotonic() - started, 3),
        "security": "typed composition operations only; no shell/file/network tools; XML/ZIP validation; no remote assets",
    }
    package = PreparedPackage(
        id=job_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        template=template,
        content=content,
        original_content=content.model_copy(deep=True),
        constraints=constraints,
        manifest=manifest,
        images=[
            UploadedImage.model_validate(a)
            for a in json.loads((directory / "images.json").read_text())
        ]
        if (directory / "images.json").exists()
        else [],
    )
    manifest["uploaded_images"] = [
        {"id": a.id, "sha256": a.sha256, "width": a.width, "height": a.height}
        for a in package.images
    ]
    package.manifest["uploaded_images"] = manifest["uploaded_images"]
    package.manifest["image_security"] = {
        "metadata": "stripped",
        "planning": "pixels_not_sent_to_llm",
        "vision": "read_only_final_audit_only",
        "external_image_urls": False,
    }
    return package


def check_prepared_package(
    store: Store,
    job_id: str,
    package: PreparedPackage,
    gateway: ModelGateway,
    started: float,
    timings: dict[str, float],
) -> None:
    directory = store.directory(job_id)
    checks_started = time.monotonic()
    for pattern in package.template.patterns:
        if pattern.reference_image:
            reference = Path(pattern.reference_image)
            package.manifest["template_layers"][str(reference.relative_to(directory))] = digest(
                reference.read_bytes()
            )
    package.analysis["image_security"] = {
        "accepted": len(package.images),
        "metadata": "removed",
        "ocr": "not_run",
        "pixels_in_planning": False,
        "placement": "server_owned",
        "visual_review": "advisory_only; cannot override deterministic checks",
    }
    # Recheck model-authored labels before any final composition measurement.
    template = package.template
    ensure_text_coverage(template, content_text(package.content, package.prepared_plans))
    package.manifest["font"] = {"name": template.font, **template.font_origin}
    # Check model-authored titles/proposals against the actual selected font too.
    check_glyphs(template.font_file, "\n".join(f.text for f in package.content.facts))
    store.update(job_id, phase="Проверка композиций и фиксация пакета", progress=95)
    if package.prepared_plans:
        check_glyphs(
            role_font(template, "title")[1],
            "\n".join(s.title for v in package.prepared_plans.variants for s in v.slides),
        )
        decks = {v.key: compose_variant(v, package) for v in package.prepared_plans.variants}
        for scenes in decks.values():
            repair_scenes(scenes, package)
        package.analysis["composition_preview"] = ensure_diversity(decks, package)
    else:
        package.analysis["composition_preview"] = {
            "verified": False,
            "findings": [],
            "reason": "slide_budget_requires_input",
        }
    package.analysis["model_calls"] = gateway.calls
    package.analysis["model_usage"] = gateway.usage
    package.analysis["induction_errors"] = getattr(gateway, "induction_errors", [])
    package.analysis["warnings"].extend(
        f["message"] for f in package.analysis["composition_preview"]["findings"]
    )
    from .font_disclosure import preparation_substitutions, warnings as font_warnings

    package.manifest["font_substitutions"] = preparation_substitutions(package)
    package.analysis["font_substitutions"] = package.manifest["font_substitutions"]
    package.analysis["warnings"].extend(
        font_warnings(package.manifest["font_substitutions"], planned=True)
    )
    package.manifest["analysis_seconds"] = round(time.monotonic() - started, 3)
    export_design(template, directory)
    from .diagnostics import stage_summary

    timings["local_final_checks_seconds"] = round(time.monotonic() - checks_started, 3)
    package.analysis["timings"] = {**timings, "model": stage_summary(gateway.calls)}


def seal_preparation(store: Store, job_id: str, package: PreparedPackage) -> None:
    directory = store.directory(job_id)
    template, constraints = package.template, package.constraints
    content = package.original_content or package.content
    report = package.analysis
    (directory / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    raw = package.model_dump_json(indent=2)
    (directory / "package.json").write_text(raw)
    warnings = template.warnings + package.content.warnings + package.analysis["warnings"]
    store.update(
        job_id,
        "ready",
        phase="Готово к генерации",
        progress=100,
        package_hash=digest(raw.encode()),
        auto_generation="manual",
        template=template.model_dump(
            exclude={"font_file", "assets", "font_assets", "background_source"}
        )
        | {
            "font_assets": [
                {k: v for k, v in a.items() if k != "path"} for a in template.font_assets
            ]
        },
        content={
            "title": package.content.title,
            "facts": len(package.content.facts),
            "tables": len(package.content.tables),
            "images": len(package.images),
        },
        constraints=constraints.model_dump(),
        warnings=warnings,
        analysis=report,
        diagnostics=preparation_diagnostics(template, warnings),
        quarantine=content.quarantined,
        analysis_seconds=package.manifest["analysis_seconds"],
    )


def record_preparation_failure(
    store: Store,
    job_id: str,
    package: PreparedPackage | None,
    gateway: ModelGateway | None,
    started: float,
    exc: Exception,
) -> None:
    directory = store.directory(job_id)
    from .diagnostics import exception

    exception("analysis.failed", exc)
    message = (
        str(exc)
        if isinstance(exc, (ValueError, InputRejected)) and not isinstance(exc, ValidationError)
        else "Ошибка анализа (" + type(exc).__name__ + ")"
    )
    diagnostic = {
        "phase": store.get(job_id).get("phase"),
        "error_type": type(exc).__name__,
        "model_calls": getattr(gateway, "calls", []),
        "induction_errors": getattr(gateway, "induction_errors", []),
        "template_semantics": package.analysis.get("template_semantics") if package else None,
        "editorial_repair": package.analysis.get("editorial_repair_diagnostics")
        if package
        else None,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    from .cache_version import atomic_json

    from .repair_errors import RepairFailure

    if isinstance(exc, RepairFailure):
        diagnostic["repair_issues"] = exc.public()
    atomic_json(directory / "failure-report.json", diagnostic)
    (directory / "failure-report.json").chmod(0o600)
    from .induction import InductionFailure

    if isinstance(exc, InductionFailure):
        stages = {
            "editorial": "составления плана слайдов",
            "editorial_outline": "выбора структуры презентации",
            "editorial_repair": "исправления конкретных слайдов",
            "editorial_slides": "подготовки содержания слайдов",
            "editorial_review": "проверки смысла",
            "template_analyst": "анализа шаблона",
        }
        stage = getattr(exc, "stage", "")
        label = stages.get(stage, stage or "анализа")
        reason = (
            "ответ модели превысил допустимый размер"
            if str(exc) == "ModelResponseTruncated"
            else "модель не завершила запрос за отведённое время"
            if str(exc) == "TimeoutError"
            else "не удалось соединиться с провайдером модели после повторных попыток"
            if str(exc)
            in (
                "ConnectError",
                "ConnectTimeout",
                "ReadError",
                "WriteError",
                "RemoteProtocolError",
            )
            else "модель не вернула проверяемый ответ"
        )
        message = f"На этапе {label} {reason}. Проверенные результаты сохранены; повторный анализ использует их."
    else:
        message += f" Этап: {diagnostic['phase']}; причина: {type(exc).__name__}."
    store.update(
        job_id,
        "failed",
        error=message,
        phase="Анализ остановлен",
        failure_diagnostics=diagnostic,
    )


def run_preparation(
    store: Store,
    job_id: str,
    request: PreparationRequest,
    settings: Settings,
    services: PreparationServices,
) -> None:
    started = time.monotonic()
    timings: dict[str, float] = {}
    package = None
    gateway = None
    store.update(job_id, "running", phase="Разбор PPTX и дизайн-системы", progress=15)
    try:
        technical = prepare_template(store, job_id, request, settings, services)
        if technical is None:
            return
        gateway = services.gateway_factory(settings)
        if (
            technical.cached_analysis is None
            and settings.mode == "api"
            and settings.visual_review
            and any(pattern.title_zone for pattern in technical.profile.patterns)
        ):
            from .raster_review import review_template_rasters

            store.update(job_id, phase="VL-проверка растровых фонов", progress=38)

            async def review_backgrounds():
                try:
                    await review_template_rasters(
                        store.directory(job_id) / "input.pptx", gateway, store.directory(job_id)
                    )
                finally:
                    await gateway.aclose()

            asyncio.run(review_backgrounds())
        package = prepare_materials(store, job_id, request, technical, services, started, timings)
        if technical.cached_analysis is not None:
            package.analysis["_template_snapshot"] = technical.cached_analysis

        async def analyze() -> PreparedPackage:
            try:
                return await services.prepare_intelligence(
                    package,
                    store.directory(job_id) / "input.pptx",
                    gateway,
                    lambda phase, value: store.update(job_id, phase=phase, progress=value),
                )
            finally:
                if hasattr(gateway, "aclose"):
                    await gateway.aclose()

        analysis_started = time.monotonic()
        package = asyncio.run(analyze())
        timings["intelligence_seconds"] = round(time.monotonic() - analysis_started, 3)
        check_prepared_package(store, job_id, package, gateway, started, timings)
        seal_preparation(store, job_id, package)
    except PromptInjectionDetected as exc:
        store.update(
            job_id,
            "failed",
            error=str(exc),
            phase="Материалы заблокированы проверкой безопасности",
            security_violation=exc.public(),
            quarantine=exc.findings,
        )
    except Exception as exc:
        record_preparation_failure(store, job_id, package, gateway, started, exc)
