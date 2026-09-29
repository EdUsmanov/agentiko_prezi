"""Explainable background extraction, including corporate identity in every OOXML layer."""

import re
from xml.etree import ElementTree as ET

from studio._vendor.portable_background_extractor.bgextract.cleanup import count_asset_slides, remove_repeated_avatar_slots, remove_sample_media_units, remove_sample_table_rules, remove_speaker_avatar_slot, remove_subject_svg_overlays
from studio._vendor.portable_background_extractor.bgextract.context import _remove_content_grid_dividers, _remove_sample_content_surfaces, refine_content_media, remove_content_panel_members, remove_content_unit_tail, remove_icon_showcase_vectors, remove_labelled_card_backings, remove_percentage_chart_artwork, remove_placeholder_companions, remove_sample_style_connectors, remove_subject_mockups, remove_vectors_inside_sample_text
from studio._vendor.portable_background_extractor.bgextract.features import A, P, collect_objects, shape_id
from studio._vendor.portable_background_extractor.bgextract.layout import remove_footer_status_icons, remove_layout_content_scaffold, remove_navigation_dots, remove_numbered_timeline_rule, remove_web_link_button
from studio._vendor.portable_background_extractor.bgextract.subject_fragments import preserve_masked_photo_collage, remove_closing_subject_icons, remove_screenshot_header_strips, subject_fragment_reason

BRAND = re.compile(
    r"logo|logotype|wordmark|brand(?:ing)?\b|corporate identity|watermark|логотип|айдентик|фирменный знак|водяной знак",
    re.I,
)
DECOR = re.compile(r"background|decora|ornament|фонов|\bфон\b|декор|орнамент", re.I)
CONTENT = re.compile(
    r"photo(?:graph)?|portrait|headshot|screenshot|image slot|chart|diagram|gantt|timeline|фото|портрет|скриншот|диаграм|график|гант|таймлайн",
    re.I,
)
SAMPLE = re.compile(
    r"click (?:here )?to|add (?:text|title)|your (?:company|logo|footer)|company name|sample|placeholder|lorem|введите|образец|название компании|место для|шаблон|пример|заголовок|текстовый|slide title|text block|^title$|^subtitle$|visit our\s+faq|presentationgo\.com|slideegg\.com",
    re.I,
)
SOCIAL_ICON = re.compile(r"facebook|instagram|youtube|twitter|linkedin", re.I)
BRAND_TEXT = re.compile(
    r"©|®|™|university|college|университет|институт|корпорац|confidential|\b(?:inc|ltd|llc|corp)\b|\.(?:com|org|edu|ru)\b",
    re.I,
)
DATA = re.compile(
    r"chart|diagram|gantt|timeline|calendar plan|color palette|color scheme|countries|continents|project centers|student participants|диаграм|график|гант|таймлайн|календарный план|палитр|схем|гистограмм|иконк|icons|iconography|infographic|инфограф|\bmap\b|карта",
    re.I,
)


