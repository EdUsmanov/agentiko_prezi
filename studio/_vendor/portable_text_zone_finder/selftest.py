"""Small behavioral checks; run with `python selftest.py` from this folder."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from textzone import analyze_image


def _overlap(box: list[int], region: tuple[int, int, int, int]) -> int:
    left, top, right, bottom = box
    x1, y1, x2, y2 = region
    return max(0, min(right, x2) - max(left, x1)) * max(0, min(bottom, y2) - max(top, y1))


def test_protected_identity() -> None:
    image = Image.new("RGB", (1280, 720), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((830, 180, 1130, 510), fill="#171717")
    slide = {
        "protectedRegions": [{"kind": "identity", "x": 830, "y": 180, "width": 301, "height": 331}],
        "objects": [],
    }
    result = analyze_image(image, slide)
    assert result["box"] is not None
    assert _overlap(result["box"], (830, 180, 1131, 511)) == 0
    assert result["minimum_contrast"] >= 4.5


def test_photo_abstention_and_flat_panel() -> None:
    noise = np.random.default_rng(123).integers(0, 256, (720, 1280, 3), dtype=np.uint8)
    photo = Image.fromarray(noise)
    assert analyze_image(photo)["box"] is None
    draw = ImageDraw.Draw(photo)
    draw.rectangle((160, 180, 1060, 520), fill="white")
    slide = {
        "protectedRegions": [],
        "objects": [
            {
                "type": "sp",
                "role": "decoration",
                "box": [160 / 1280, 180 / 720, 900 / 1280, 340 / 720],
            }
        ],
    }
    all_cells = {f"{col}{row}" for col in "ABCDEFGH" for row in "12345678"}
    result = analyze_image(photo, slide, all_cells)
    assert result["recognition_mode"] == "vl_with_flat_panel"
    assert result["box"] is not None
    assert result["minimum_contrast"] >= 4.5


def test_local_panel_recovery_and_photo_abstention() -> None:
    noise = np.random.default_rng(321).integers(0, 256, (720, 1280, 3), dtype=np.uint8)
    photo = Image.fromarray(noise)
    all_cells = {f"{col}{row}" for col in "ABCDEFGH" for row in "12345678"}
    assert analyze_image(photo, vl_cells=all_cells)["box"] is None

    draw = ImageDraw.Draw(photo)
    draw.rectangle((80, 152, 445, 580), fill="#9e1717")
    # A hairline inside the title panel defeats the strict whole-panel check.
    # The local pass must still find a text zone above or below that line.
    draw.line((81, 410, 444, 410), fill="#770f0f", width=1)
    slide = {
        "protectedRegions": [],
        "objects": [
            {
                "type": "sp",
                "role": "decoration",
                "box": [80 / 1280, 152 / 720, 365 / 1280, 428 / 720],
            }
        ],
    }
    result = analyze_image(photo, slide, all_cells)
    assert result["recognition_mode"] == "local_surface_fallback"
    assert result["box"] is not None
    left, top, right, bottom = result["box"]
    assert 80 < left < right < 445
    assert 152 < top < bottom < 580
    assert result["minimum_contrast"] >= 4.5


def test_cli_corpus_contract() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        background = root / "deck" / "background"
        background.mkdir(parents=True)
        Image.new("RGB", (1280, 720), "#ffffff").save(background / "001.png")
        (root / "deck" / "model.json").write_text(
            json.dumps(
                {"slides": [{"sourceSlideNumber": 1, "objects": [], "protectedRegions": []}]}
            )
        )
        output = root / "result"
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / "find_text_zones.py"),
                str(root / "deck"),
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads((output / "report.json").read_text())
        assert report["background/001.png"]["box"] is not None
        assert (output / "overlays" / "background" / "001.png").exists()


if __name__ == "__main__":
    test_protected_identity()
    test_photo_abstention_and_flat_panel()
    test_local_panel_recovery_and_photo_abstention()
    test_cli_corpus_contract()
    print("selftest passed")

