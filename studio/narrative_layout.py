"""Measure grounded editorial content using the final composition path."""

from .models import SlidePlan, VariantPlan
from .repair_errors import RepairIssue
from .repair_policy import scene_fit_feedback


def narrative_storyboard(package):
    from .storyboard import fit_storyboard

    facts = {f.id: f for f in package.content.facts}
    tables = {t.id: t for t in package.content.tables}
    outline = []
    for group in package.analysis["narrative"]["groups"]:
        ids = group["fact_ids"]
        tid = next((facts[fid].source for fid in ids if facts[fid].source in tables), None)
        units = [
            u
            for u in package.analysis.get("archetypes", {}).get("units", [])
            if u["fact_ids"] == ids
        ]
        purpose = group.get("purpose") or (units[0]["purpose"] if len(units) == 1 else "content")
        visualization = tables[tid].visualization if tid else None
        chart = visualization not in (None, "auto", "table", "metrics")
        outline.append(
            SlidePlan(
                title=group["title"],
                fact_ids=ids,
                table_id=tid,
                purpose=purpose,
                layout="chart"
                if chart
                else "table"
                if tid
                else "process"
                if purpose in ("process", "timeline")
                else "columns",
                chart_type=visualization if chart else "auto",
            )
        )
    if package.analysis.get("editorial"):
        from .composer import compose_slide
        from .uploads import assign_images
        from .audit import audit_scenes, repair_scenes

        # Probe the same final scene that generation will export. Raw compose()
        # can still contain a provisional table before native chart conversion.
        variant = VariantPlan(key="executive", title="Readability preview", slides=outline)
        image_groups = assign_images(package, variant)
        bad = []
        fit_issues = []
        for index, slide in enumerate(outline):
            try:
                scene = compose_slide(variant, package, index, image_groups)
                repair_scenes([scene], package)
                feedback = scene_fit_feedback(
                    scene,
                    audit_scenes([scene], package),
                    index + 1,
                    {row["fact_id"] for row in package.analysis["editorial"].get("provenance", [])},
                )
                if feedback["repair_issues"]:
                    bad.append(index + 1)
                    fit_issues.append(feedback)
            except ValueError as exc:
                bad.append(index + 1)
                fit_issues.append(
                    {
                        "slide": index + 1,
                        "message": str(exc),
                        "fields": [],
                        "repair_issues": [
                            RepairIssue(
                                code="composition_failed",
                                message=str(exc),
                                slide=index + 1,
                                action="stop",
                            ).model_dump()
                        ],
                    }
                )
        if bad:
            package.analysis["slide_budget"] = {
                "status": "needs_input",
                "planned": None,
                "fit_issues": fit_issues,
                "message": "Компоновка требует исправления на слайдах "
                + ", ".join(map(str, bad))
                + ". Причины и допустимые действия записаны по каждому объекту.",
            }
            return
    else:
        outline = fit_storyboard(package, outline)
    package.analysis["storyboard"] = [s.model_dump() for s in outline]
    package.analysis["slide_budget"] = {
        "status": "adjusted",
        "requested": package.constraints.slides,
        "count_mode": package.constraints.count_mode,
        "planned": len(outline),
        "required": len(outline),
        "requested_range": package.analysis["narrative"]["requested_range"],
        "message": f"По смыслу и читаемости предложено {len(outline)} слайдов. Можно принять или пересобрать план.",
    }
