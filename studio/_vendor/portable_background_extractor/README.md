# Portable PowerPoint background extractor

Extracts the original background, decoration and corporate identity from every
slide of a PPTX/POTX. The result is an editable PPTX with the original theme,
layouts, masters and surviving OOXML artwork. Sample text, subject photos,
charts and replaceable placeholders are removed. The optional JSON report gives
an action and reason for every classified shape.

Requires Python 3.10+, Pillow 12 and HTTPX. If Tesseract OCR is installed and on
`PATH`, the extractor also clears recognized sample wording embedded inside
otherwise valid identity PNGs. This is optional; without Tesseract those pixels
remain unchanged. No Agentico checkout, harness, template collection, network
service or LibreOffice is needed to extract a presentation.

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python extract_backgrounds.py input.pptx background.pptx --report decisions.json
```

Some PPTX files bake example cards or charts into an otherwise smooth raster
background. For those files, enable the optional VL check with an
OpenAI-compatible Chat Completions endpoint. It locates subject regions and
reconstructs the underlying smooth canvas from nearby pixels. The JSON report
records every modified asset and region. The default model is
`qwen3.8-27b-noreason` with `reasoning_effort: none`. It checks each smooth
canvas twice, including a contrast-enhanced view to catch pale empty cards.
This check also covers raster images stored in the slide, layout or master
background fill (`p:bg`), which are not ordinary PowerPoint shapes.

```sh
export VL_CHAT_COMPLETIONS_URL="https://example.com/v1/chat/completions"
export VL_API_KEY="..."
.venv/bin/python extract_backgrounds.py input.pptx background.pptx --vl \
  --vl-cache .vl-cache --report decisions.json
```

The VL option requires network access and is applied only to bright raster
canvases with nearly uniform edge colors. It preserves the original PPTX
geometry and all separate vector and identity layers. Reconstructed pixels
under a baked card are an estimate, so inspect the reported regions when exact
pixel matching matters.
The optional cache lets a restarted run reuse completed VL decisions for the
same model and image bytes.

On Windows, use `.venv\Scripts\python.exe` instead. The source can also be a
`.potx` template. Output is always `.pptx`. Slide order follows the presentation,
including files whose slide XML numbers differ from display order.

Run `python selftest.py` for small built-in checks that need no sample decks.

The classifier uses shape metadata, OOXML placeholders and groups, media
relationships, recurrence across slides, image transparency/palette evidence and
slide/layout/master inheritance. Ambiguous artwork is preserved. The optional
VL mode is needed to remove content embedded inside a smooth raster background.
It retains photo collages under a translucent full-slide mask and removes
one-off SVG subject icons attached to discarded photos, while preserving SVG
motifs repeated across the deck.
Large quotation marks that frame a quote layout are treated as decoration and
kept; their sample quote text is removed.

The package writer removes unused media, speaker notes and comments from the
output archive. OCR edits are listed in the optional JSON report. It does not
require a renderer; for a visual check, open both files in PowerPoint or
LibreOffice.

