"""Content preparation using a reusable, independently versioned template profile."""

import time
from copy import deepcopy
from .planner import plan
from .template_analysis import (
    PatternMeaning as PatternMeaning,
    TemplateMeaning as TemplateMeaning,
    normalize_meanings as normalize_meanings,
    template_inventory as template_inventory,
    reference_images as reference_images,
    analyze_meaning as analyze_meaning,
    prepare_template_analysis,
)


async def prepare_intelligence(package, path, gateway, progress):
    started = time.monotonic()
    cached = package.analysis.pop("_template_snapshot", None)
    if cached is not None:
        package.analysis = deepcopy(cached)
        package.analysis["template_cache"] = {"hit": True, "scope": "validated_template_only"}
        progress("Используем проверенный анализ неизменного шаблона", 78)
    else:
        await prepare_template_analysis(package, path, gateway, progress)
        from .template_cache import TemplateCache

        cache = TemplateCache(gateway.settings)
        saved = cache.save(package.template, path.parent, package.analysis)
        package.analysis["template_cache"] = {
            "hit": False,
            "saved": saved,
            "scope": "validated_template_only",
        }
    template_seconds = round(time.monotonic() - started, 3)
    progress("Анализ содержания и подготовка плана", 80)
    from .document import structure_document
    from .narrative import prepare_narrative, narrative_storyboard

    narrative = await prepare_narrative(package, gateway, lambda message: progress(message, 80))
    if not narrative:
        await structure_document(package, gateway, lambda message: progress(message, 80))
    # A short brief is not permission to create unconfirmed propositions.
    # The storyboard adjusts the count instead of filling it with model facts.
    author_warning = None
    from .sections import add_dividers, prepare_sections
    from .planner import validate_plans

    progress("Выделяем смысловые разделы и размещаем разделители", 88)
    if not narrative:
        await prepare_sections(package, gateway)
    from .archetypes import analyze_content_archetypes

    if package.analysis.get("editorial"):
        package.analysis["archetypes"] = {
            "status": "completed",
            "method": "reviewed_editorial_groups",
            "units": [],
        }
    else:
        await analyze_content_archetypes(package, gateway, lambda message: progress(message, 89))
    from .storyboard import prepare_storyboard

    if narrative:
        narrative_storyboard(package)
    elif gateway.settings.mode == "api":
        prepare_storyboard(package)
    if package.analysis.get("slide_budget", {}).get("status") == "needs_input":
        package.prepared_plans = None
        package.analysis.update(
            planning_source="not_run",
            planning_status="needs_input",
            fact_count=len(package.content.facts),
            planned_slides=None,
            seconds=round(time.monotonic() - started, 3),
        )
        return package
    plans, warning = await plan(package, gateway, 360)
    plans = validate_plans(
        plans if package.analysis.get("storyboard") else add_dividers(plans, package), package
    )
    from .semantic_bindings import canonicalize_storyboard

    canonicalize_storyboard(plans, package)
    from .contracts import candidates as compatible_patterns

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
    package.analysis["intelligence_timings"] = {
        "template_seconds": template_seconds,
        "content_seconds": round(time.monotonic() - started - template_seconds, 3),
    }
    package.analysis.update(
        {
            "planning_source": package.analysis.get("planning_method")
            or ("model" if gateway.settings.mode == "api" and not warning else "extractive"),
            "planning_status": "degraded"
            if warning
            or author_warning
            or package.analysis.get("document_structure", {}).get("status") == "failed"
            or package.analysis.get("archetypes", {}).get("status") == "degraded"
            else "completed",
            "fact_count": len(package.content.facts),
            "planned_slides": len(plans.variants[0].slides),
            "seconds": round(time.monotonic() - started, 3),
        }
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
