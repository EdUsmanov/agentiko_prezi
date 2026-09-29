"""Compose text objects without depending on complete slide or image composition."""

from .models import Box, Element
from .fonts import wrap_text, role_font, text_width


def text_element(
    text, box, profile, role="body", size=None, color=None, source_ids=None, field_style=None
):
    from .field_style import styled_profile

    profile, resolved_style = styled_profile(profile, field_style, role)
    size = size or profile.body_size
    original_size = size
    from .design_balance import preferred_size

    size = preferred_size(profile, size, role)
    minimum = 18 if role == "title" else 16 if source_ids else None
    if minimum is not None:
        size = max(size, minimum)
    family, font_file = role_font(profile, role)
    candidates = [s for s in profile.font_sizes if 10 <= s <= size]
    if minimum is not None:
        candidates.append(minimum)
    if role == "title":
        candidates.extend(range(18, int(size) + 1, 2))
    candidates = sorted(set(candidates + [size, original_size]), reverse=True)
    for candidate in candidates:
        if any(text_width(word, font_file, candidate) > box.w * 0.94 for word in text.split()):
            continue
        if (
            len(wrap_text(text, font_file, candidate, box.w * (0.94 if role == "title" else 1)))
            * candidate
            * 1.25
            <= box.h
        ):
            return Element(
                kind="text",
                box=box,
                text=text,
                font=family,
                size=candidate,
                color=color or profile.foreground,
                bold=resolved_style.get("bold")
                if resolved_style.get("bold") is not None
                else role == "title",
                field_style=resolved_style,
                role=role,
                source_ids=source_ids or [],
            )
    # Preserve the text; audit will surface overflow rather than silently truncate.
    return Element(
        kind="text",
        box=box,
        text=text,
        font=family,
        size=min(candidates),
        color=color or profile.foreground,
        bold=resolved_style.get("bold")
        if resolved_style.get("bold") is not None
        else role == "title",
        field_style=resolved_style,
        role=role,
        source_ids=source_ids or [],
    )


def fact_elements(facts, box, profile, color, heading_zone=None, field_style=None):
    """Keep evidence as separate, editable paragraphs instead of one text wall."""
    from .field_style import styled_profile

    profile, resolved_style = styled_profile(profile, field_style, "body")
    if not facts:
        return []
    out = []
    if (
        heading_zone
        and len(facts) > 1
        and len(facts[0].text) < 70
        and len(
            wrap_text(
                facts[0].text,
                role_font(profile, "title")[1],
                profile.body_size,
                heading_zone.w * 0.94,
            )
        )
        * profile.body_size
        * 1.25
        <= heading_zone.h
    ):
        heading = text_element(
            facts[0].text,
            heading_zone,
            profile,
            "subheading",
            size=profile.body_size,
            color=color,
            source_ids=[facts[0].id],
        )
        heading.bold = True
        out.append(heading)
        facts = facts[1:]
    # Sentences split by the parser retain their source line. Keep a source
    # paragraph together rather than turning every sentence into a new bullet.
    grouped = []
    for fact in facts:
        if (
            grouped
            and fact.line > 0
            and fact.line == grouped[-1][0].line
            and fact.section == grouped[-1][0].section
            and not fact.list_item
        ):
            grouped[-1].append(fact)
        else:
            grouped.append([fact])
    originals = grouped
    facts = [
        group[0].model_copy(update={"text": " ".join(f.text for f in group)}) for group in grouped
    ]
    from .design_balance import preferred_size as readable_size

    preferred_size = readable_size(profile, max(16, profile.body_size), "body")
    sizes = sorted(
        {
            preferred_size,
            max(16, profile.body_size),
            16,
            min(12, profile.body_size),
            *[s for s in profile.font_sizes if 12 <= s <= preferred_size],
        },
        reverse=True,
    )
    # Measure exactly the marker that will be rendered. A single paragraph,
    # source note or heading must not lose width to an invisible bullet.
    bullets = [
        fact.list_item
        or len(facts) > 1
        and not fact.text.startswith(("Источник:", "Source:"))
        and not fact.text.endswith(":")
        for fact in facts
    ]

    def layout(size):
        gap = size * 0.5
        heights = [
            len(
                wrap_text(
                    f.text,
                    profile.font_file,
                    size,
                    (box.w - (size * 1.4 if bullet else 0))
                    * (0.94 if f.emphasis or resolved_style.get("bold") else 1),
                )
            )
            * size
            * 1.25
            for f, bullet in zip(facts, bullets)
        ]
        return gap, heights

    for size in sizes:
        gap, heights = layout(size)
        words_fit = all(
            text_width(word, profile.font_file, size)
            <= (box.w - (size * 1.4 if bullet else 0))
            * (0.94 if f.emphasis or resolved_style.get("bold") else 1)
            for f, bullet in zip(facts, bullets)
            for word in f.text.split()
        )
        if words_fit and sum(heights) + gap * (len(facts) - 1) <= box.h:
            break
    y = box.y
    for fact, height, group, bullet in zip(facts, heights, originals, bullets):
        b = Box(x=box.x, y=y, w=box.w, h=height)
        e = Element(
            kind="text",
            box=b,
            text=fact.text,
            font=profile.font,
            size=size,
            color=color,
            role="body",
            source_ids=[f.id for f in group],
            bullet=bullet,
            bold_prefix=fact.emphasis,
            field_style=resolved_style,
            bold=bool(resolved_style.get("bold")),
        )
        out.append(e)
        y += height + gap
    # Do not let the last fact extend the authored container unnoticed.
    if out and y - gap > box.y + box.h:
        out[-1].box.h = max(1, box.y + box.h - out[-1].box.y)
    return out
