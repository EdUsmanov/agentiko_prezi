"""Safe adapter for the colleague's static OOXML color extractor.

Role tokens are evidence, not a substitute for rendering/compositing.
"""

from collections import Counter, defaultdict
import json
from studio.security import validate_pptx
from studio.templates.template_geometry import contrast
from studio._vendor.color_extraction.pipeline.reference_color_usage import (
    extract_reference_color_usage,
)


def color_schemes(report, title_foregrounds=None):
    """Keep background and typography from the same source slides together."""
    title_foregrounds = title_foregrounds or {}
    observations = []
    for slide in report["slides"]:
        roles = defaultdict(Counter)
        for use in slide["uses"]:
            if use["opacity"] == 1 and "component" not in use:
                roles[use["role"]][use["color"]] += 1
        background = roles["background"].most_common(1)
        text = Counter()
        for role, weight in (
            ("text.body", 3),
            ("text.title", 2),
            ("text.other", 1),
            ("text.table", 1),
        ):
            for color, count in roles[role].items():
                text[color] += count * weight
        canvas = background[0][0] if background else ""
        ink = title_foregrounds.get(slide["number"]) or (text.most_common(1)[0][0] if text else "")
        # White text inside a colored card on a white slide is not white
        # canvas text. Leave the canvas pairing unresolved for pixel analysis.
        if canvas and ink and contrast(canvas, ink) < 3:
            ink = ""
        observations.append(
            {
                "slide": slide["number"],
                "background": canvas,
                "foreground": ink,
                "roles": roles,
            }
        )
    # A decorative slide without text inherits the dominant text scheme for
    # its own background, not an unrelated color from another canvas.
    by_background = defaultdict(Counter)
    for item in observations:
        if item["background"] and item["foreground"]:
            by_background[item["background"]][item["foreground"]] += 1
    grouped = {}
    for item in observations:
        background = item["background"]
        foreground = item["foreground"] or (
            by_background[background].most_common(1)[0][0] if by_background[background] else ""
        )
        key = (background, foreground)
        if key not in grouped:
            grouped[key] = {
                "id": f"scheme-{len(grouped) + 1}",
                "background": background,
                "foreground": foreground,
                "slides": [],
                "text_colors": Counter(),
                "fill_colors": Counter(),
            }
        scheme = grouped[key]
        scheme["slides"].append(item["slide"])
        for role, paints in item["roles"].items():
            if role.startswith("text."):
                scheme["text_colors"].update(paints)
            elif role in ("shape.fill", "table.fill"):
                scheme["fill_colors"].update(paints)
    schemes = []
    mapping = {}
    for scheme in grouped.values():
        scheme["text_colors"] = [color for color, _ in scheme["text_colors"].most_common()]
        scheme["fill_colors"] = [color for color, _ in scheme["fill_colors"].most_common()]
        schemes.append(scheme)
        mapping.update({str(number): scheme["id"] for number in scheme["slides"]})
    return schemes, mapping


def pattern_color_context(pattern):
    """Only measured source pairs; empty values mean the color is unresolved."""
    return {
        "scheme_id": pattern.color_scheme_id,
        "title": {
            "background": pattern.title_background,
            "text": pattern.title_foreground,
        },
        "body": [
            {
                "background": pattern.zone_backgrounds[index]
                if index < len(pattern.zone_backgrounds)
                else "",
                "text": pattern.zone_foregrounds[index]
                if index < len(pattern.zone_foregrounds)
                else "",
            }
            for index in range(len(pattern.body_zones))
        ],
    }


def resolve_rendered_schemes(profile):
    """Group observed editable title surfaces, including raster backgrounds."""
    groups = []
    slide_schemes = {}
    for pattern in profile.patterns:
        if not pattern.source_slide or not pattern.title_background or not pattern.title_foreground:
            continue
        background = pattern.title_background
        foreground = pattern.title_foreground
        rgb = tuple(int(background[index : index + 2], 16) for index in (1, 3, 5))
        scheme = next(
            (
                group
                for group in groups
                if group["foreground"] == foreground
                and max(
                    abs(a - b)
                    for a, b in zip(
                        rgb,
                        tuple(
                            int(group["background"][index : index + 2], 16) for index in (1, 3, 5)
                        ),
                    )
                )
                <= 24
            ),
            None,
        )
        if scheme is None:
            scheme = {
                "id": f"rendered-scheme-{len(groups) + 1}",
                "background": background,
                "foreground": foreground,
                "slides": [],
                "method": "rendered_title_field",
            }
            groups.append(scheme)
        if pattern.source_slide not in scheme["slides"]:
            scheme["slides"].append(pattern.source_slide)
        slide_schemes[str(pattern.source_slide)] = scheme["id"]
        pattern.color_scheme_id = scheme["id"]
    if not groups:
        return
    profile.color_analysis.setdefault("static_schemes", profile.color_schemes)
    profile.color_analysis["schemes"] = groups
    profile.color_analysis["slide_schemes"] = slide_schemes
    profile.color_schemes = groups
    dominant = max(groups, key=lambda group: len(group["slides"]))
    profile.background = dominant["background"]
    profile.foreground = dominant["foreground"]


def agent_color_context(profile):
    return {
        "source_schemes": [
            {
                "id": scheme["id"],
                "background": scheme["background"],
                "text": scheme["foreground"],
                "slides": scheme["slides"],
            }
            for scheme in profile.color_schemes
        ],
        "patterns": {pattern.id: pattern_color_context(pattern) for pattern in profile.patterns},
    }


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
    schemes, slide_schemes = color_schemes(report)
    summary = {
        "status": "completed",
        "method": "static_ooxml",
        "slides": report["slideCount"],
        "uses": sum(len(s["uses"]) for s in report["slides"]),
        "unresolved": sum(len(s["unresolved"]) for s in report["slides"]),
        "excluded": sum(len(s["excluded"]) for s in report["slides"]),
        "report": "color-model.json",
        "visual_accuracy_verified": False,
        "schemes": schemes,
        "slide_schemes": slide_schemes,
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
