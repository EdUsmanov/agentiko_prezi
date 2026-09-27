"""One quality policy for selection, Design, diversity, repair and publication."""

from .scene_quality import scene_quality_findings as scene_quality_findings

from collections import Counter
from itertools import combinations
from math import ceil


def candidate_regressions(before, after, package, audit=None):
    from .audit import audit_scenes
    from .scene_regions import unused_body_regions

    audit = audit or audit_scenes
    problems = []
    if len(before) != len(after):
        return [{"slide": 0, "code": "slide_count"}]

    def add(slide, code):
        problems.append({"slide": slide, "code": code})

    for index, (old, new) in enumerate(zip(before, after), 1):
        if old.source_ids != new.source_ids or old.title != new.title or old.purpose != new.purpose:
            add(index, "scenario_changed")
        if unused_body_regions(new, package) > unused_body_regions(old, package):
            add(index, "empty_regions")

        def sizes(scene):
            result = {}
            for e in scene.elements:
                if e.kind not in ("text", "table", "chart"):
                    continue
                ids = e.source_ids or (["@title"] if e.role == "title" else [])
                for fid in ids:
                    key = (e.kind, fid, e.role)
                    result[key] = min(result.get(key, e.size), e.size)
            return result

        previous = sizes(old)
        for key, size in sizes(new).items():
            floor = 18 if key[-1] == "title" else 16
            if key in previous and size < min(previous[key], floor) - 0.1:
                add(index, "readability_regression")
        old_areas = {
            (e.kind, fid): e.box.w * e.box.h
            for e in old.elements
            if e.kind in ("table", "chart")
            for fid in e.source_ids
        }
        if any(
            e.box.w * e.box.h < old_areas.get((e.kind, fid), 0) * 0.7
            for e in new.elements
            if e.kind in ("table", "chart")
            for fid in e.source_ids
        ):
            add(index, "data_area_regression")

    def keys(scenes):
        return Counter(
            (f.slide, f.code)
            for f in audit(scenes, package)
            if f.severity == "error" or f.code in ("contrast", "readability", "unsafe_text_zone")
        )

    for (slide, code), count in (keys(after) - keys(before)).items():
        problems.extend({"slide": slide, "code": code} for _ in range(count))
    return problems


def meaningful_diversity(decks, profile):
    """Compare source-linked content, requiring substantial changes on >= half the content slides."""
    pairs = []
    for (left, a), (right, b) in combinations(decks.items(), 2):
        eligible = [
            i
            for i, s in enumerate(a)
            if s.purpose not in ("cover", "divider")
            and any(e.source_ids for e in s.elements if e.role != "title")
        ]
        changed = []
        for i in eligible:

            def boxes(scene):
                return {
                    (fid, e.kind): e.box
                    for e in scene.elements
                    if e.role not in ("title", "footer", "brand", "template_background")
                    for fid in e.source_ids
                }

            first, second = boxes(a[i]), boxes(b[i]) if i < len(b) else {}
            common = set(first) & set(second)
            moved = sum(
                max(
                    abs(first[k].x - second[k].x) / profile.width,
                    abs(first[k].y - second[k].y) / profile.height,
                    abs(first[k].w - second[k].w) / max(first[k].w, second[k].w, 1),
                    abs(first[k].h - second[k].h) / max(first[k].h, second[k].h, 1),
                )
                >= 0.12
                for k in common
            )
            # A changed object type also counts, but mere font/color/title changes do not.
            difference = moved + len(set(first) ^ set(second)) / 2
            if first and difference >= max(1, ceil(len(first) * 0.5)):
                changed.append(i + 1)
        required = min(len(eligible), max(2, ceil(len(eligible) * 0.5)))
        pairs.append(
            {
                "variants": [left, right],
                "changed_slides": changed,
                "required_slides": required,
                "verified": bool(required) and len(changed) >= required,
            }
        )
    return {
        "verified": bool(pairs) and all(p["verified"] for p in pairs),
        "pairs": pairs,
        "method": "substantial_source_linked_content_changes",
    }


def quality_report(manifest):
    findings = []
    seen = set()

    def add(item, source, variant=None):
        row = {**item, "source": source}
        if variant is not None:
            row["variant"] = variant
        row.setdefault("severity", "warning")
        key = (
            row.get("variant"),
            row.get("slide"),
            row.get("code"),
            row.get("message"),
            row["severity"],
        )
        if key not in seen:
            seen.add(key)
            findings.append(row)

    for result in manifest.get("variants", []):
        for item in result.get("findings", []) + result.get("export_findings", []):
            add(item, "variant", result["key"])
        for item in result.get("repairs", []):
            add(item, "repair", result["key"])
    for key in ("visual_audit", "contextual_audit"):
        report = manifest.get(key, {})
        for item in report.get("findings", []):
            add(item, key)
        incomplete = report.get("status") != "completed"
        if key == "visual_audit" and "total" in report:
            incomplete |= report.get("checked", 0) != report["total"]
        if incomplete:
            add({"code": "audit_incomplete", "message": "Проверка не завершена: " + key}, key)
    for message in manifest.get("preparation_analysis", {}).get("warnings", []):
        add({"code": "preparation_warning", "message": message}, "preparation")
    for message in manifest.get("warnings", []):
        add({"code": "run_warning", "message": message}, "run")
    for item in manifest.get("composition_diversity", {}).get("findings", []):
        add(item, "diversity")
    if manifest.get("composition_diversity", {}).get("verified") is False:
        add(
            {
                "code": "insufficient_diversity",
                "message": "Три существенно разные композиции не подтверждены.",
            },
            "diversity",
        )
    if manifest.get("checks", {}).get("native_pptx_render") is False:
        add(
            {"code": "native_render_missing", "message": "Нет проверки отрисованного PPTX."},
            "export",
        )
    errors = max(int(manifest.get("errors", 0)), sum(f["severity"] == "error" for f in findings))
    warnings = sum(f["severity"] == "warning" for f in findings)
    status = "blocked" if errors else "needs_review" if warnings else "passed_checks"
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "findings": findings,
        "export_completed": bool(manifest.get("variants")),
        "manual_acceptance": "not_recorded",
    }
