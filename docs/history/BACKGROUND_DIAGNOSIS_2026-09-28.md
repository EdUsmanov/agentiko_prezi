> Исторический отчёт из main до объединения с модульным backend. Пути модулей отражают состояние на дату отчёта. Текущая архитектура: [ARCHITECTURE.md](../../ARCHITECTURE.md).

# Repeated background diagnosis

The supplied template matches the saved input byte-for-byte. The analyzed profile
contains 36 patterns, with blue, white, pink and lavender backgrounds. The final
executive variant has a pink cover and ten blue content slides; eight content
slides use token_composition. The background report counts three artwork families,
which does not mean three visible background colors.

Confirmed causes:
1. contracts.candidates prefers authored example-slide fields whenever available
   for summarized editorial content, before knowing that those fields cannot fit.
   This removes master/layout alternatives from later selection.
2. background_diversity searches with source_slides_only=True, excluding masters.
   A white two-column master works for slide 2 without candidate regressions, but
   cannot enter this search.
3. Several authored colorful fields are narrow/short (roughly 244–320 by 118pt),
   unsuitable for the data plus prose in this input. Those failures legitimately
   lead to token composition; composer then uses the profile's base blue color.
4. The fallback preserves fonts/palette but does not independently choose safe
   source backgrounds. Consequently the diversity pass cannot recover variety.

No background-selection behavior was changed in the chart fix. Recommended next
change: try compatible validated masters after authored layouts fail, then add a
separate quality-guarded source-background selection for generic compositions.
Preserve source art, verify safe text/data regions and contrast; do not merely
cycle palette colors or loosen geometry checks to claim diversity.
