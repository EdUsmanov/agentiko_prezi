> Исторический отчёт из main до объединения с модульным backend. Пути модулей отражают состояние на дату отчёта. Текущая архитектура: [ARCHITECTURE.md](../../ARCHITECTURE.md).

# Adaptive selection of template backgrounds

Implements the follow-up to BACKGROUND_DIAGNOSIS_2026-09-28.md. No template names,
slide numbers, color constants or customer-specific branches are used.

Compatible masters/layouts now remain eligible after authored examples fail to
fit. Authored compositions still have priority. The deck-wide selection can use
masters as well as authored slides, subject to the existing semantic, content,
readability and geometry checks. Background variety may not reduce either the
width or height of chart/table content.

A token composition can independently select sanitized source artwork. Every
occupied box, including titles and footers, must lie inside the canvas and on a
verified uniform region of that artwork. Text and data colors are checked against
the actual local background; table cell paints are checked too. Source references,
data, geometry and font sizes are preserved. No eligible alternative means the
existing safe fallback remains in use. Uploaded-image scenes are excluded.

The selected background is persisted separately from the content layout. Native
PPTX export copies sanitized source objects; preview images are not pasted over
editable slide content. Unsafe or stale selections are rejected again by scene QA
and before export. Layout repair clears the old background selection on the
changed slide; untouched slides retain their actual exported state. Visual QA
receives the selected artwork without imposing the donor's text-field geometry.

Related regressions addressed:
- strict stage report accepts background color statistics;
- saved-plan validation rejects missing/incompatible background IDs;
- server-owned background metadata stays out of planning/Design model requests;
- background-only differences do not count as meaningful composition diversity;
- identical flat master/token backgrounds count as the same artwork family;
- replay expectations reflect actual new master layouts and their review inputs.

## Verification

Offline recomposition and native rendering of the saved customer content produced
33 slides (three variants of 11). Each deck now has pink cover ×1, blue ×5,
lavender ×1 and white ×4, with five distinct artwork families and no identical
adjacent families, instead of pink cover + ten blue slides. Native geometry and
export evidence audits have no errors. The pre-existing slide-count adjustment
warning remains. This change does not claim to solve cross-variant composition
similarity for that source material.

Inspected the new lavender composition and white chart compositions. Slides 6
and 10 are pixel-identical to the verified stacked-chart fix. Recomposition from
the saved plan preserves slide 9's selected white background, geometry and data.
All chart/table boxes match the previous verified deck exactly.
Artifacts: test-results/background-selection-fix/ (ignored local output).

Regression tests cover unsafe artwork intersections, bounds, contrast, uploaded
images, cover/native exclusions, missing sanitized sources, stale paint/geometry,
native editable export without old text, model schema/plan validity, roomy master
fallback, visual references and diversity accounting. Browser tests use synthetic
model responses only. No live model generation or benchmark was started.

Final checks passed: 6 strict browser scenarios, 36 background/composition checks,
39 background/repair-routing checks and 37 visual/chart checks (overlapping sets),
plus the earlier 103 successful backend cases with its one changed bounded-search
expectation corrected and rerun. Ruff formatting/lint, diff checks and all 7 UI
phase-label checks passed. No outstanding test failures remain.