def background_roles(parts: dict[str, bytes], width: int, height: int) -> dict[str, list[dict]]:
    """Return roles and reasons before mutation. No object is removed solely for its size."""
    objects = collect_objects(parts, width, height)
    asset_slide_counts = count_asset_slides(objects)
    result = {}
    for part, rows in objects.items():
        inherited = not part.startswith("ppt/slides/")
        data_slide = bool(DATA.search(" ".join(row["text"] for row in rows)))
        for row in rows:
            row["brandNeighbour"] = _brand_neighbour(row, rows)
            row["repeatedMediaUnit"] = _repeated_media_unit(row, rows)
        decisions = [_classify(row, inherited, data_slide) for row in rows]
        if not inherited:
            preserve_masked_photo_collage(rows, decisions)
        remove_web_link_button(rows, decisions)
        if inherited:
            remove_layout_content_scaffold(rows, decisions)
        else:
            _remove_sample_content_surfaces(rows, decisions)
            remove_sample_table_rules(rows, decisions)
            remove_sample_media_units(rows, decisions)
            _remove_content_grid_dividers(rows, decisions)
            refine_content_media(rows, decisions)
            remove_screenshot_header_strips(rows, decisions)
            remove_percentage_chart_artwork(rows, decisions)
            remove_placeholder_companions(rows, decisions)
            remove_subject_mockups(rows, decisions)
            remove_labelled_card_backings(rows, decisions)
            remove_content_panel_members(rows, decisions)
            remove_content_unit_tail(rows, decisions)
            remove_icon_showcase_vectors(rows, decisions)
            remove_vectors_inside_sample_text(rows, decisions)
            remove_sample_style_connectors(rows, decisions)
            remove_navigation_dots(rows, decisions)
            remove_repeated_avatar_slots(rows, decisions)
            remove_speaker_avatar_slot(rows, decisions)
            remove_numbered_timeline_rule(rows, decisions)
            remove_closing_subject_icons(rows, decisions)
            remove_subject_svg_overlays(rows, decisions, asset_slide_counts)
        remove_footer_status_icons(rows, decisions)
        removed = {item["id"] for item in decisions if item["action"] == "remove"}
        for row, decision in zip(rows, decisions, strict=True):
            if row["connections"] and any(sid in removed for sid in row["connections"]):
                decision.update(
                    role="content", action="remove", reason="connector_attached_to_content"
                )
            if decision["reason"] in {
                "unlabelled_vector_preserved",
                "unresolved_media_preserved",
            } and _contains_content(row, rows, removed):
                decision.update(role="content", action="remove", reason="container_of_sample_text")
        detached = {
            sid
            for row, decision in zip(rows, decisions, strict=True)
            if row["type"] == "cxnSp" and decision["action"] == "remove"
            for sid in row["connections"]
        }
        for row, decision in zip(rows, decisions, strict=True):
            _, _, w, h = row["box"]
            if (
                row["id"] in detached
                and decision["action"] == "keep"
                and row["type"] == "sp"
                and row["preset"] == "ellipse"
                and w <= 0.025
                and h <= 0.04
            ):
                decision.update(
                    role="content", action="remove", reason="detached_connector_endpoint"
                )
        containers = [item for item in decisions if item["reason"] == "container_of_sample_text"]
        for row, decision in zip(rows, decisions, strict=True):
            if (
                not inherited
                and (
                    decision["role"] in {"decoration", "unknown"}
                    or (
                        decision["reason"] == "transparent_flat_artwork"
                        and row["recurring"] == 1
                        and row["image"].get("alpha", 0) >= 0.9
                        and not BRAND.search(row["metadata"])
                    )
                )
                and any(_inside(row["box"], container["box"]) for container in containers)
            ):
                decision.update(
                    role="content", action="remove", reason="member_of_removed_content_panel"
                )
        result[part] = decisions
    return result


