"""Safe adapter for the colleague's static OOXML color extractor.

Role tokens are evidence, not a substitute for rendering/compositing.
"""

from collections import Counter, defaultdict
import json
from .security import validate_pptx
from ._vendor.color_extraction.pipeline.reference_color_usage import extract_reference_color_usage


def extract_colors(path, directory):
    # Also protects direct callers: XML entities, duplicate/unsafe ZIP paths,
    # macros, expansion limits. No external relationship is fetched.
    validate_pptx(path)
    report = extract_reference_color_usage(path.read_bytes(), path.name)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "color-model.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    counts = defaultdict(Counter)
    for slide in report["slides"]:
        for use in slide["uses"]:
            # Translucent paint and gradient stops aren't a solid visible RGB.
            if use["opacity"] == 1 and "component" not in use:
                counts[use["role"]][use["color"]] += 1
    roles = {role: [color for color, _ in values.most_common()] for role, values in counts.items()}
    summary = {
        "status": "completed",
        "method": "static_ooxml",
        "slides": report["slideCount"],
        "uses": sum(len(s["uses"]) for s in report["slides"]),
        "unresolved": sum(len(s["unresolved"]) for s in report["slides"]),
        "excluded": sum(len(s["excluded"]) for s in report["slides"]),
        "report": "color-model.json",
        "visual_accuracy_verified": False,
    }
    table_styles = {}
    for slide in report["slides"]:
        paints = {"header": Counter(), "body": Counter()}
        for use in slide["uses"]:
            if use["role"] == "table.fill" and "component" not in use:
                row = "header" if ":r0c" in use["shapeId"] else "body"
                paints[row][(use["color"], use["opacity"])] += 1
        if any(paints.values()):
            table_styles[str(slide["number"])] = {
                role: {
                    "color": value.most_common(1)[0][0][0],
                    "opacity": value.most_common(1)[0][0][1],
                }
                for role, value in paints.items()
                if value
            }
    summary["table_styles"] = table_styles
    return roles, summary
