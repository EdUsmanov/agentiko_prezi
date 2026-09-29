# Development corpus and detector measurement — 2026-09-29

The expanded audit runs against the unchanged product. Its checks are deliberately
not green: full replay completed export for 15 of 24 cases; deterministic quality
returned 12 failures and 12 inconclusive results. The native defect corpus exposed
11 missed defect categories. These results establish executable coverage and
remaining gaps, not a qualified semantic or visual judge.

All changes in this increment are under `audit_e2e/`. The original nine case
definitions and their cassettes are unchanged. No new paid generation, Luna
evaluation, or baseline/candidate live comparison was run for this increment.
Earlier live measurements remain in [the original report](VALIDATION_2026-09-29.md).

## Corpus identity and coverage

The suites contain 4 core, 9 extended and 24 development cases. Development adds
15 owned native PPTX/POTX templates, 15 independent source materials and 3 image
assets. Revision `2026-09-29-development.4` has 15 distinct template hashes and
15 measured source-slide structural signatures. The native files are used in
both replay and live mode. Rebuilding all 15 templates and 3 images twice produced
identical bytes; master/layout relationships were checked.

These signatures omit text, names, fonts, colors and unused layouts. They are
structural fingerprints, not proof of independent semantic design. See
[CORPUS.md](CORPUS.md) and the locally generated
[inventory](results/inventory.json) for the exact measurement.

The original suite registers eight external template hashes. Its nine simplified
replay analogs have seven distinct byte hashes and four aspect/column/theme
profiles. External branded files remain local. No claim is made that their replay
analogs reproduce all native features or WPI reference pages.

All development source ledgers remain silver and unreviewed. Agreement with
generated output cannot promote them to gold. The historical observation index
currently contains one Binghamton live-run lead from an uncalibrated primary
review, covering unsupported claims and template drift. It is not a gold label
or a complete import of historical defects.

## Executed checks

| Check | Result |
| --- | --- |
| `python -m pytest -q audit_e2e/tests audit_e2e/e2e/test_evaluation_workflows.py` | 103 passed in 78.06 s |
| Ruff over `audit_e2e/` | Passed |
| `git diff --check` | Passed |
| Host replay, all 24 development cases | 339.037 s; 15 executions passed, 9 inconclusive; quality 12 failed / 12 inconclusive |
| Native defect corpus build and audit | 72 cases; 216 PPTX, 216 PDF and 216 HTML exports; detector not qualified |
| Pinned Linux runtime/browser tests | 9 passed in 49.62 s |
| Pinned Linux core replay | 69.933 s; 4 executions passed; all 4 failed diversity checks |

The host replay, defect build and Linux core replay each recorded
`source_unchanged: true`. The host replay snapshot includes base commit
`40d98f36cceafa6a39999eebba3fcd26e29df73d` and the uncommitted source tree hash
`8530d8fe5d3b0d15c118c6ec7734f9cc06568e453eb2a8adbb241eba59faa3bf`.
This report was written after that frozen run and is not part of that hash.

Linux used image
`sha256:784bccfcf2ab16882289f2d277975170e05c6898b0a6c88675b369fd11397d2c`
with `docker run --network none`: the process tree had loopback only. Linux
coverage is four core cases, not the entire 24-case development suite.
Commands returned exit code 1 for the failed quality gates; those failures were
not converted into expected passes.

Reproduction commands, from the repository root:

```bash
python -m audit_e2e run --mode replay --suite development --timeout 3600 \
  --output audit_e2e/results/development-v4-replay
python -m audit_e2e build-defects --timeout 3600 \
  --output audit_e2e/results/defects
E2E_IMAGE=presentation-eval-audit-diversity:4 audit_e2e/test_hermetic.sh
```

Use a fresh output directory when preserving an earlier measurement. Generated
evidence remains local under ignored `audit_e2e/results/`; it is not in Git.

## Full development replay

The completed cases exported 45 decks containing 252 slides in total. All original
nine cases executed successfully and failed the stronger organization-diversity
check. Their finite replay responses reuse the same material organization.

| New case | Execution | Quality |
| --- | --- | --- |
| `dev-short-brief` | Inconclusive | Inconclusive |
| `dev-long-title-paragraphs` | Passed | Inconclusive |
| `dev-route-comparison` | Inconclusive | Inconclusive |
| `dev-project-timeline` | Inconclusive | Inconclusive |
| `dev-long-table` | Inconclusive | Inconclusive |
| `dev-wide-table` | Inconclusive | Inconclusive |
| `dev-labeled-metrics` | Passed | Inconclusive |
| `dev-chart-series` | Passed | Failed |
| `dev-screenshot-landscape` | Inconclusive | Inconclusive |
| `dev-screenshot-portrait` | Inconclusive | Inconclusive |
| `dev-screenshot-square` | Passed | Inconclusive |
| `dev-narrow-region` | Inconclusive | Inconclusive |
| `dev-grouped-process` | Passed | Failed |
| `dev-multiple-masters` | Inconclusive | Inconclusive |
| `dev-chart-workbook-resource` | Passed | Failed |

