"""Measure occupied template regions without composing or repairing scenes."""


def unused_body_regions(scene, package):
    """Count unoccupied authored content slots, not intentional cover whitespace."""
    pattern = next((p for p in package.template.patterns if p.id == scene.pattern_id), None)
    if not pattern or scene.purpose in ("cover", "divider") or pattern.role in ("cover", "divider"):
        return 0
    content = [
        e
        for e in scene.elements
        if (e.source_ids or e.image_id)
        and e.role not in ("title", "footer", "brand", "template_background")
    ]

    def occupied(zone):
        for element in content:
            box = element.box
            overlap = max(0, min(zone.x + zone.w, box.x + box.w) - max(zone.x, box.x)) * max(
                0, min(zone.y + zone.h, box.y + box.h) - max(zone.y, box.y)
            )
            if min(zone.w * zone.h, box.w * box.h) > 0 and overlap >= 0.5 * min(
                zone.w * zone.h, box.w * box.h
            ):
                return True
        return False

    zones = list(pattern.body_zones)
    # Data patterns combine proven empty authored fields into one editable band.
    # Their old field boxes are not separate panels once that band is occupied.
    merged_zone = pattern.body_zones[0] if len(pattern.body_zones) == 1 else None
    occupied_merge = bool(
        merged_zone
        and pattern.safe_text_zone.get("data_region") == merged_zone.model_dump()
        and pattern.safe_text_zone.get("data_region_method")
        == "authored_field_band_expanded_with_uniform_background_guard"
        and occupied(merged_zone)
    )
    # A large source text field removed as 'unused' can leave a conspicuous
    # panel (e.g. an Education code sample). Do not select it for variety alone.
    canvas = getattr(package.template, "width", 0) * getattr(package.template, "height", 0)
    if canvas > 0:
        from studio.models import Box

        for field in pattern.fields:
            if field.get("role") == "unused" and field.get("box"):
                zone = Box.model_validate(field["box"])
                if (
                    occupied_merge
                    and zone.x >= merged_zone.x
                    and zone.y >= merged_zone.y
                    and zone.x + zone.w <= merged_zone.x + merged_zone.w
                    and zone.y + zone.h <= merged_zone.y + merged_zone.h
                ):
                    continue
                if zone.w * zone.h >= canvas * 0.08:
                    zones.append(zone)
    return sum(not occupied(zone) for zone in zones)
