"""Analysis stages: validate inputs, prepare materials, run intelligence, seal a package."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import json
import time
from pydantic import ValidationError
from ..models import PreparedPackage, UploadedImage
from ..config import Settings
from studio.providers.gateway import ModelGateway
from studio.jobs.store import Store
from ..security import digest, InputRejected
from ..security_gate import PromptInjectionDetected
from studio.contents.parsing import insufficient_material, parse_content, parse_constraints
from studio.templates.fonts import role_font, check_glyphs
from studio.templates.font_coverage import content_text, ensure_text_coverage
from studio.composition.composer import compose_variant
from studio.checks.audit import repair_scenes
from studio.checks.diversity import ensure_diversity
from studio.templates.opendesign import export_design, provenance


from .contracts import (
    PreparationRequest,
    PreparationServices,
    TemplateAnalysisResult,
    TemplatePreparation,
)
from .template import prepare_template, prepare_template_result


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
    if request.input_mode == "brief":
        constraints.summarize = True
        constraints.confirm_plan = True
    elif message := insufficient_material(content, constraints):
        raise InputRejected(message)
    if not template.font_file:
        raise InputRejected(template.warnings[-1])
    manifest = {
        "template_sha256": template.sha256,
        "content_sha256": digest(
            content.model_dump_json().encode() if content_model is not None else text.encode()
        ),
        "content_hash_format": "canonical_json" if content_model is not None else "original_text",
        "template_layers": {},
        "font": {"name": template.font, **template.font_origin},
        "constraints_sha256": digest(constraints.model_dump_json().encode()),
        "versions": services.versions(),
        "git_commit": services.revision(),
        "opendesign": provenance(),
        "analysis_seconds": round(time.monotonic() - started, 3),
        "security": "typed composition operations only; no shell/file/network tools; XML/ZIP validation; no remote assets",
    }
    original = content.model_copy(deep=True)
    if request.draft is not None:
        from studio.contents.brief import apply_edited_draft, draft_hash

        content, revision_provenance = apply_edited_draft(
            original, request.draft, allowed_fact_ids=set(request.allowed_fact_ids or [])
        )
        manifest["brief_revision_provenance"] = revision_provenance
        manifest["submitted_draft_hash"] = draft_hash(request.draft)
    package = PreparedPackage(
        id=job_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        template=template,
        content=content,
        original_content=original,
        input_mode=request.input_mode,
        draft=request.draft,
        brief_evidence=content.model_copy(deep=True) if request.draft is not None else None,
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
    from studio.templates.font_disclosure import (
        preparation_substitutions,
        warnings as font_warnings,
    )

    package.manifest["font_substitutions"] = preparation_substitutions(package)
    package.analysis["font_substitutions"] = package.manifest["font_substitutions"]
    package.analysis["warnings"].extend(
        font_warnings(package.manifest["font_substitutions"], planned=True)
    )
    package.manifest["analysis_seconds"] = round(time.monotonic() - started, 3)
    export_design(template, directory)
    from ..diagnostics import stage_summary

    timings["local_final_checks_seconds"] = round(time.monotonic() - checks_started, 3)
    package.analysis["timings"] = {**timings, "model": stage_summary(gateway.calls)}


def seal_preparation(store: Store, job_id: str, package: PreparedPackage) -> None:
    directory = store.directory(job_id)
    template, constraints = package.template, package.constraints
    content = package.original_content or package.content
    report = {
        **package.analysis,
        **package.control.model_dump(mode="json"),
    }
    (directory / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    raw = package.model_dump_json(indent=2)
    from studio.contents.brief import assert_draft_matches_package, draft_hash

    if package.input_mode == "brief":
        assert_draft_matches_package(package)
    review_hash = (
        draft_hash(package.draft) if package.input_mode == "brief" and package.draft else None
    )
    (directory / "package.json").write_text(raw)
    warnings = template.warnings + package.content.warnings + package.analysis["warnings"]
    store.update(
        job_id,
        "ready",
        phase="Готово к генерации",
        progress=100,
        package_hash=digest(raw.encode()),
        auto_generation="needs_confirmation"
        if package.input_mode == "brief"
        or (report.get("slide_budget") or {}).get("status") == "needs_input"
        or (
            not constraints.confirm_plan
            and (report.get("slide_budget") or {}).get("status") == "adjusted"
        )
        else "scheduled",
        auto_generate_at=time.time() + 60,
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
        input_mode=package.input_mode,
        draft_hash=review_hash,
        control=package.control.model_dump(mode="json"),
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
    from ..diagnostics import exception

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
    from ..cache_version import atomic_json

    from studio.checks.repair_errors import RepairFailure

    if isinstance(exc, RepairFailure):
        diagnostic["repair_issues"] = exc.public()
    atomic_json(directory / "failure-report.json", diagnostic)
    (directory / "failure-report.json").chmod(0o600)
    from studio.providers.induction import InductionFailure

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
        timings["technical_template_seconds"] = round(time.monotonic() - started, 3)
        package = prepare_materials(store, job_id, request, technical, services, started, timings)
        package.manifest["execution_kind"] = settings.execution_kind
        gateway = services.gateway_factory(settings)

        async def analyze() -> PreparedPackage:
            nonlocal package
            try:
                template_started = time.monotonic()
                template_result = await prepare_template_result(
                    store, job_id, technical, settings, gateway
                )
                template_seconds = round(time.monotonic() - template_started, 3)
                timings["template_seconds"] = template_seconds
                package.template = template_result.profile
                package.analysis = {
                    k: v for k, v in template_result.analysis.items() if k != "model_mode"
                }
                package.control.model_mode = template_result.analysis.get(
                    "model_mode", settings.mode
                )
                package.manifest["template_layers"].update(template_result.template_layers)
                analysis_started = time.monotonic()
                package = await services.prepare_intelligence(
                    package,
                    store.directory(job_id) / "input.pptx",
                    gateway,
                    lambda phase, value: store.update(job_id, phase=phase, progress=value),
                    template_result,
                )
                if "intelligence_timings" in package.analysis:
                    package.analysis["intelligence_timings"]["template_seconds"] = template_seconds
                if "seconds" in package.analysis:
                    package.analysis["seconds"] = round(
                        package.analysis["seconds"] + template_seconds, 3
                    )
                timings["intelligence_seconds"] = round(time.monotonic() - analysis_started, 3)
                return package
            finally:
                if hasattr(gateway, "aclose"):
                    await gateway.aclose()

        package = asyncio.run(analyze())
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


def run_template_preanalysis(
    store: Store,
    job_id: str,
    settings: Settings,
    services: PreparationServices,
) -> None:
    """Fill the existing template cache before the user submits content."""
    started = time.monotonic()
    gateway = None
    store.update(job_id, "running", phase="Разбор PPTX и дизайн-системы", progress=15)
    try:
        technical = prepare_template(
            store,
            job_id,
            PreparationRequest("", "", "", None),
            settings,
            services,
            template_only=True,
        )
        if technical is None:
            return
        gateway = services.gateway_factory(settings)

        async def analyze() -> TemplateAnalysisResult:
            try:
                return await prepare_template_result(store, job_id, technical, settings, gateway)
            finally:
                if hasattr(gateway, "aclose"):
                    await gateway.aclose()

        result = asyncio.run(analyze())
        profile = result.profile
        store.update(
            job_id,
            "ready",
            phase="Шаблон изучен",
            progress=100,
            template_sha256=profile.sha256,
            template={
                "font": profile.font,
                "colors": profile.colors[:12],
                "patterns": len(profile.patterns),
                "warnings": profile.warnings,
            },
            template_cache=result.analysis["template_cache"],
            template_semantics=result.analysis.get("template_semantics", {}).get("status"),
            native_render=result.analysis.get("native_render", {}).get("patterns", 0),
            analysis_seconds=round(time.monotonic() - started, 3),
        )
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
        record_preparation_failure(store, job_id, None, gateway, started, exc)
