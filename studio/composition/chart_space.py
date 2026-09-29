"""Grow chart height inside its existing safe region, without touching source artwork."""


def expand_chart_space(scene, package):
    from studio.models import Box

    charts = [e for e in scene.elements if e.kind == "chart"]
    if len(charts) != 1 or any(e.image_id for e in scene.elements):
        return scene
    chart = charts[0]
    profile = package.template
    pattern = next((p for p in profile.patterns if p.id == scene.pattern_id), None)
    if pattern:
        zones = [
            z
            for z in pattern.body_zones
            if z.x <= chart.box.x + 0.5
            and z.y <= chart.box.y + 0.5
            and z.x + z.w >= chart.box.x + chart.box.w - 0.5
            and z.y + z.h >= chart.box.y + chart.box.h - 0.5
        ]
        if not zones:
            return scene
        zone = min(zones, key=lambda z: z.w * z.h)
        bottom = min(zone.y + zone.h, profile.height - profile.margin / 2)
    elif scene.strategy == "token_composition":
        bottom = profile.height - profile.margin
    else:
        return scene
    for e in scene.elements:
        if e is chart or e.role == "template_background":
            continue
        if min(e.box.x + e.box.w, chart.box.x + chart.box.w) <= max(e.box.x, chart.box.x):
            continue
        if e.box.y >= chart.box.y:
            bottom = min(bottom, e.box.y - 12)
    if bottom <= chart.box.y + chart.box.h + 1:
        return scene
    result = scene.model_copy(deep=True)
    target = next(e for e in result.elements if e.kind == "chart")
    target.box = Box(x=chart.box.x, y=chart.box.y, w=chart.box.w, h=bottom - chart.box.y)
    return result
