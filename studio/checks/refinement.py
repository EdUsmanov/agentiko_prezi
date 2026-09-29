"""Bounded render-observe-repair cycle. Only typed, authorized layout edits."""

from dataclasses import dataclass
import asyncio
import time
from collections import Counter
from studio.composition.layout_edits import LayoutEdit as LayoutEdit, RepairBatch as RepairBatch
from studio.composition.contracts import candidates
from studio.composition.composer import CompositionSession
from studio.checks.audit import audit_scenes, repair_scenes
from studio.composition.render import render_variant
from studio.checks.visual import review_visuals
from studio.contents.planner import validate_plans
from studio.composition.artifacts import publish_variants, PublicationRollbackError
from studio.checks.export_audit import audit_export


@dataclass(frozen=True)
class ValidatedEdits:
    edits: list[LayoutEdit]
    rejected: int


def error_counts(findings):
    return Counter((f.code, f.slide) for f in findings if f.severity == "error")


def apply_edits(package, plans, decks, edits, allowed, composition_cache=None):
    """Atomic proposal validation; no text, facts, files, code or coordinates accepted."""
    cache = composition_cache or CompositionSession(package)
    trial = plans.model_copy(deep=True)
    mapping = {v.key: v for v in trial.variants}
    seen = set()
    for edit in edits:
        key = (edit.variant, edit.slide)
        choice = edit.pattern_id if edit.operation == "change_layout" else "@readable_chart"
        if (
            key in seen
            or choice not in allowed.get(key, [])
            or edit.operation == "readable_chart"
            and edit.pattern_id is not None
        ):
            raise ValueError("Repair outside the affected slide/candidate allowlist")
        seen.add(key)
        if edit.operation == "change_layout":
            mapping[edit.variant].slides[edit.slide - 1].pattern_id = edit.pattern_id
        else:
            mapping[edit.variant].slides[edit.slide - 1].chart_style = "readable"
            # Re-evaluate physical capacity, including a larger compatible layout.
            mapping[edit.variant].slides[edit.slide - 1].pattern_id = None
    validate_plans(trial, package)
    changed = {}
    for key in {e.variant for e in edits}:
        # Preserve the actual exported state of untouched slides, including diversity repairs.
        scenes = [
            cache.slide(mapping[key], i) if (key, i + 1) in seen else s.model_copy(deep=True)
            for i, s in enumerate(decks[key])
        ]
        for i, scene in enumerate(scenes, 1):
            if (key, i) in seen:
                repair_scenes([scene], package)
        if error_counts(audit_scenes(scenes, package)) - error_counts(
            audit_scenes(decks[key], package)
        ):
            raise ValueError("Repair introduces a new deterministic error")
        from studio.checks.quality import candidate_regressions

        regressions = candidate_regressions(decks[key], scenes, package)
        if regressions:
            raise ValueError(
                "Repair reduces quality: " + ", ".join(sorted({r["code"] for r in regressions}))
            )
        changed[key] = scenes
    return trial, changed


def select_edits(
    package, plans, decks, proposed, allowed, issues, composition_cache
) -> ValidatedEdits:
    """Validate model edits and bounded server candidates before staging any files."""
    edits = []
    seen = set()
    rejected = 0
    for edit in proposed:
        key = (edit.variant, edit.slide)
        if key in seen:
            rejected += 1
            continue
        try:
            apply_edits(package, plans, decks, [edit], allowed, composition_cache)
        except ValueError:
            rejected += 1
            continue
        edits.append(edit)
        seen.add(key)
    # Known chart-reading defects have a safe server-owned candidate,
    # even if the model declines or proposes a malformed unrelated edit.
    for finding in issues:
        key = (finding["variant"], finding["slide"])
        if (
            key not in seen
            and "@readable_chart" in allowed.get(key, [])
            and finding["code"] in ("readability", "overlap", "hierarchy")
        ):
            edit = LayoutEdit(variant=key[0], slide=key[1], operation="readable_chart")
            try:
                apply_edits(package, plans, decks, [edit], allowed, composition_cache)
            except ValueError:
                continue
            edits.append(edit)
            seen.add(key)
    # A malformed model proposal is not a reason to skip a safe physical
    # candidate. The unchanged story is re-rendered and reviewed below.
    for key, choices in allowed.items():
        if key in seen:
            continue
        for choice in choices:
            if choice == "@readable_chart":
                continue
            edit = LayoutEdit(variant=key[0], slide=key[1], pattern_id=choice)
            try:
                apply_edits(package, plans, decks, [edit], allowed, composition_cache)
            except ValueError:
                continue
            edits.append(edit)
            seen.add(key)
            break
    return ValidatedEdits(edits, rejected)


