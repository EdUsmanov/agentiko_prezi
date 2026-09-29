"""One quality policy for selection, Design, diversity, repair and publication."""

from studio.checks.scene_quality import scene_quality_findings as scene_quality_findings

from collections import Counter
from itertools import combinations
from math import ceil


def candidate_regressions(before, after, package, audit=None, *, preserve_structure=False):
    from studio.checks.audit import audit_scenes
    from studio.checks.scene_regions import unused_body_regions
    from studio.composition.design_balance import broken_words

    audit = audit or audit_scenes
    problems = []
    if len(before) != len(after):
        return [{"slide": 0, "code": "slide_count"}]

    def add(slide, code):
        problems.append({"slide": slide, "code": code})

    for index, (old, new) in enumerate(zip(before, after), 1):
        if broken_words(new, package.template) > broken_words(old, package.template):
            add(index, "word_break_regression")
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

    before_errors = keys(before)
    if preserve_structure:
        for index, (old, new) in enumerate(zip(before, after), 1):
            if not any(slide == index for slide, _ in before_errors) and _organization(
                old
            ) != _organization(new):
                add(index, "composition_changed")
    for (slide, code), count in (keys(after) - before_errors).items():
        problems.extend({"slide": slide, "code": code} for _ in range(count))
    return problems


def _content_objects(scene):
    """Visible, source-linked objects; presentation labels and styling are excluded."""
    return [
        e
        for e in scene.elements
        if e.source_ids
        and e.role not in ("title", "footer", "brand", "template_background")
        and e.kind in ("text", "table", "chart", "image")
    ]


def _organization(scene):
    """Topology of evidence and its reading relationships, independent of page position."""
    objects = _content_objects(scene)

    def label(e):
        shape = ()
        if e.kind == "table":
            shape = (len(e.rows), max((len(row) for row in e.rows), default=0))
        elif e.kind == "chart":
            shape = (e.chart_type, len(e.labels), len(e.series_values))
        return (tuple(sorted(e.source_ids)), e.kind, shape)

    nodes = sorted(label(e) for e in objects)
    edges = []
    for i, first in enumerate(objects):
        for second in objects[i + 1 :]:
            a, b = first, second
            if label(a) > label(b):
                a, b = b, a
            if label(a) == label(b):
                continue
            dx = b.box.x - a.box.x
            dy = b.box.y - a.box.y
            # Reading anchors ignore resizing; 24 px absorbs alignment noise.
            horizontal = abs(dx) > 24
            vertical = abs(dy) > 24
            relation = (
                (1 if dx > 0 else -1) if horizontal else 0,
                (1 if dy > 0 else -1) if vertical else 0,
            )
            edges.append((label(a), label(b), relation))
    return nodes, sorted(edges)


def meaningful_diversity(decks, profile):
    """Require changed evidence organization on at least half the content slides."""
    pairs = []
    for (left, a), (right, b) in combinations(decks.items(), 2):
        eligible = [
            i
            for i, s in enumerate(a)
            if s.purpose not in ("cover", "divider") and _content_objects(s)
        ]
        changed = []
        for i in eligible:
            if i >= len(b):
                continue
            before, after = _organization(a[i]), _organization(b[i])
            source_before = {fid for e in _content_objects(a[i]) for fid in e.source_ids}
            source_after = {fid for e in _content_objects(b[i]) for fid in e.source_ids}
            if source_before == source_after and before != after:
                changed.append(i + 1)
        required = ceil(len(eligible) * 0.5)
        pairs.append(
            {
                "variants": [left, right],
                "changed_slides": changed,
                "required_slides": required,
                "verified": bool(required) and len(changed) >= required,
            }
        )
    return {
        "verified": len(decks) == 1 or bool(pairs) and all(p["verified"] for p in pairs),
        "pairs": pairs,
        "method": "single_variant" if len(decks) == 1 else "source_linked_visual_organization",
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