def _classify(row: dict, inherited: bool, data_slide: bool) -> dict:
    def decision(role: str, action: str, reason: str) -> dict:
        return {key: row[key] for key in ("id", "type", "box", "text", "asset", "preset")} | {
            "role": role,
            "action": action,
            "reason": reason,
        }

    meta, text = row["metadata"], row["text"]
    x, y, w, h = row["box"]
    area = w * h
    edge = y + h <= 0.16 or y >= 0.86 or x + w <= 0.12 or x >= 0.88
    brand_position = edge or (w <= 0.3 and h <= 0.25 and (y <= 0.12 or y >= 0.78))
    bleed = (w >= 0.9 and h >= 0.9 and x <= 0.06 and y <= 0.06) or (
        not data_slide and h >= 0.98 and y <= 0.02 and w >= 0.45 and (x <= 0.02 or x + w >= 0.98)
    )
    group_brand = any(BRAND.search(group["metadata"]) for group in row["groups"])
    if SOCIAL_ICON.search(meta) and row["asset"]:
        return decision("content", "remove", "social_media_sample_icon")
    fragment = subject_fragment_reason(row, inherited, data_slide)
    if fragment:
        return decision("content", "remove", fragment)
    if (
        row["asset"]
        and x >= 0.85
        and y >= 0.82
        and w <= 0.04
        and h <= 0.06
        and row["recurring"] == 1
        and row["image"].get("alpha", 0) >= 0.5
        and not BRAND.search(meta)
    ):
        return decision("content", "remove", "isolated_corner_information_icon")
    if (BRAND.search(meta) or group_brand) and not SAMPLE.search(text + " " + meta):
        return decision("identity", "keep", "explicit_brand_identity")
    if row["placeholder"] is not None:
        return decision("content", "remove", "replaceable_placeholder")
    if row["type"] == "graphicFrame":
        return decision("content", "remove", "structured_table_chart_or_diagram")
    if DECOR.search(meta) and not SAMPLE.search(text):
        return decision(
            "background" if bleed else "decoration", "keep", "explicit_background_or_decoration"
        )
    if CONTENT.search(meta):
        return decision("content", "remove", "explicit_subject_media")
    if text in {"«", "»", "“", "”", "❝", "❞"}:
        return decision("decoration", "keep", "decorative_quotation_glyph")
    if text:
        brand_text = (BRAND_TEXT.search(text) and h <= 0.1) or (
            row.get("brandNeighbour") and (row["recurring"] > 1 or inherited)
        )
        if not SAMPLE.search(text) and brand_text and edge:
            result = decision("identity", "keep", "fixed_edge_corporate_text")
            replacement = re.sub(
                r"\s*[|•]?\s*(?:page|страница|слайд)\s*\d+\s*$", "", text, flags=re.I
            )
            if replacement != text:
                result["replacementText"] = replacement
            return result
        if bleed:
            return decision("background", "clear-text", "canvas_surface_with_sample_text")
        return decision("content", "remove", "sample_text")
    if (
        not inherited
        and row["type"] == "sp"
        and row["preset"] == "rect"
        and row["outlineOnly"]
        and 0.1 <= area < 0.65
        and x >= 0.1
        and y >= 0.05
        and x + w <= 0.98
        and y + h <= 0.9
    ):
        return decision("content", "remove", "empty_outlined_content_slot")
    if bleed:
        return decision("background", "keep", "full_canvas_surface")
    if row["type"] == "grpSp":
        return decision("container", "keep", "classify_group_members_individually")
    # Static inherited artwork is part of the design unless there is positive content evidence.
    if inherited:
        role = "identity" if brand_position and row["asset"] else "decoration"
        return decision(role, "keep", "static_inherited_artwork")
    image = row["image"]
    if row["asset"]:
        if (
            row["asset"].lower().endswith(".svg")
            and row["type"] == "pic"
            and (
                (0.15 <= area <= 0.5 and x < 0.1 and y < 0.15 and w < 0.7)
                or (w > 0.8 and h > 0.3 and y > 0.5)
            )
        ):
            return decision("content", "remove", "subject_svg_heading_or_number")
        if brand_position and row["recurring"] > 1:
            return decision("identity", "keep", "recurring_fixed_edge_asset")
        if row["repeatedMediaUnit"]:
            return decision("content", "remove", "repeated_foreground_media_units")
        if data_slide:
            return decision("content", "remove", "raster_in_sample_data_composition")
        # Transparency plus a restricted palette supports artwork, not a rectangular photo.
        # Alpha alone is insufficient: cut-out portraits have many opaque colors.
        if image.get("alpha", 0) >= 0.08 and image.get("colors", 1000) <= 64:
            return decision(
                "identity" if brand_position else "decoration", "keep", "transparent_flat_artwork"
            )
        if image:
            if brand_position:
                return decision("unknown", "keep", "ambiguous_edge_media_preserved")
            return decision("content", "remove", "interior_subject_raster")
        return decision("unknown", "keep", "unresolved_media_preserved")
    if any(group["text"] and not BRAND.search(group["metadata"]) for group in row["groups"]):
        return decision("content", "remove", "member_of_sample_content_group")
    if data_slide and not edge and area < 0.65:
        return decision("content", "remove", "vector_in_sample_data_composition")
    return decision("decoration", "keep", "unlabelled_vector_preserved")


