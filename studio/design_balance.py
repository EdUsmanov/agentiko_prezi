"""Bounded presentation preferences; source content and template regions stay intact."""

from .template_geometry import contrast


def broken_words(scene, profile):
    """Reflow must not introduce line breaks inside evidence words."""
    from .fonts import element_font, text_width

    return sum(
        text_width(word, element_font(profile, e)[1], e.size)
        > (e.box.w - (e.size * 1.4 if e.bullet else 0)) * (0.94 if e.bold or e.bold_prefix else 1)
        + 0.1
        for e in scene.elements
        if e.kind == "text" and (e.source_ids or e.role == "title")
        for word in e.text.split()
    )


def preferred_size(profile, size, role):
    """Grow within the authored scale, then let measured fitting decide what fits."""
    if role not in ("title", "body"):
        return size
    proportion = 0.09 if role == "title" else 0.055
    return round(max(size, min(size * 1.35, profile.height * proportion)), 1)


def fit_reflow_words(scene, profile):
    """Fit alternate text columns without splitting words or sacrificing data space."""
    from .fonts import element_font, text_width, wrap_text

    for e in scene.elements:
        if e.kind != "text" or not e.source_ids or e.role == "title":
            continue
        font = element_font(profile, e)[1]
        floor = min(e.size, 16)
        original = e.size
        for step in range(int((original - floor) * 2) + 2):
            size = max(floor, original - step / 2)
            width = (e.box.w - (size * 1.4 if e.bullet else 0)) * (
                0.94 if e.bold or e.bold_prefix else 1
            )
            if (
                all(text_width(word, font, size) <= width for word in e.text.split())
                and len(wrap_text(e.text, font, size, width)) * size * 1.25 <= e.box.h
            ):
                e.size = size
                break
    return scene


def improve_contrast(scene, profile):
    """Body copy needs comfortable contrast even when its size permits a lower floor."""
    for e in scene.elements:
        if e.kind != "text" or e.role not in ("body", "subheading") or not e.color:
            continue
        background = e.background_hint or scene.background
        if contrast(e.color, background) >= 4.5:
            continue
        candidates = [c for c in profile.colors if contrast(c, background) >= 4.5]
        if candidates:
            # Prefer the template's normal ink; otherwise the most legible palette color.
            e.color = (
                profile.foreground
                if profile.foreground in candidates
                else max(candidates, key=lambda c: contrast(c, background))
            )
    return scene


def composition_family(scene, profile):
    """Visible content geometry, independent of wording, artwork and pattern IDs."""
    if scene.purpose in ("cover", "divider"):
        return (scene.purpose,)
    return tuple(
        sorted(
            {
                (e.kind, round(e.box.x / profile.width * 6), round(e.box.w / profile.width * 6))
                for e in scene.elements
                if e.source_ids
                and e.role not in ("title", "footer", "brand", "template_background")
            }
        )
    )


def rhythm_cost(families):
    """Soft preference for changes of composition; never reorder facts or slides."""
    repeats = sum(a == b for a, b in zip(families, families[1:]))
    triples = sum(a == b == c for a, b, c in zip(families, families[1:], families[2:]))
    return repeats * 3 + triples * 9


def design_cost(scene, profile):
    """Prefer readable type and substantial evidence over decorative variety."""
    if scene.purpose in ("cover", "divider"):
        return 0
    body = [e for e in scene.elements if e.kind == "text" and e.role == "body" and e.source_ids]
    cost = 0
    if body:
        target = preferred_size(profile, max(16, profile.body_size), "body")
        cost = sum(max(0, target - e.size) for e in body) / len(body) * 4
    charts = [e for e in scene.elements if e.kind == "chart"]
    for e in charts:
        cost += max(0, 0.48 - e.box.h / profile.height) * 80
        cost += max(0, 0.5 - e.box.w / profile.width) * 40
    return round(cost, 2)
