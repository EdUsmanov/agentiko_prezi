> Исторический снимок. Текущее устройство — [ARCHITECTURE.md](../../ARCHITECTURE.md), запуск — [TEAM_SETUP.md](../../TEAM_SETUP.md).

# Adaptive readability correction

## Failure mechanism

Analysis could enter a shortening loop even when the editable prose already fit. A cover's background check could leave a title field shorter than one readable line. A source table could retain a small authored font, and equally sized columns wasted space on short numeric values while wrapping long labels. Further prose shortening cannot repair these layout defects.

## Changes

- A cover with unusable title/subtitle fields may gain a derived composition inside the already measured clear background region. The source artwork is retained. No new region is guessed, no template name or content fingerprint selects behavior, and low contrast, absent or out-of-bounds regions are rejected. Other semantic layouts retain their field bindings.
- Table columns share space according to measured text and the actual bold header face. Audit, native selection, repair, PPTX, PDF and HTML use the same widths. Source cells and numeric values remain unchanged.
- An authored small body/table font no longer caps the initial composition below 16 pt. Overflow and readability checks remain active.
- If automatic native table composition still fails geometry/readability, the same complete compositor can try the existing template-font/palette layout. It is accepted only if all checked geometry/readability defects are gone. An explicitly selected pattern remains binding. Cover/divider layouts are not replaced by this table fallback. This may reduce fidelity to an individual source layout; it is not a guarantee of manual design acceptance.
- Native chart captions reserve the same measured space in selection, audit and export. Mixed-unit captions group values under a shared column/category heading without dropping categories, values or totals. A chart that cannot fit its captions is rejected before export.
- Technical retry/error text in preparation and generation phase labels is shown as a neutral checking status. Raw job phases and error details remain available in the journal.

## Validation

Synthetic regression cases exercise different canvas sizes, safe-region rejection, native layout fallback and exact preservation of exported table cells. Existing editorial, semantic, font, quality and export tests are run alongside them. 126 targeted Python regression tests passed across the selected suites; 7 pure JavaScript phase-label checks passed and are included in CI. This is not a claim that the full post-push suite has already passed.

## Live frontend verification (completed 2026-09-27)

- Analysis `7c7e1bfd97d54fe6a36e8f6522469bfd` was started by clicking the existing Chrome form. It reached `ready`, with a seven-slide plan, accepted on the first editorial pass without a shortening repair loop.
- Generation was also started through the UI. The first attempt exposed missing caption-space validation; the next exposed the export audit's expectation of the older caption format. Both causes were corrected; validators still reject altered or missing source values.
- Final generation `0195de89a7c1431b906763d6190c4a8e` reached `needs_review` in 42.769 seconds: executive, analytical and story each contain 7 slides; 0 errors and 9 warnings. The ZIP contains 20 files and passes its integrity check.
- Visual checking covers all 21 slides. The final run reused content-addressed visual checks from the preceding equivalent render; it did not spend new model calls on unchanged images. Full manual design acceptance is still open.
- Direct offline replays of both previously failing plans also pass their geometry gate. Synthetic tests and the implementation contain no template-name, fingerprint, participant-name or source-number exceptions.
- The raw retries and failure details remain in the job journals. Failed attempts were retained for diagnosis; the final successful result has its own ID.
