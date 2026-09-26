# Adaptive analysis fixes — 2026-09-27

## Reproduced failure

Two user analyses (`9879a9219ee44d4ead7e2b0545a65005`,
`672bf1b409c6488399382029c65c7eda`) stopped while repeatedly shortening slide 5.
The actual defect was a four-column table rendered at 14 pt. Prose was already
22 pt and the title 32 pt. All table rows inherited the multi-line header's
height, wasting space on one-line numeric rows. Shortening adjacent prose could
not fix this geometry.

The uploaded template's SHA-256 matches earlier successful analyses, but those
used an older preparation pipeline. There is no filename/template-ID exception
in this fix. No model training or provider change was performed.

## Changes

- Measure each table row independently, including the actual bold header face,
  wrapping, padding and available box. Selection, repair, audit, PPTX, PDF and
  HTML share the calculation. Never enlarge a table past its reserved region.
- Consider the 16 pt readability floor even if the source font scale skips it.
  Genuine cell overflow still fails validation; source cells remain intact.
- Identify the exact element in readability diagnostics.
- Allow longer semantic-review explanations (bounded at 2,000 characters).
  These are diagnostics, not slide copy. Negative verdicts, source ID checks,
  complete claim coverage and independent semantic review remain enforced.
- Expose an existing cover's one-subtitle/140-character constraint in the
  targeted repair schema and prompt. Restoring a caveat cannot add a second
  cover bullet or rewrite accepted neighbouring slides.
- Require the editor and independent reviewer to preserve data-status caveats
  (illustrative, hypothetical, simulated, estimated or preliminary figures)
  as visible, grounded qualifications rather than dismissing them as metadata.
- Reconcile stale omission metadata with already validated citations, as was
  already done for displayed source tables and targeted patches. Unknown and
  duplicate omission IDs still fail. The independent reviewer receives the
  complete source and all genuinely omitted facts.

## Evidence

Local replay of the unchanged failed plan passes analysis geometry and exports
three six-slide decks through native PPTX/LibreOffice/PDF/HTML. Actual export
audits return no findings for all three variants. The formerly failing table
uses 16 pt and retains every header, row and value. Evidence is local, outside
Git: `/tmp/native-heritage-debug/export-evidence.json`.

A saved earlier VK Tech plan also passes the new geometry checks and native
export. Synthetic regression cases use two font families, different widths,
row counts, multi-line headers/body rows, and deliberately insufficient space.
They reopen actual PPTX tables and check row geometry, data and readable type.

Final local regression suite: **136 passed in 210.88 seconds** across 14 test
modules (table geometry/exports, semantic bindings, field fonts, quality policy,
editorial repair, analysis/design regressions). The preceding commit's GitHub
CI completed successfully; this does not establish the new commit's CI result.

Frontend run `7b1cb0b15e964ca682f4e9e3b35bdd01` completed analysis in 87.923
seconds; generation `e967f8328f634d36b0833f7ce54eb669` exported three six-slide
variants, zero errors and all 18 rendered slides checked. Manual inspection
found that the semantic reviewer had incorrectly accepted omission of the
source's illustrative-data caveat. The prompt correction above is followed by
a new live run; that final run is recorded below. No full manual design
acceptance is implied by technical success.

## Final live verification

- Analysis: `c0265893ab60466198bd9c34235241ab`, `ready`, 82.654 seconds
  for the analysis stage (83.4 seconds shown by the frontend), six slides.
- Generation: `2341987de54745ffb399353fa7e9ca8a`, `needs_review`,
  47.637 seconds (48.2 seconds in the frontend), three six-slide presentations.
- Quality report: **0 errors, 9 warnings**; rendered-slide review completed
  **18/18**. The warnings include adjusted count, use of some general layouts,
  and insufficient verified composition diversity. They are not silenced.
- Reopened all three PPTX files: each has six slides and visibly includes
  “Все цифры иллюстративные”. Actual export audits have zero errors.
- ZIP integrity passes (20 entries). The frontend exposes PPTX/PDF/HTML and ZIP
  download links for this generation.
- Original and replayed analysis inputs are byte-equivalent after JSON parsing;
  the uploaded template bytes are identical. No source material was rewritten
  to make the checks pass.

Local artifacts and model traces remain in ignored `data/jobs/` and are not
included in Git. The final run validates this failure and the shared paths;
it is not a claim of universal compatibility or manual acceptance of all
compositions.
