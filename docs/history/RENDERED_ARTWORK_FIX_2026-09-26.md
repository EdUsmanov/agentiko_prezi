> Исторический снимок. Текущее устройство — [ARCHITECTURE.md](../../ARCHITECTURE.md), запуск — [TEAM_SETUP.md](../../TEAM_SETUP.md).

# Native American Heritage: body fields over inherited artwork

## Evidence and cause

The paid benchmark 06-main and 06-experiment finished needs_review. Manual QA
found body captions over feather/leaf artwork; the model visual critic reported
no such error. Original exports are preserved, with separate changes_required
reviews in the benchmark workspace.

`native_patterns` inspected direct slide pictures, but the actual background also
contains inherited master/layout artwork. The global portable detector returned
an unknown rectangle for this complex surface. `compile_backgrounds` previously
kept the full authored body field, including the decorated lower-left area.

## Change in both branches

After rendering the sanitized background, constrain each body field separately
using the existing conservative clear-area detector. The complete raster includes
master/layout decoration and grouped artwork. Keep source shape IDs, per-field
identity and artwork unchanged; never merge cards, expand fields or repaint them.
Synchronize body_zones, text_zones and native object contracts before planning.
Record before/after rectangles in safe_text_zone.body_adjustments/text-zones.json.
An unknown global zone remains unknown; this heuristic is not a visual proof.

For native-slide-2, the usable body rectangle changes from
(230.4,147.35,656.4,335.425) to (271.425,147.35,615.375,218.026) points.
Existing physical fitting still controls readable table/chart/text sizes. In the
offline main example the caption fits at 16pt; the original had 22pt with overlap.
Neither engine, semantic data, retry policy nor analysis deadline was changed.

## Verification

- Main: 52 tests passed; experiment: 61 tests passed. Includes native field and
  card preservation, scaled raster geometry, actual background compilation and
  persisted diagnostics, semantic contracts, charts and bounded analysis retries.
- The initial run had three invalid new fixtures (missing required Pattern.role)
  and one stale gateway mock using an integer calls field. Fixed fixtures, not
  production policy: the real gateway.calls is a metrics list; test counter is
  now requests. The same retry-count assertion remains.
- `template-benchmark/verify_heritage_artwork.py main|experiment` produced separate
  local native exports via composition or PPTAgent contracts. All ten PNGs were
  visually inspected: captions clear the decoration; chart/table values retained.
  This is an offline regression diagnostic, NOT another paid engine run.
- SHA256 checks verify 24 original inputs/results/exports per branch unchanged.
- JUnit: template-benchmark/test-results/rendered-artwork-*-final.xml.

The original runs remain changes_required. Limited variant diversity and sparse
cover/closing composition are still visible. Final visual QA remains necessary.
