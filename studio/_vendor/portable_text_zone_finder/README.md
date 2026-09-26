# Portable text zone finder

Finds one conservative axis-aligned rectangle where black or white text can
be placed on an **already extracted** slide background. The package is separate
from the PowerPoint background extractor and from the application's harness.

Python 3.11+ is required. Install the three declared dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run on a PNG without PPTX metadata:

```bash
.venv/bin/python find_text_zones.py background.png result_dir
```

For backgrounds produced by the portable background extractor, keep each
deck's `model.json` next to its `background/` folder. The CLI discovers the
model and matches `001.png` to `sourceSlideNumber: 1`:

```text
corpus/
  deck-a/
    model.json
    background/001.png
    background/002.png
```

```bash
.venv/bin/python find_text_zones.py corpus result_dir --vl
```

Use repeated `--include` globs to audit only selected decks in a large corpus.

`--vl` calls an OpenAI-compatible Chat Completions endpoint. Set
`LLM_CHAT_COMPLETIONS_URL` and `LLM_API_KEY` in the environment. The model is
fixed to `qwen3.8-27b-noreason` with `reasoning_effort: none`; the ordinary
reasoning model is never called. Each unique image receives two grid prompts.
Responses are cached under `result_dir/responses/`. To reuse a cache without
calling the endpoint, pass `--vl-cache /path/to/responses` and omit `--vl`.
Flat backgrounds are settled by pixels and OOXML geometry without a VL call.

For a single PNG with a separate model file, pass `--model model.json --slide 1`.
For a complex photographic background without a VL response, the tool may
return `null` rather than invent a safe area.

## Outputs

- `report.json`: one entry per image. `box` uses source PNG pixels in the
  half-open form `[left, top, right, bottom]`; `text_color` is `black` or
  `white`. `box: null` means no sufficiently safe rectangle was found.
- `overlays/*.png`: source image with the candidate outlined in green.
- `responses/*.json`: cached VL answers, if `--vl` was used.

Algorithm: combine visible pixel differences, protected OOXML regions, and
VL's 8×8 artwork grid. Keep a 24 px clearance from blocked pixels and 40 px
from the slide edge. Require 4.5:1 pixel contrast across the entire rectangle,
then find the largest rectangle made of fully safe 16×16 tiles. Flat text
panels over photographs are recognized from `model.json`. See `AGENTS.md` for
the invariants behind these rules.
If the VL grid blocks all useful space, a second pass checks local texture and
edges to recover a genuinely smooth panel or open card without ignoring logos.

## Audit result

The first 15-deck audit covered 152 slides (80 unique backgrounds). The
algorithm returned rectangles for 149 slides and abstained on three full-photo
TAMU slides.

The next 20 real templates covered 367 slides. After background corrections,
the algorithm returned 355 rectangles and abstained on 12 photographic or
ornamental slides. Every returned rectangle was checked against the extracted
`protectedRegions`: zero overlaps, with minimum measured contrast 4.59:1.
These checks do not guarantee that arbitrary text length will fit in a
rectangle.

