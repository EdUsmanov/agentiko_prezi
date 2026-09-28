# Table readability during analysis

Generic compositions reserved 62% of content width for a table even when its
headers needed more space. Fitting reduced 16pt data to 12pt; analysis correctly
rejected it as unreadable with LayoutCapacityError.

The shared composition path now tries wider side-by-side allocations and a
full-width table below its explanation. It keeps all cells, source links, text,
fonts and colors. Candidates pass existing fit and regression checks after
fitting. Authored regions are untouched. If no safe candidate exists, the
original blocking failure remains. No template-specific rules were added.

Validation: 52 composition/routing/chart tests and 56 repair/quality/table/replay
tests passed; 6 browser scenarios and 7 frontend label checks passed; Ruff and
diff checks passed. Four strengthened regression cases fail with the adapter
disabled in memory and pass when enabled. An oversized table still blocks.

Saved failed input replay without model calls: six-slide analysis geometry
passes; slide 5 retains all four columns and six data rows at 16pt in all three
variants. This is not a complete model run or visual acceptance.

Local server was not restarted; no benchmark or user generation was launched.
