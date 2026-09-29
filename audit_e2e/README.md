# Independent presentation evaluation

The evaluation tools use the production application and inspect its **exported**
PPTX, PDF and slide images against independently authored source requirements.
The application's own quality verdict is execution evidence, not the oracle.
Luna Max judges receive source facts and final exports, not internal verdicts.

Run the commands below from the repository root with the project's Python
environment (`.venv/bin/python` locally). This directory owns the audit runner,
Luna judges and calibration controls, `prompts/`, `fixtures/`, `tests/`, browser
scenarios in `e2e/`, the worker bootstrap, and the pinned `Dockerfile`.
The common application launcher, strict HTTP replay and synthetic-template
helpers remain in `test_support/`, shared with the application's other tests.

New run artifacts default to `audit_e2e/results/evaluations/`; other audit
artifacts also belong under `audit_e2e/results/`. This output directory is
excluded from Git, Docker build context, source packages and source snapshots.
Historical runs and the external template corpus remain under `test-results/`
so their recorded paths and sealed evidence remain valid. Pass a historical
run's full path to `judge` or `compare`. Recorded validation is in
[VALIDATION_2026-09-29.md](VALIDATION_2026-09-29.md). The expanded development corpus,
full replay results and detector gaps are recorded in
[VALIDATION_DIVERSITY_2026-09-29.md](VALIDATION_DIVERSITY_2026-09-29.md).

```bash
python -m pytest -q audit_e2e/tests
```

## Start with replay

The CI-equivalent command builds the pinned Linux image, then runs its process
tree with only loopback networking (`docker run --network none`):

```bash
audit_e2e/test_hermetic.sh
```

The build needs network access for pinned dependencies; execution does not.
LibreOffice and fonts come from a dated Debian snapshot, Chromium from the pinned
Playwright version. Each invocation keeps a new directory under
`audit_e2e/results/hermetic/`, including the built image ID and browser evidence.
For a faster host-environment run:

```bash
python -m audit_e2e run --mode replay --suite core
```

The core suite contains Binghamton content, Binghamton brief/approval, A-State
dark mixed table/image input and dense Adelphi 4:3. The extended suite adds
A-State light, McMurry, USI, WPI and WSSU. Replay uses explicitly labelled seeded
**synthetic analogs**, finite reviewed HTTP cassettes and real application workers
and native export. It makes no claim about a live model or compatibility of the
original external templates. Missing cassettes fail closed; tests never record or
refresh them and never call a real provider.

The external corpus remains in `test-results/external-template-corpus/`, outside
Git. Its file names and SHA-256 are pinned in the case registry. `--corpus-root`
can point to another copy of these exact files. Original input text and independent
key-point ledgers are versioned in `audit_e2e/fixtures/`.
The initial semantic ledgers are source-anchored, agent-authored **silver**
references with review pending. Promote them only after an independent review,
recording that review and incrementing the reference version. Literal table
requirements and deliberately constructed calibration controls have separate
provenance. A silver ledger cannot establish a passing semantic quality gate.

## Development coverage and private challenge

```bash
python -m audit_e2e inventory --output audit_e2e/results/inventory.json
python -m audit_e2e run --mode replay --suite development --timeout 3600
python -m audit_e2e build-defects \
  --output audit_e2e/results/defects --timeout 3600
python -m audit_e2e challenge-status \
  --root SEALED_REVISION_DIRECTORY --identity RECORDED_SHA256
```

`development` extends the unchanged nine legacy cases with owned native templates
and independent source materials. Their persisted PPTX/POTX bytes are used in
both replay and live mode. See [CORPUS.md](CORPUS.md) for measured unique templates,
source hashes, structural families and OOXML features. A native chart in an input
template establishes input coverage; it does not prove that generation preserved
or produced an editable chart. Repeated runs and cosmetic template changes do not
increase the independent-material count.

