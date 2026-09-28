# Readable stacked columns

The chart box minimum did not account for plot height consumed by category labels,
legend and total captions. Native rendering could leave a nearly flat plot while
pre-export checks accepted the enclosing box.

Stacked columns now share a measured layout between selection and native export.
Generic scenes use the vertical interval below the title and above the footer;
width increases in bounded steps only when labels need it and prose still fits.
Authored layouts retain their region contracts. Legends use 12–14pt text; source
totals are paired with their series instead of duplicating the legend in a caption.
Long category labels wrap without breaking identifiers. Numbers remain 16pt;
short-segment labels are separated with leaders when required. Charts, legends,
labels and leaders remain editable PowerPoint objects. The auxiliary labels are
separate text objects and do not automatically recalculate after manual chart-data edits.

The exported group identifies native-chart annotations. Only known, contained
labels may overlap their own chart; text/text overlap, bounds, font fit and
unrelated content still undergo the usual checks. Export validates every source
total/series pair and rejects changed or missing totals. Insufficient space
remains blocking. No template names, hashes or slide-number conditions exist.

Validation:
- 45 unique targeted backend checks passed, including 10 new regression cases.
- 6 browser scenarios and 7 frontend label checks passed; Ruff/diff checks passed.
- Offline replay of the saved 11-slide executive variant: native LibreOffice
  rendering, no object geometry findings and no export evidence findings.
- Plot height on the two reported slides is approximately 173/181pt; all source
  rows and totals retained. Original slides 7–9 render pixel-identically.
- No model run, benchmark or user-job history mutation. This is not a new full
  model/VL acceptance run or a complete cross-office compatibility matrix.

Artifacts remain locally under test-results/stacked-height-fix-full/.