The three new failures include identical exported variants. Other completed
cases lack affirmative diversity evidence, and some also have unresolved export
text consistency. Nine incomplete executions retain their finite synthetic
responses and diagnostics. Six model an intentionally ineffective editorial
repair; the long/wide tables hit readability or capacity checks, and the portrait
input did not produce an acceptable cover/readability result. This is not evidence
that a real model could not solve those tasks.

Initial cassette authoring exposed harness fixture defects: 14 of 15 attempts
failed on stale master/layout relationships, and chart workbook timestamps were
not byte-stable. Those fixture defects were fixed before the final v4 run. The
earlier authoring attempts remain local, rather than being reported as product
failures or counted as successful coverage.

Evidence: [HTML report](results/development-v4-replay/report.html),
[execution manifest](results/development-v4-replay/run.json),
[Linux core report](results/hermetic/20260929T131459Z-64574/core-run/report.html).
Replay made zero physical provider requests. Token usage is unavailable, not an
inferred zero-cost estimate.

## Native defect detection matrix

The builder creates 22 categories, each with clean, defective and permissible
paraphrase whole-case controls. Every whole case contains three composed variants.
Three additional cosmetic-only duplicate controls and three source-limited
controls bring the total to 72 cases. Expected labels stay outside detector
packets. Detection requires the expected failing category and every affected
variant or pair to be located; uncertainty is not counted as detection.

| Result | Categories / controls |
| --- | --- |
| Detected: 9 categories | `number`, `table_binding`, `units`, `hidden_text`, `duplicates`, `readability`, `image_missing`, `pptx_pdf_mismatch`, `pptx_html_mismatch` |
| Detected: 3 additional controls | All three cosmetic-only duplicate controls |
| Missed: 11 categories | `negation`, `condition`, `provenance`, `template_fidelity`, `clipping`, `overlap`, `lowcontrast`, `backgroundloss`, `foreign_template_content`, `chart_value`, `chart_axis` |
| Inconclusive: 2 negative categories | `omission`, `image_distorted` |
| Inconclusive: 47 remaining controls | 44 clean/paraphrase and 3 source-limited controls |

Whole-case totals: **12 detected, 11 missed, 49 inconclusive, 0 false positives,
0 false successes, 0 positively qualified clean cases, 0 not run**. Zero false
alarms is not successful calibration: the clean controls still abstain.
Evidence: [build identity](results/defects/build.json) and
[per-control results](results/defects/validation.json).

## Private challenge aggregate

The aggregate-only validator accepted the sealed local revision: 13 records,
12 eligible holdouts and one reserve; six active families and 12 active materials.
All 13 references are silver/unreviewed, with zero gold labels. Eligible coverage
includes three OLE cases, three SmartArt cases, four mixed-language cases and four
font cases. Structural validation and native source-template rendering passed.

No product quality or Microsoft Office compatibility result is claimed. No case
IDs, file names, source contents or expected answers are published here. Lifecycle
validation retires disclosed records before activating a distinct reserve.
Same-user filesystem separation is procedural, not a security boundary. See
[CHALLENGE.md](CHALLENGE.md) for the public contract.

## Remaining qualification gaps

The conservative diversity check can reject matching literal organization or
composition; it cannot certify semantic diversity from geometry or paraphrase
alone. Inconclusive diversity remains mandatory even after a favorable Luna
review. A calibrated resolution policy is still needed for a positive result.
Common covers and source tables are permitted, and cosmetic changes never provide
a positive qualification shortcut.

Export text agreement is not optical equivalence. Missing extracted text can be
caused by rasterization or paraphrase and normally remains inconclusive. HTML
parsing is not browser rendering, CSS analysis or OCR. The measured misses above
need new detectors or independently calibrated visual evaluation.

The whole-case Luna prompt now explicitly assesses both source organization and
composition. Its version change invalidates prior certificates. The older
13-control calibration command does not qualify this expanded 22-category corpus;
three independent model repetitions of these new controls have not been run.
No baseline/candidate improvement, judge accuracy, new model timing or monetary
cost is inferred from these deterministic runs.