New runs assess the three delivered variants along two separate dimensions:
source-material organization (grouping/order/emphasis) and composition. Both must
differ. Color, font, background and pixel differences alone do not qualify;
shared covers and unchanged source tables are allowed. The deterministic checker
can establish identical literal point grouping or content geometry. Different
arrangements alone cannot certify meaningful alternatives, and paraphrased or
insufficient source evidence stays **inconclusive** pending calibrated independent
review. This stricter policy can fail or abstain on a replay whose HTTP execution
completed successfully; execution status and quality status are separate.

PPTX/PDF/HTML checks compare source anchors, with per-format evidence and slide
locations. Text absent from PDF extraction can still be rasterized or outlined,
so absence is uncertainty unless an independently constructed text-export control
requires literal agreement. This is not optical proof that every export looks
the same. HTML parsing excludes directly hidden/script content but does not
evaluate CSS classes or browser visibility.

The bad-export builder saves expected labels outside evidence bundles, then runs
the ordinary artifact detector without labels. Its detection matrix distinguishes
detected, missed, inconclusive and false-positive outcomes. Constructed controls
do not make silver development references gold. The original three-pass Luna
calibration remains a separate, narrower control set; building the expanded corpus
does not certify any new category or invoke a model.
`build-defects` prints build completion separately from the detector result;
its exit code follows detection (1 for misses/false alarms, 2 for uncertainty).
A completed artifact build is not a calibration certificate.

The current diversity rule never automatically certifies semantic alternatives.
Its `inconclusive` result remains part of the overall gate even after a favorable
Luna review. Positive qualification will require a calibrated resolution policy;
this implementation deliberately supplies no permissive fallback.

The owned fixture registry covers the original eleven defect families plus clipping,
overlap, low contrast, lost backgrounds, foreign template text, chart value/axis
corruption, missing or distorted figures, and PPTX/PDF/HTML disagreement. Every
category has a clean control, a constructed defect, and a source-faithful wording
transformation; each case exports executive, analytical, and story layouts from
three fictional source topics. The full build creates 66 main cases (198 native
presentations) plus three cosmetic-only duplicate controls and three source-limited
abstention controls: 72 cases, 216 PPTX/PDF/HTML presentation sets. The latter
controls alter palette or font while preserving fact order and geometry; source-
limited controls contain only one measured point, so they cannot establish three
meaningful organizations.

Expected labels are written to `labels.expected.json`, outside every evidence
bundle. `control-metadata.json` records source/ledger and per-export hashes,
composition IDs, source-point grouping and authored localization; generated cases
are marked `native_rendered_control`. This is not a product E2E run, and repeated
judging does not increase its unique source-family or composition counts. The
matrix scores only independent artifact findings: failed target findings must
localize every expected affected variant, while partial localization and related
proxy findings remain inconclusive. Unmapped visual challenges (including
overlap, contrast, background loss, foreign template text and chart corruption)
remain curator-labeled and will count as missed unless an independent finding
actually targets them. The image-pixel detector can flag a changed image, but it
cannot distinguish distortion from any other pixel change. Text extraction is not
optical verification. Historical observations are indexed as local-only paths and
hashes in `fixtures/historical_observations.json`; they come from an uncalibrated
review of a silver reference and are not detector labels. The external branded
source deck and previews are not copied into this repository.

The private challenge lives outside this repository. Its command returns counts,
seal integrity and lifecycle readiness only, never cases, filenames or expected
answers. `ready` is not a product-quality verdict. Disclosure retires affected
holdout entries; a replacement needs a new sealed identity. The separate directory
and curator workflow prevent accidental disclosure, but do not prevent another
process running as the same OS user from reading it. Enforced isolation requires
a separate curator account or environment. No product run is implicit in status
validation.

## Live generation and evaluation

Use the application's existing `STUDIO_MODEL_*` / `LLM_*` configuration. Model
generation keeps the configured product model; the independent evaluator is
`gpt-6-luna`, reasoning `max`, through an authenticated local Codex CLI.

