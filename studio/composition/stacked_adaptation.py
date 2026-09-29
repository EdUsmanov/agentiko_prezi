"""Scene-level stacked chart repair, separate from rendering and measurement."""

from studio.templates.fonts import element_font, wrap_text
from studio.composition.stacked_chart import stacked_layout


def adapt_stacked_scene(scene, package):
    """Use available vertical space and measured width inside generic slide bounds."""
    if scene.strategy != "token_composition" or scene.pattern_id:
        return scene
    indices = [
        i
        for i, e in enumerate(scene.elements)
        if e.kind == "chart" and e.chart_type == "column_stacked"
    ]
    if len(indices) != 1:
        return scene
    from studio.checks.audit import audit_scenes
    from studio.models import Box
    from studio.checks.quality import candidate_regressions
    from studio.checks.repair_policy import FIT_CODES

    index = indices[0]
    chart = scene.elements[index]
    body = [
        i
        for i, e in enumerate(scene.elements)
        if e.kind == "text" and e.role == "body" and e.source_ids
    ]
    title_bottom = max(
        (e.box.y + e.box.h for e in scene.elements if e.role == "title"), default=chart.box.y - 8
    )
    top = max(package.template.margin, title_bottom + 8)
    bottom = min(
        [package.template.height - package.template.margin / 2]
        + [e.box.y - 8 for e in scene.elements if e.role == "footer"]
    )
    left = chart.box.x
    right = package.template.width - package.template.margin
    width = right - left
    if width <= 0 or bottom <= top:
        return scene
    gap = package.template.width * 0.025

    def accepted(candidate):
        return not any(
            f.code in FIT_CODES for f in audit_scenes([candidate], package)
        ) and not candidate_regressions([scene], [candidate], package)

    for fraction in [0.62 + step * 0.005 for step in range(41)] if body else (1,):
        candidate = scene.model_copy(deep=True)
        e = candidate.elements[index]
        e.box = Box(x=left, y=top, w=width * fraction, h=bottom - top)
        if not stacked_layout(e, package.template)["fits"]:
            continue
        y = top
        for i in body:
            text = candidate.elements[i]
            x = left + e.box.w + gap
            available = right - x
            if available <= 0:
                break
            measured = (available - (text.size * 1.4 if text.bullet else 0)) * (
                0.94 if text.bold or text.bold_prefix else 1
            )
            if measured <= 0:
                break
            h = (
                len(
                    wrap_text(
                        text.text, element_font(package.template, text)[1], text.size, measured
                    )
                )
                * text.size
                * 1.25
            )
            text.box = Box(x=x, y=y, w=available, h=h)
            y += h + 8
        else:
            if y - 8 <= bottom and accepted(candidate):
                return candidate
    # Long category names can require a wider chart than a side column allows.
    # Keep each claim intact below it, using the same measured text and audit gates.
    for compact in (False, True) if body else ():
        candidate = scene.model_copy(deep=True)
        heights = []
        for i in body:
            text = candidate.elements[i]
            if compact:
                text.size = min(text.size, 16)
            measured = (width - (text.size * 1.4 if text.bullet else 0)) * (
                0.94 if text.bold or text.bold_prefix else 1
            )
            heights.append(
                len(
                    wrap_text(
                        text.text, element_font(package.template, text)[1], text.size, measured
                    )
                )
                * text.size
                * 1.25
            )
        spacing = 0 if compact else 8
        body_height = sum(heights) + spacing * (len(body) - 1)
        chart_height = bottom - top - body_height - 8
        if chart_height <= 0:
            continue
        e = candidate.elements[index]
        e.box = Box(x=left, y=top, w=width, h=chart_height)
        if not stacked_layout(e, package.template)["fits"]:
            continue
        y = top + chart_height + 8
        for i, height in zip(body, heights):
            candidate.elements[i].box = Box(x=left, y=y, w=width, h=height)
            y += height + spacing
        if accepted(candidate):
            return candidate
    return scene