async def refine(
    package, plans, decks, results, visual, directory, source, gateway, timeout, progress
):
    composition_cache = CompositionSession(package)
    report = {"status": "not_needed", "attempts": 0, "accepted": False, "edits": []}
    issues = list(visual.get("findings", []))
    issues.extend(
        {**f, "variant": r["key"]}
        for r in results
        for f in r.get("export_findings", [])
        if f["severity"] == "error" and f.get("slide")
    )
    if not issues or gateway.settings.mode != "api":
        return plans, decks, results, visual, report
    if visual["status"] != "completed" or (timeout is not None and timeout < 45):
        report.update(status="not_run", reason="incomplete_review_or_insufficient_deadline")
        return plans, decks, results, visual, report
    started = time.monotonic()
    allowed = {}
    affected = []
    for finding in issues:
        key = (finding["variant"], finding["slide"])
        if key in allowed:
            continue
        variant = next(v for v in plans.variants if v.key == key[0])
        slide = variant.slides[key[1] - 1]
        current = decks[key[0]][key[1] - 1].pattern_id
        patterns = [p for p in candidates(package, slide, key[1] - 1) if p.id != current]
        allowed[key] = [p.id for p in patterns]
        chart_repair = slide.layout == "chart" and slide.chart_style != "readable"
        if chart_repair:
            allowed[key].append("@readable_chart")
        affected.append(
            {
                "variant": key[0],
                "slide": key[1],
                "plan": slide.model_dump(),
                "current_pattern": current,
                "readable_chart_available": chart_repair,
                "candidates": [
                    {
                        "id": p.id,
                        "purpose": p.purpose,
                        "title_zone": p.title_zone.model_dump(),
                        "body_zones": [z.model_dump() for z in p.body_zones],
                    }
                    for p in patterns
                ],
            }
        )
    progress("Исправляем замечания визуальной проверки в пределах сценария")
    report.update(status="running", attempts=1)
    try:
        async with asyncio.timeout(timeout):
            try:
                raw = await gateway.json_request(
                    "repair",
                    {"affected": affected, "findings": issues},
                    timeout=90 if timeout is None else min(25, timeout * 0.25),
                    schema=RepairBatch.model_json_schema(),
                )
                proposed = RepairBatch.model_validate(raw).edits
            except Exception as exc:
                proposed = []
                report["proposal_error"] = type(exc).__name__
            selection = select_edits(
                package, plans, decks, proposed, allowed, issues, composition_cache
            )
            edits = selection.edits
            report["rejected_proposals"] = selection.rejected
            report["proposed_edits"] = [e.model_dump() for e in edits]
            if not edits:
                report.update(status="unresolved", reason="no_safe_edit_proposed")
                return plans, decks, results, visual, report
            trial, changed = apply_edits(package, plans, decks, edits, allowed, composition_cache)
            staging = directory / "refinement-1"
            staging.mkdir(exist_ok=True)
            revised = []
            for result in results:
                key = result["key"]
                if key not in changed:
                    continue
                rendering = await asyncio.to_thread(
                    render_variant, changed[key], package.template, source, staging / key
                )
                variant = next(v for v in trial.variants if v.key == key)
                revised.append(
                    {
                        **result,
                        "rendering": rendering,
                        "export_findings": audit_export(
                            staging / key / "deck.pptx", variant, package
                        ),
                        "template_strategies": sorted({s.strategy for s in changed[key]}),
                        "findings": [f.model_dump() for f in audit_scenes(changed[key], package)],
                    }
                )
            progress("Повторно проверяем исправленные презентации")
            after = await review_visuals(
                revised,
                staging,
                gateway,
                None if timeout is None else max(0.01, timeout - (time.monotonic() - started) - 3),
                progress,
                package,
            )
            keys = set(changed)
            before = [f for f in visual["findings"] if f["variant"] in keys]
            before.extend(
                {**f, "variant": r["key"]}
                for r in results
                if r["key"] in keys
                for f in r.get("export_findings", [])
            )
            after_findings = after["findings"] + [
                {**f, "variant": r["key"]} for r in revised for f in r.get("export_findings", [])
            ]

            def score(items):
                return (sum(f["severity"] == "error" for f in items), len(items))

            def visual_errors(items):
                return Counter(
                    (f["variant"], f["slide"], f["code"])
                    for f in items
                    if f["severity"] == "error"
                    or f["code"]
                    in ("readability", "pptx_readability", "contrast", "unsafe_text_zone")
                )

            # Do not adopt an unreviewed or non-improving revision.
            if (
                after["status"] != "completed"
                or score(after_findings) >= score(before)
                or visual_errors(after_findings) - visual_errors(before)
            ):
                report.update(status="rejected", reason="visual_improvement_not_confirmed")
            else:
                publish_variants(directory, staging, sorted(keys))
                for key in keys:
                    decks[key] = changed[key]
                replacements = {r["key"]: r for r in revised}
                results = [replacements.get(r["key"], r) for r in results]
                visual = {
                    **visual,
                    "findings": [f for f in visual["findings"] if f["variant"] not in keys]
                    + after["findings"],
                    "refinement_review": after,
                    "revision": 2,
                }
                plans = trial
                report.update(
                    status="completed", accepted=True, edits=[e.model_dump() for e in edits]
                )
    except PublicationRollbackError:
        raise
    except Exception as exc:
        report.update(status="failed", reason=type(exc).__name__)
    report["seconds"] = round(time.monotonic() - started, 3)
    return plans, decks, results, visual, report