```bash
# One unattended bounded run. The timeout is for the whole run, including judging.
python -m audit_e2e run --mode live --suite core \
  --max-requests 160 --timeout 3600 \
  --calibration audit_e2e/results/calibration/calibration.json

# Explicit diagnostic scope: does not establish model quality.
python -m audit_e2e run --mode live --suite core \
  --case binghamton-content --max-requests 40 --timeout 600 --generation-only
```

Each case starts its own application and empty data/cache directory. Physical
model attempts, including retries and failed requests, count against the request
limit at the product-provider boundary. The Codex judge has a separate bound:
at most three concurrent workers, at most two reviews per case in a live run,
and the remaining run deadline. Codex's internal backend request count is not
exposed as a reliable per-request counter; it is not claimed as part of
`--max-requests`. Its emitted token usage is recorded separately.
Exceeding limits, missing dependencies, invalid judge JSON, incomplete
slide/fact coverage or unavailable models cannot yield a passing quality result.
The deadline stops experiment work; process cleanup and saving the final evidence
report can finish after it. A result completed after the deadline cannot pass.
There is no repeat-generation-until-success loop. Source code is hashed before
and after the run, including uncommitted content; changing code during a run
invalidates its reproducibility.

The brief case and dark mixed-input case use the actual browser. The other cases
use HTTP. Brief approval exercises the normal application hash-bound consent
operation as a test actor; the harness does not bypass the production contract.
Case-registry schema 2 records the browser cases' five-slide target and the
explicit mini-preset range of 3–5. The browser submits size_preset=mini without
an exact slide count, so an approved three-slide plan is within contract and is
reported as a target deviation. HTTP cases submit an explicit slide count; that
exact count remains the contract even when the request also includes mini.

## Calibrate before trusting the judge

```bash
# Build clean and deliberately damaged real presentation exports without an LLM.
python -m audit_e2e calibrate \
  --output audit_e2e/results/controls --timeout 1800 --controls-only

# Build controls and evaluate every control three times with Luna Max.
python -m audit_e2e calibrate \
  --output audit_e2e/results/calibration --timeout 7200
```

Expected labels stay outside judge packets. Calibration checks the expected
defect category in every affected variant, not merely any failing finding.
The identical-variant control uses a case-wide comparison contract.
Clean and permissible-paraphrase
controls must not produce critical false alarms. A category qualifies only if
all its defects and clean controls are classified correctly on all three passes.
Certificates bind the model, prompts, report schema and control identities.
An absent, stale or failed calibration leaves model quality **inconclusive**.

Semantics, visible defects and design are separate. Required facts must preserve
numbers, units, negation, conditions and attribution. Source words hidden off
slide or present only in speaker notes do not count as visible coverage. Model
proposals remain proposals even after draft approval. Silver/uncertain reference
items are not automatically promoted to gold by agreement with generated output.

The test runner launches independent `codex exec` worker processes, each using
Luna Max, a read-only sandbox and a separate packet directory. One worker reviews
a whole case, including all three variants and every image; at most three workers
run concurrently. The Python runner assigns cases directly: there is no extra
LLM coordinator and no reliance on incomplete CLI collaboration telemetry.
Failed/inconclusive cases, including invalid primary responses, and a fixed
20% sample of successful cases receive an independent second review. Unresolved
disagreement stays inconclusive. An invalid primary response plus a valid second
response also remains inconclusive. Before calibration qualifies, raw positive
and negative judge verdicts remain diagnostic. Design scores and paired preference are
advisory, never a compensation for factual or visible failures.

## Reuse exports while tuning

```bash
python -m audit_e2e judge --run audit_e2e/results/evaluations/RUN \
  --timeout 1800 --calibration audit_e2e/results/calibration/calibration.json

python -m audit_e2e compare \
  --baseline audit_e2e/results/evaluations/BASELINE \
  --candidate audit_e2e/results/evaluations/CANDIDATE \
  --output audit_e2e/results/comparison --timeout 1800
```

