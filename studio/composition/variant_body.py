"""Source-backed body structures shared by text and image compositions."""

from studio.models import Box, Element
from studio.composition.text_composer import (
    text_element,
    fact_elements,
    body_size_candidates,
    words_fit,
)
from studio.templates.fonts import wrap_text, element_font


def compose_variant_body(
    slide, package, variant, body, box, profile, color, style=None, table_pattern=None
):
    """Use source-backed structure inside one ordinary template body field."""
    if len(body) < 2 or slide.table_id or slide.purpose in ("cover", "divider"):
        return None
    from studio.contents.semantic_bindings import content_groups, labeled_facts

    body = labeled_facts(body, package)
    binding = content_groups(slide, package)
    groups = binding["groups"] if binding["status"] == "specialized" else []
    ordered = [f.id for group in groups for f in group["facts"]] == [f.id for f in body]
    if (
        variant == "analytical"
        and ordered
        and len(groups) >= 2
        and slide.purpose not in ("process", "timeline")
    ):
        # Each column is a reviewed participant; every cell retains full source wording.
        rows = [
            [g["label"] for g in groups],
            ["\n".join(f.text for f in g["facts"]) for g in groups],
        ]
        native_table = getattr(table_pattern, "table_template", None)
        target = (
            native_table.box
            if native_table and len(native_table.column_widths) == len(rows[0])
            else box
        )
        if all(rows[0]):
            from studio.checks.audit import table_fits
            from studio.composition.table_style import apply_table_style

            for size in body_size_candidates(profile, profile.body_size, minimum=16):
                candidate = Element(
                    kind="table",
                    box=target,
                    rows=rows,
                    role="grounded_comparison",
                    font=profile.font,
                    size=size,
                    color=color,
                    fill=profile.accent,
                    source_ids=[f.id for f in body],
                )
                apply_table_style(candidate, table_pattern)
                if candidate.table_template and any(
                    0 < cell.font_size < 16
                    for row in candidate.table_template.cells
                    for cell in row
                ):
                    continue
                if table_fits(candidate, profile):
                    return [candidate]
    if variant == "executive":
        gap = 10
        lead_box = Box(x=box.x, y=box.y, w=box.w, h=box.h * 0.34)
        detail_box = Box(
            x=box.x, y=lead_box.y + lead_box.h + gap, w=box.w, h=box.h - lead_box.h - gap
        )
        lead = text_element(
            body[0].text,
            lead_box,
            profile,
            "body",
            profile.body_size * 1.2,
            color,
            [body[0].id],
            style,
            body_fallback=16,
        )
        lead.bold = True
        detail = fact_elements(body[1:], detail_box, profile, color, field_style=style)
        for element in detail:
            element.size = min(element.size, lead.size)
        result = [lead, *detail]
    elif variant == "analytical":
        columns = min(len(body), 3)
        gap = max(14, box.w * 0.025)
        width = (box.w - gap * (columns - 1)) / columns
        result = []
        for i in range(columns):
            facts = body[i * len(body) // columns : (i + 1) * len(body) // columns]
            column = Box(x=box.x + i * (width + gap), y=box.y, w=width, h=box.h)
            result.extend(fact_elements(facts, column, profile, color, field_style=style))
    elif (
        variant == "story"
        and slide.purpose in ("process", "timeline")
        and ordered
        and len(groups) >= 2
    ):
        from studio.contents.semantic_bindings import inline_group_text

        paragraphs = [
            inline_group_text(
                group["label"]
                if slide.purpose == "timeline"
                and group["label"].casefold()
                not in " ".join(f.text for f in group["facts"]).casefold()
                else "",
                [f.text for f in labeled_facts(group["facts"], package)],
            )
            for group in groups
        ]
        result = [
            text_element(
                "\n".join(paragraphs),
                box,
                profile,
                "body",
                color=color,
                source_ids=[f.id for f in body],
                field_style=style,
                body_fallback=16,
            )
        ]
    elif variant == "story":
        result = [
            text_element(
                "\n".join(f.text for f in body),
                box,
                profile,
                "body",
                color=color,
                source_ids=[f.id for f in body],
                field_style=style,
                body_fallback=16,
            )
        ]
    else:
        return None
    if any(
        e.size < 16
        or not words_fit(
            e.text,
            element_font(profile, e)[1],
            e.size,
            (e.box.w - (e.size * 1.4 if e.bullet else 0))
            * (0.94 if e.bold or e.bold_prefix else 1),
        )
        or len(wrap_text(e.text, element_font(profile, e)[1], e.size, e.box.w)) * e.size * 1.25
        > e.box.h + 1
        for e in result
        if e.kind == "text"
    ):
        return None
    return result
