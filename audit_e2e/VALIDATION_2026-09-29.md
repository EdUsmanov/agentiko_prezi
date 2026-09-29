# Evaluation implementation validation — 2026-09-29

This records verification of the new evaluation infrastructure. It does not
certify the product model's presentation quality or promote the initial silver
references to reviewed gold references. See [README.md](README.md) for
commands and interpretation of results.

## Verified autonomous execution

- The initial combined targeted suite passed **52 tests** in 37.09 seconds: CLI identity and failure
  accounting, export evidence and controls, live-provider accounting, judge
  validation and calibration rules, and strict model HTTP replay.
  Evidence: `test-results/evaluation-implementation/unit-release.xml`.
- After the final protocol and visibility fixes, the judge/CLI/runtime/evidence
  suite passed **49 tests** in 35.58 seconds, including **22** judge tests.
  Ruff check and format verification passed. This is distinct from the earlier
  combined suite, which also included thirteen strict HTTP replay tests.
- The v6 combined suite passed **69 tests** in **35.49 seconds**: 28 judge,
  14 CLI/reporting, 9 evidence, 5 runtime and 13 strict HTTP replay tests.
  Ruff passed. Evidence: `test-results/evaluation-implementation/unit-v6.xml`.
- The existing browser suite passed **6 tests** with actual native exports.
  Evidence: `test-results/evaluation-implementation/legacy-browser.xml`.
- The pinned Linux image ran with Docker `--network none`. All **6** runtime and
  browser tests passed, followed by **4/4** core replay cases. This includes real
  server-side brief approval and selected repair, rather than browser API stubs.
  Evidence: `test-results/hermetic/20260928T215044Z-14367/`.
- The first extended host integration completed **9/9** deterministic cases,
  producing 27 presentation variants and 135 slides in 99.401 seconds. Its overall
  result correctly remained `inconclusive` because development continued during
  that run. Evidence:
  `test-results/evaluation-implementation/replay-integration-1/`.
- The final stable extended replay passed **9/9** cases in **107.597 seconds**,
  with an unchanged source tree and zero external model requests. Evidence:
  `test-results/evaluation-implementation/replay-final/`.
- The final Linux rebuild again passed its runtime/browser tests and **4/4**
  core cases with an unchanged source tree and `--network none`. Evidence:
  `test-results/hermetic/20260928T224316Z-26887/`; image ID:
  `sha256:6bd88ac1d568e21dde644051cb43c0fbbf972a9fcd89057ad68425979d078538`.
- After the v5 protocol, visibility and cleanup fixes, the stable extended
  replay again passed **9/9** in **108.611 seconds**. The rebuilt offline Linux
  image passed **7** runtime/browser tests and **4/4** core cases in 58.462
  seconds; both run manifests report unchanged source. Evidence:
  `test-results/evaluation-implementation/replay-v5/` and
  `test-results/hermetic/20260928T231433Z-34459/`; final image ID:
  `sha256:696bec0332797517bdb7b002fce9e66de9c8437f9cc10079b14d80cd3122ae61`.
- The original external corpus passed hash preflight for all nine scenarios.
  Replay uses synthetic counterparts; it does not assert that original external
  templates have been tested with a real model.

The first verified Linux image ID was
`sha256:b3d853a951372bec6d3a7956cbf9cdd84110dc4136808d50014db2e95569562a`.
Its base manifest is pinned to
`sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e`,
with Debian packages from snapshot `20260925T000000Z`. The local build was arm64;
actual versions were Python 3.12.14, LibreOffice 7.4.7.2 and Chromium 140.0.7339.16.
The later rebuilt image IDs and their results are recorded above.
Image building requires network access; test execution does not.

## Calibration boundaries

The fixture builder creates thirteen independent real PPTX/PDF/PNG controls:
clean, permissible paraphrase, and eleven deliberate defect categories. The
calibration verifier is tested against three complete repetitions, wrong-category
findings, clean false positives, incomplete repetitions and stale certificates.
These deterministic verifier tests are not evidence of Luna's accuracy.

Semantic references in the registered product cases remain source-anchored silver
references with review pending. A successful raw judge response alone cannot
make those cases pass the semantic gate. Design remains advisory. Missing,
malformed, expired or incomplete assessments remain inconclusive.

