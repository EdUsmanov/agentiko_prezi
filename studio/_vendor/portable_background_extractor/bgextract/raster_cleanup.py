"""Remove recognized sample wording embedded in otherwise valid identity PNGs."""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
from collections import defaultdict

from PIL import Image, ImageDraw, UnidentifiedImageError


def clean_identity_png(payload: bytes) -> tuple[bytes, list[str]]:
    """Clear only OCR-localized placeholder words; leave the rest pixel-identical."""
    if not shutil.which("tesseract"):
        return payload, []
    try:
        with Image.open(io.BytesIO(payload)) as source:
            original = source.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError):
        return payload, []
    if original.width < 100 or original.height < 40:
        return payload, []
    scan = original.copy()
    scan.thumbnail((3000, 3000))
    white = Image.new("RGBA", scan.size, "white")
    white.alpha_composite(scan)
    stream = io.BytesIO()
    white.convert("RGB").save(stream, format="PNG")
    try:
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "11", "tsv"],
            input=stream.getvalue(),
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return payload, []
    if result.returncode:
        return payload, []
    lines: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for word in csv.DictReader(
        io.StringIO(result.stdout.decode("utf-8", "replace")), delimiter="\t"
    ):
        if word.get("level") == "5" and word.get("text", "").strip():
            lines[(word["block_num"], word["par_num"], word["line_num"])].append(word)
    pairs: list[tuple[str, list[dict]]] = []
    for words in lines.values():
        tokens = [word["text"].strip(".,:;!? ").casefold() for word in words]
        for phrase in (("college", "name"), ("goes", "here")):
            for index in range(len(tokens) - 1):
                if tuple(tokens[index : index + 2]) == phrase:
                    pairs.append((" ".join(phrase), words[index : index + 2]))
    # The two phrases together identify a replaceable label inside a brand asset.
    if {name for name, _ in pairs} != {"college name", "goes here"}:
        return payload, []
    drawing = ImageDraw.Draw(original)
    scale_x, scale_y = original.width / scan.width, original.height / scan.height
    for _, words in pairs:
        for word in words:
            left = int(word["left"])
            top = int(word["top"])
            width = int(word["width"])
            height = int(word["height"])
            margin = max(4, round(height * 0.06))
            drawing.rectangle(
                (
                    max(0, round((left - margin) * scale_x)),
                    max(0, round((top - margin) * scale_y)),
                    min(original.width, round((left + width + margin) * scale_x)),
                    min(original.height, round((top + height + margin) * scale_y)),
                ),
                fill=(0, 0, 0, 0),
            )
    output = io.BytesIO()
    original.save(output, format="PNG")
    return output.getvalue(), ["college name", "goes here"]

