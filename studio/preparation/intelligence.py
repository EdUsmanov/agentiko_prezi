"""Content preparation using a reusable, independently versioned template profile."""

import time
from studio.preparation.contracts import TemplateAnalysisResult
from studio.contents.planner import plan
from studio.preparation.template import analyze_template_only


async def prepare_intelligence(package, path, gateway, progress, template_result=None):
    started = time.monotonic()
    if template_result is None:
        template_result = await analyze_template_only(
            TemplateAnalysisResult(package.template, {}, {}), path, gateway, progress
        )
    package.template = template_result.profile
    package.analysis = {k: v for k, v in template_result.analysis.items() if k != "model_mode"}
    package.control.model_mode = template_result.analysis.get("model_mode", gateway.settings.mode)
    package.manifest.setdefault("template_layers", {}).update(template_result.template_layers)
    template_seconds = round(time.monotonic() - started, 3)
    progress("Анализ содержания и подготовка плана", 80)
    if package.input_mode == "brief":
        if package.draft is None:
            from studio.contents.author import expand_brief

            package, warning = await expand_brief(package, gateway, 120)
            if warning:
                raise ValueError(warning)
        package.brief_evidence = package.content.model_copy(deep=True)
    from studio.contents.document import structure_document
    from studio.contents.narrative import prepare_narrative, narrative_storyboard

    narrative = await prepare_narrative(package, gateway, lambda message: progress(message, 80))
    if not narrative:
        await structure_document(package, gateway, lambda message: progress(message, 80))
    # Only explicit brief mode may use visibly attributed model proposals.
    author_warning = None
    from studio.contents.sections import add_dividers, prepare_sections
    from studio.contents.planner import validate_plans

    progress("Выделяем смысловые разделы и размещаем разделители", 88)
    if not narrative:
        await prepare_sections(package, gateway)
    from studio.contents.archetypes import analyze_content_archetypes, reviewed_editorial_report

    if package.analysis.get("editorial"):
        package.analysis["archetypes"] = reviewed_editorial_report(package)
    else:
        await analyze_content_archetypes(package, gateway, lambda message: progress(message, 89))
    from studio.contents.storyboard import prepare_storyboard

    if narrative:
        narrative_storyboard(package)
    elif gateway.settings.mode == "api":
        prepare_storyboard(package)
    if package.control.slide_budget and package.control.slide_budget.status == "needs_input":
        package.prepared_plans = None
        package.control.planning_source = "not_run"
        package.control.planning_status = "needs_input"
        package.control.planned_slides = None
        package.analysis.update(
            fact_count=len(package.content.facts),
            seconds=round(time.monotonic() - started, 3),
        )
        return package
    plans, warning = await plan(package, gateway, 360)
    plans = validate_plans(
        plans if package.analysis.get("storyboard") else add_dividers(plans, package), package
    )
    from studio.contents.semantic_bindings import canonicalize_storyboard

    canonicalize_storyboard(plans, package)
    from studio.composition.contracts import candidates as compatible_patterns

    limitations = []
    if any(p.source_slide for p in package.template.patterns):
        for index, slide in enumerate(plans.variants[0].slides):
            if not compatible_patterns(package, slide, index, source_slides_only=True):
                limitations.append(
                    {
                        "slide": index + 1,
                        "purpose": slide.purpose,
                        "reason": "no_compatible_source_exemplar",
                    }
                )
    package.analysis["adaptation_limits"] = limitations
    if limitations:
        package.analysis["warnings"].append(
            "Для слайдов "
            + ", ".join(str(row["slide"]) for row in limitations)
            + " нет совместимого образца в шаблоне. Общие макеты могут не сохранить характер исходной инфографики."
        )
    package.prepared_plans = plans
    if package.input_mode == "brief":
        from studio.contents.brief import build_draft

        package.draft = build_draft(package)
    package.analysis["intelligence_timings"] = {
        "template_seconds": template_seconds,
        "content_seconds": round(time.monotonic() - started - template_seconds, 3),
    }
    package.control.planning_source = package.analysis.get("planning_method") or (
        "model" if gateway.settings.mode == "api" and not warning else "extractive"
    )
    package.control.planning_status = (
        "degraded"
        if warning
        or author_warning
        or package.analysis.get("document_structure", {}).get("status") == "failed"
        or package.analysis.get("archetypes", {}).get("status") == "degraded"
        else "completed"
    )
    package.control.planned_slides = len(plans.variants[0].slides)
    package.analysis.update(
        fact_count=len(package.content.facts),
        seconds=round(time.monotonic() - started, 3),
    )
    package.analysis["warnings"].extend(x for x in (warning, author_warning) if x)
    if (
        len(package.content.facts) < package.constraints.slides
        and package.constraints.count_mode != "maximum"
    ):
        package.analysis["warnings"].append(
            f"Для {package.constraints.slides} слайдов доступно только {len(package.content.facts)} исходных фрагментов. Пустые слайды не добавлены."
        )
    return package