Native Codex integration uncovered an unsupported JSON Schema keyword and an
exact-quotation wrapper mismatch. The runner now uses the supported strict
schema subset; semantic validation still checks complete slide/point coverage and
exact source/export evidence locally. Both failed integration attempts remain
under `test-results/evaluation-implementation/judge-smoke/`.

## First real product-model integration

The first core attempt used the configured `qwen/qwen3.8-27b` product model, with
a 160-request cap and 1,800-second total deadline. Its historical result remains
at `test-results/evaluation-implementation/live-core-1/`.

- Binghamton content exported three five-slide variants and passed deterministic
  checks.
- Binghamton brief exercised browser approval and exported three variants. The
  executive and story variants were pixel-identical: a genuine blocking defect.
- Both browser cases legitimately approved three slides using the UI's mini
  preset (3–5). The initial exact-five assertion was a harness error, corrected
  by versioning the case contract. Five remains the target; the HTTP cases keep
  their explicitly requested exact count. Historical outputs and verdicts were
  not rewritten to conceal the error.
- Adelphi stopped at the application's HTTP 503 source-change guard while test
  infrastructure was still being finalized. This is an incomplete execution,
  not evidence of an Adelphi quality defect.
- The old batch judge attempt was deliberately stopped when its CLI telemetry
  could not establish independent child execution. Its partial logs remain; it
  supplies no qualifying quality verdict.

The attempt lasted 669.655 seconds and made 74 physical generator requests.
Only 72 returned token accounting: the observed subtotal is 263,860 input and
11,387 output tokens, explicitly marked partial. No monetary cost or complete
token total is inferred from these data. The source snapshot correctly lists
the changed evaluation files, so this integration attempt is not presented as
a reproducible clean baseline.

The incomplete Adelphi case was run once separately after freezing the code,
with a 60-request cap and 900-second deadline. It completed in **110.974 seconds**
with **22 requests**, three five-slide variants, unchanged source and passing
deterministic checks. All 22 requests reported usage: 78,166 input and 2,458
output tokens. This is explicitly a generation-only run, not a semantic-quality
pass. Evidence: `test-results/evaluation-implementation/live-adelphi-final/`.
The three previously exported core cases were not regenerated.

## Isolated Luna workers

The final controller assigns one whole case to each independent `codex exec`
process, up to three at once, using `gpt-6-luna` with reasoning `max`. It validates
and accounts for each response separately. This replaces the initial dependence
on an LLM coordinator's unauditable native-child telemetry.

An actual visual-only smoke passed in **35.8 seconds**: the required duration
appeared only inside the PNG, with no exported slide text. Luna correctly cited
the central panel and preserved the twelve-week fact. Usage was 72,493 input and
1,326 output tokens. Evidence:
`test-results/evaluation-implementation/isolated-worker-smoke/pass-visual/worker-001/`.
This verifies image grounding and the CLI protocol; it is not a calibration
certificate or a measurement of broad judge accuracy.

The first full-control attempt at
`test-results/evaluation-implementation/calibration-final/` exposed three local
protocol defects: typographic quotation marks were rejected around exact slide
text; a visual-region label was incorrectly required to equal the separately
provided region description; and an omission could not refer to a distorted
reference point. Two remaining workers were stopped once these defects made the
attempt non-qualifying. Its original JSON, errors, usage and inconclusive report
are retained. This attempt is not counted as a successful calibration, and its
model ratings are not edited to fit the controls.

Before the next calibration, the control source was completed with the existing
North/South table already used by its reference and slides. This repairs a source
provenance gap; expected defect labels and values were not changed. Deterministic
checks were also hardened against off-canvas tables and unproven grouped-text
coordinates. Pixel visibility (occlusion, text matching its background) remains a
separate rendered-image judgment, not a claim derived from extracted text alone.

## Complete three-repeat measurement

`test-results/evaluation-implementation/calibration-v5/` contains **39** independent
Luna Max evaluations: thirteen controls repeated three times, with no more than
three workers at once. Source remained unchanged. The original result is
**inconclusive**; it is not an eligible calibration certificate.
The interval from the saved controls manifest to the final report was
2,698.2 seconds (about 45 minutes); this excludes preparation of the controls.