def _repeated_media_unit(row: dict, rows: list[dict]) -> bool:
    """Repeated interior picture columns with subject text are content units, including raster cards."""
    x, y, w, h = row["box"]
    if not row["asset"] or not 0.015 <= w <= 0.4 or y < 0.16 or y + h > 0.85:
        return False
    peers = [
        other
        for other in rows
        if other["asset"]
        and other["box"][2] > 0
        and abs(other["box"][1] - y) <= 0.04
        and 0.8 <= other["box"][2] / w <= 1.25
    ]
    return len(peers) >= 3 and any(item["text"] for item in rows)


def _brand_neighbour(row: dict, rows: list[dict]) -> bool:
    """Repeated text near a small brand mark can be a wordmark/slogan, not a heading."""
    x, y, w, h = row["box"]
    if not row["text"] or h > 0.1 or w > 0.5:
        return False
    for other in rows:
        ox, oy, ow, oh = other["box"]
        mark = BRAND.search(other["metadata"]) or (
            other["asset"]
            and other["image"].get("alpha", 0) >= 0.08
            and other["image"].get("colors", 1000) <= 64
        )
        if not mark or ow * oh > 0.04:
            continue
        if abs(y + h / 2 - oy - oh / 2) <= 0.05 and min(abs(x - ox - ow), abs(ox - x - w)) <= 0.05:
            return True
    return False


def _inside(box: tuple, container: tuple) -> bool:
    x, y, w, h = box
    cx, cy, cw, ch = container
    margin = 0.005
    return (
        w > 0
        and h > 0
        and cx - margin <= x
        and cy - margin <= y
        and x + w <= cx + cw + margin
        and y + h <= cy + ch + margin
    )


def _contains_content(row: dict, rows: list[dict], removed: set[str]) -> bool:
    x, y, w, h = row["box"]
    if row["type"] not in {"sp", "pic"} or w * h >= 0.65 or w <= 0 or h <= 0:
        return False
    return any(
        item["id"] in removed
        and (item["text"] or item["asset"])
        and x <= item["box"][0] + item["box"][2] / 2 <= x + w
        and y <= item["box"][1] + item["box"][3] / 2 <= y + h
        and (
            item["text"]
            or (
                row["type"] == "sp"
                and 0 < item["box"][2] * item["box"][3]
                and w * h <= 6 * item["box"][2] * item["box"][3]
            )
        )
        for item in rows
        if item["id"] != row["id"]
    )


def apply_background_roles(root: ET.Element, roles: list[dict]) -> None:
    decisions = {item["id"]: item for item in roles}
    tree = root.find(f".//{{{P}}}spTree")
    if tree is None:
        return
    for parent in list(tree.iter()):
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] not in {"sp", "pic", "cxnSp", "graphicFrame", "grpSp"}:
                continue
            decision = decisions.get(shape_id(child), {})
            if decision.get("action") == "remove":
                parent.remove(child)
            else:
                for holder in child.iter():
                    for field in list(holder):
                        if field.tag == f"{{{A}}}fld" and field.get("type", "").casefold() in {
                            "slidenum",
                            "slidenumber",
                        }:
                            holder.remove(field)
                if "replacementText" in decision:
                    texts = child.findall(f".//{{{A}}}t")
                    for index, text in enumerate(texts):
                        text.text = decision["replacementText"] if index == 0 else ""
                elif decision.get("action") == "clear-text":
                    for text in child.findall(f".//{{{A}}}t"):
                        text.text = ""


def protected_background_regions(roles: list[dict], width: int, height: int) -> list[dict]:
    regions = []
    for item in roles:
        x, y, w, h = item["box"]
        if item["action"] != "keep" or item["role"] not in {"identity", "decoration", "unknown"}:
            continue
        if (
            item["type"] == "sp"
            and item["role"] != "identity"
            and item.get("preset") in {"rect", "roundRect"}
        ):
            continue
        if w <= 0 or h <= 0 or w * h >= 0.65 or x >= 1 or y >= 1 or x + w <= 0 or y + h <= 0:
            continue
        regions.append(
            {
                "kind": item["role"],
                "x": round(x * 1280),
                "y": round(y * 1280 * height / width),
                "width": max(1, round(w * 1280)),
                "height": max(1, round(h * 1280 * height / width)),
            }
        )
    return regions

