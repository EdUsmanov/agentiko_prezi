"""Reallocate generic table/prose space before accepting smaller data labels."""

from studio.models import Box


def adapt_table_layout(scene, package):
    # Authored regions and uploaded-image compositions have their own contracts.
    if scene.strategy != "token_composition" or scene.pattern_id:
        return scene
    tables = [i for i, e in enumerate(scene.elements) if e.kind == "table"]
    body = [
        i
        for i, e in enumerate(scene.elements)
        if e.kind == "text" and e.role == "body" and e.source_ids
    ]
    if len(tables) != 1 or not body:
        return scene
    from studio.checks.audit import audit_scenes, repair_scenes
    from studio.templates.fonts import element_font, wrap_text
    from studio.checks.quality import candidate_regressions
    from studio.checks.repair_policy import FIT_CODES

    baseline = scene.model_copy(deep=True)
    repair_scenes([baseline], package)
    table_index = tables[0]
    if not any(
        f.element == table_index and f.code in FIT_CODES for f in audit_scenes([baseline], package)
    ):
        return scene
    table = scene.elements[table_index]
    left, top = table.box.x, table.box.y
    right = max(scene.elements[i].box.x + scene.elements[i].box.w for i in body)
    bottom = max(
        table.box.y + table.box.h,
        *(scene.elements[i].box.y + scene.elements[i].box.h for i in body),
    )
    width, height = right - left, bottom - top
    gap = package.template.width * 0.025

    def text_boxes(x, y, available_width, compact=False):
        boxes = []
        for i in body:
            e = scene.elements[i]
            size = 16 if compact else max(16, e.size)
            measure_width = (available_width - (size * 1.4 if e.bullet else 0)) * (
                0.94 if e.bold or e.bold_prefix else 1
            )
            if measure_width <= 0:
                return [], float("inf")
            h = (
                len(wrap_text(e.text, element_font(package.template, e)[1], size, measure_width))
                * size
                * 1.25
            )
            boxes.append(Box(x=x, y=y, w=available_width, h=h))
            y += h + gap
        return boxes, y - gap

    options = []
    # Larger prose is a preference, not a reason to make a previously fitting
    # table fail. Try the measured large text first, then the readable 16pt floor.
    for compact in (False, True):
        for fraction in (0.70, 0.76, 0.82, 0.88):
            tw = width * fraction
            boxes, end = text_boxes(left + tw + gap, top, width - tw - gap, compact)
            if end <= bottom:
                options.append((Box(x=left, y=top, w=tw, h=height), boxes, compact))
        boxes, end = text_boxes(left, top, width, compact)
        if end + gap < bottom:
            options.append(
                (Box(x=left, y=end + gap, w=width, h=bottom - end - gap), boxes, compact)
            )
    for box, boxes, compact in options:
        candidate = scene.model_copy(deep=True)
        candidate.elements[table_index].box = box
        candidate.elements[table_index].size = max(16, table.size)
        for i, text_box in zip(body, boxes):
            candidate.elements[i].box = text_box
            candidate.elements[i].size = 16 if compact else max(16, scene.elements[i].size)
        repair_scenes([candidate], package)
        if any(f.code in FIT_CODES for f in audit_scenes([candidate], package)):
            continue
        if not candidate_regressions([baseline], [candidate], package):
            candidate.notes += (
                "\nTable space adapted to retain readable data and complete source text."
            )
            return candidate
    # Preserve the original failure when no safe composition exists.
    return scene