The v5 verifier accepted 28 responses and rejected 11 for protocol/grounding or
coverage problems (7, 1 and 3 in the three repeats). Some valid model quotations
used PDF line breaks/table spacing instead of native PPTX text delimiters; those
are harness defects, not evidence of semantic mistakes. Other replies used a
requirement ID where a key-point ID was required or had incomplete coverage.
All raw replies and original verdicts remain available.

Omission, changed number, negation, template-fidelity damage and unreadable image
controls met the original exact-category check in all three repeats. Provenance
failed that check: one valid reply called the bad assertion `unsupported` rather
than reporting the expected `provenance` category. This is a categorization miss,
not evidence that the model entirely overlooked the unsupported assertion.
Other categories or positive controls remained inconclusive, so semantic and
visible quality gates are not certified. Design stays advisory.

Reported judge usage for these 39 processes was **3,232,236 input** and
**360,697 output** tokens; 2,385,920 input tokens were reported as cached.
Cached tokens are part of the input count, not an additional amount. No monetary
cost is inferred. The generator was not called for this measurement.

## v6 protocol repair and saved-response audit

The validator now accepts exact quotations from native visible text and filtered
PDF-visible text, normalizing only formatting whitespace and bounded escaped
line wraps. Raw PDF text, speaker notes and off-slide text do not provide visible
coverage. Worker schemas constrain case, variant, pair and key-point IDs to the
supplied packet. Calibration requires detection in every affected variant;
duplicate variants use an explicit case-wide all-variants contract.

Invalid primary responses receive an independent second review. A valid second
answer cannot establish agreement with an invalid first answer. Both answers and
their errors remain available. Without an eligible calibration certificate, raw
positive and negative model verdicts are diagnostic; deterministic export
failures remain blocking.

The original 39 v5 responses were revalidated locally, with **zero new model
calls**, and without rewriting the original calibration. **32** now pass the
protocol and **7** remain rejected, compared with the original 28/11 split.
The audit retains hashes of the original responses and definitions. This is
`saved_response_protocol_revalidation`, explicitly **not a fresh calibration
certificate**. Residual grounding/coverage errors and provenance-category misses
leave the judge uncertified. Evidence:
`test-results/evaluation-implementation/protocol-revalidation/report.json`.

The seven residual rejections comprise four unknown key-point/coverage-ID cases
and three visual-region grounding mismatches. Some region mismatches may be
over-strict validation of a legitimate observation; they remain inconclusive,
not product defects. Two accepted provenance-control replies label the assertion
`unsupported` without the required `provenance` finding. The revised protocol was
not used to relabel those replies or turn the calibration green.

## Saved real-core evaluation through the public CLI

`judge --run test-results/evaluation-implementation/live-core-saved-review`
evaluated all four previously exported real cases without regeneration. The
collection is explicitly `reevaluation_of_four_saved_cases_not_a_new_e2e_run`:
three cases came from the historical core attempt, Adelphi from its separate
completed generation. Original execution manifests and deterministic verdicts,
including the historical mini-preset assertion error, were preserved.

All eight bounded Luna Max processes completed: four primary reviews and four
independent rechecks, with at most three concurrent. The worker interval was
approximately **18.3 minutes**, measured from saved worker durations and completion
timestamps. Evaluation source remained
unchanged. Three responses passed the then-current protocol; five failed
grounding validation. Binghamton brief's two valid reviews disagreed. All four
effective quality results therefore remained **inconclusive**. The combined
historical run remains failed because it retains deterministic failures, including
the real identical-variant defect. The unsuccessful v5 certificate was correctly
rejected as `calibration_not_passed`.

Reported judge usage: **1,520,703 input** and **114,171 output** tokens,
including 1,105,280 cached input tokens. Generator calls: **zero**. Evidence:
`test-results/evaluation-implementation/live-core-saved-review/reevaluation-7a211778.html`
and `judgment-ba6d48f0/` within that directory.

After this run, exact quotation grounding was extended to Russian guillemets
(`«…»`). A regression checks both legitimate quotes and rejection of changed
numbers/negation. The final judge/CLI suite passed **43 tests** and Ruff passed.
This bounded formatting fix recovers one additional saved response locally:
**4/8** accepted, **4/8** still rejected. The original judgments are unchanged;
this audit made zero model calls and creates no certificate. Evidence:
`test-results/evaluation-implementation/protocol-revalidation/saved-core-quote-audit.json`.

## Blind identical-input comparison