Judging checks sealed artifact hashes before use and saves another judgment;
it does not rewrite the historical run. Comparison requires identical case inputs
and reference versions. Baseline/candidate labels are concealed from the paired
judge and the fixed randomized assignment is saved separately. Saved exports can
be rejudged without generating them again. Partial runs are explicitly labelled
with their selected cases and never presented as the whole suite.

Paired A/B sides are experiments of the same variant. Identical exports are a
valid tie. The paired comparison does not assess within-run duplication because
side assignments can differ between pairs; whole-case `judge` and deterministic
export checks assess diversity among the three delivered variants. Comparison
manifests retain both input runs' source/environment identities and the evaluator's
uncommitted source snapshot. Source drift during a comparison prevents a pass.

Every run has `run.json`, a portable `report.html`, source snapshot and environment
versions, per-case execution diagnostics, sealed evidence and judge reports. The
generator and judge usage are reported separately; account-wide usage deltas are
not attributed to an experiment. Exit codes: `0` passed, `1` failed, `2`
inconclusive. A replay pass establishes deterministic behavior only. An
uncalibrated live judge is diagnostic even if its raw judgments are favorable.

## Deliberate cassette updates

```bash
python -m audit_e2e.build_cassettes --case binghamton-content
# Replacing an existing fixture is an explicit authoring action:
python -m audit_e2e.build_cassettes --case binghamton-content --replace --revision 4
# The separate real-UI selected repair scenario:
python -m audit_e2e.build_cassettes --case binghamton-content --selected-repair
```

This offline development tool authors synthetic replies against the real gateway
and normally saves only a complete successful exchange set. The explicit
`--capture-failure` authoring option can preserve a finite failed execution as a
regression; it records the failure and never turns it into an expected success.
If the app asks for an editorial repair, this mode explicitly authors an unchanged
patch to exercise the bounded failure path. Its metadata marks the ineffective
repair; it does not establish that a capable model could not fix the layout.
Use `--suite development --case CASE` for new development fixtures. Review the request/response
diff and rerun strict replay before accepting an update. Its canned critic replies
are plumbing fixtures, not independent quality assessments. Never weaken matching
to a stage name or derive expected facts from the candidate output.

## Initial integration with modular backend and main

The integration branch retains the independent case ledgers, judge prompts and
oracles from `demo/audit-e2e` at `40d98f3`. Cassette revision 3 matches the current
prompts, required claim labels and composition/visual-review requests. The
author, editorial and editorial-review responses are unchanged from revision 1,
except for explicitly empty `group` fields required by the new request schema.
Revision 3 preserves revision 2 content responses; only composition requests,
deterministic baseline assignments and visual-review batches were refreshed after
the variant selection fix. Input cases and independent verdict rules are unchanged.
The browser opens the current additional-settings panel and uses the download
link's accessible name. See the [integration report](../docs/AUDIT_MERGE_2026-09-29.md)
for actual verification results; the older validation report describes its own
recorded source revision. The [variant selection follow-up](../docs/VARIANT_SELECTION_FIX_2026-09-29.md)
records the subsequent fix and its checks.

## Expanded audit integration into main

The expanded audit from `demo/audit-diversity` at `ee56ea2` adds stronger
organization-diversity and export-consistency checks. These supersede the original
nine-case audit's narrower verdict rules; the older integration reports describe
their historical evaluator revision.

The 15 `dev-*` cassettes are explicitly refreshed against the modular product at
`04aa0b4`: each fixture revision increases by one. Source, author and editorial
responses are preserved, apart from explicit empty claim `group` fields required
by the current schema. Previously recorded editorial-review replies are preserved;
newly reached review stages use explicitly authored synthetic plumbing replies,
not independent quality judgments. Request schemas, prompt hashes, baseline
compositions and review batches follow the current pipeline. Recorded failed
executions remain diagnostic failures; they are not expected successes. Strict
replay matching and the independent case ledgers are unchanged.

See [the combined merge report](../docs/MAIN_AUDIT_DIVERSITY_MERGE_2026-09-29.md)
for the integration results. The quality gate remains failing; infrastructure tests
passing does not qualify the generated presentations.