The public `compare` CLI was exercised on the same sealed synthetic clean-control
export on both sides: three variants, two slides each. Both independent workers
returned valid structured responses and tied every pair in semantics, visible
quality and design. They nevertheless incorrectly treated equality across
experiments as a duplicate-delivery defect; their findings differed enough to
make the final result **inconclusive**. This exposed an ambiguity in the paired
instruction, rather than an improvement or regression in presentation generation.
The original result is preserved at
`test-results/evaluation-implementation/compare-self-final/`.

That measurement used 320,306 input and 23,296 output tokens, including 219,648
cached input tokens. It took approximately 7.5 minutes for the two workers.
No generator was called. It is neither a product baseline/candidate improvement
comparison nor evidence that the judge is calibrated.

The paired contract was then corrected: A/B equality is a valid tie. Within-run
duplication stays in the whole-case judge and deterministic checks; it is excluded
from the paired schema and rejected locally if a response still reports it.
Two additional CLI cases cover version recording and source drift. The final
judge/CLI suite passed **46 tests**, including the cross-experiment duplicate
regression; Ruff passed. This prompt/schema change invalidates prior calibration
certificates by hash, and the original comparison is not rewritten.

One bounded verification of the corrected contract then completed at
`test-results/evaluation-implementation/compare-self-scope-fixed/`. Both workers
passed protocol validation, agreed on their semantic/visible assessments, and
returned **tie for all three pairs in all three categories**. The case-level
comparison passed; the overall result correctly remained **inconclusive** because
no qualifying calibration certificate was supplied. Source was unchanged and
both input identities and the evaluator snapshot were recorded. Reported usage
was **324,196 input** and **17,970 output** tokens, including 219,648 cached input
tokens; the generator was not called.

The harness is implemented and its replay/native-CLI paths have been exercised.
Mandatory model quality gating remains deliberately unavailable: initial product
ledgers still need independent review, calibration has not qualified, and some
real-case grounding disagreements remain unresolved. No real product improvement
between distinct baseline/candidate generators is claimed by the self-comparison.

## Complete unattended live core run

After the implementation checks, one fresh continuous `run --mode live --suite
core` was executed with the configured `qwen/qwen3.8-27b` product model, a
160-request cap, a 3,600-second deadline and the non-qualifying v5 calibration
file. Evidence: `test-results/evaluation-implementation/live-core-complete-20260929/`.
This is a genuine `end_to_end` run, not a collection of prior exports or a
generation-only run. Each case used isolated data and an empty application cache.

The command completed in **1,789.631 seconds (29m 50s)** without exhausting its
deadline. Both generation and evaluation source snapshots remained unchanged.
All four cases completed generation and export, producing **12 presentations / 48
slides**. Both browser workflows used the real server. No case was regenerated
to obtain a favorable outcome.

| Case | Generator requests | Slides per variant | Deterministic export checks |
| --- | ---: | ---: | --- |
| Binghamton content | 19 | 5 | Passed |
| Binghamton brief | 17 | 3 | Passed |
| A-State dark mixed | 20 | 3 | Passed |
| Adelphi dense 4:3 | 18 | 5 | **Failed: executive and analytical are pixel-identical** |

All eight independent Luna Max processes completed (four primary reviews and
four rechecks, maximum concurrency three). Seven responses passed protocol
validation. Adelphi's primary response failed exact/located evidence validation;
its secondary response validated, leaving agreement inconclusive. The Binghamton
brief reviews agreed; Binghamton content and A-State reviews disagreed. All
effective model quality outcomes remain **inconclusive**, including the agreed
case, because calibration is unqualified and the product references are still
silver. The full run correctly returns **failed** because of the independently
confirmed Adelphi duplicate, not because an uncalibrated model verdict was treated
as authoritative. The former Binghamton duplicate was not reproduced in this one
run; this does not establish that the underlying defect has been fixed.

Generator usage was reported for all **74/74 physical requests**: 275,992 input
and 10,968 output tokens (3,200 cached input tokens included in the input count).
Judge usage was 1,448,724 input and 122,272 output tokens (1,030,016 cached input
tokens included). The two usages are separate; no monetary cost is inferred.
The HTML report is `live-core-complete-20260929/report.html`; raw responses,
provider requests, browser evidence, sealed exports and both reviews remain in
the same run directory.
