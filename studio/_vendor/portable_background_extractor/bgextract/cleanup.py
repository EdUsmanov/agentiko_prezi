"""Companion shapes in example content units."""

import re
from collections import Counter


def count_asset_slides(objects: dict[str, list[dict]]) -> dict[str, int]:
    """Count each media asset once per slide, regardless of repeated shapes."""
    return Counter(
        asset
        for part, rows in objects.items()
        if part.startswith("ppt/slides/")
        for asset in {row["asset"] for row in rows if row["asset"]}
    )


def remove_speaker_avatar_slot(rows: list[dict], decisions: list[dict]) -> None:
    """An empty circle beside a removed sample speaker byline is a portrait slot."""
    bylines = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if decision["action"] == "remove"
        and re.search(r"speaker|спикер|должност|имя фамили", row["text"], re.I)
    ]
    for row, decision in zip(rows, decisions, strict=True):
        x, y, w, h = row["box"]
        if (
            decision["action"] != "keep"
            or row["type"] != "sp"
            or row["preset"] != "ellipse"
            or row["text"]
            or not (0.035 <= w <= 0.15 and 0.05 <= h <= 0.18)
        ):
            continue
        if any(
            -0.01 <= label["box"][0] - x - w <= 0.04
            and abs(label["box"][1] + label["box"][3] / 2 - y - h / 2) <= 0.05
            for label in bylines
        ):
            decision.update(role="content", action="remove", reason="sample_speaker_avatar_slot")


def remove_repeated_avatar_slots(rows: list[dict], decisions: list[dict]) -> None:
    """Paired empty portrait circles in the content area are replaceable slots."""
    circles = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if decision["action"] == "keep"
        and row["type"] == "sp"
        and row["preset"] == "ellipse"
        and not row["text"]
        and 0.4 < row["box"][1] < 0.9
        and 0.04 < row["box"][2] < 0.2
        and 0.05 < row["box"][3] < 0.25
    ]
    for row, decision in zip(rows, decisions, strict=True):
        if row not in circles:
            continue
        if any(
            other is not row
            and abs(other["box"][1] - row["box"][1]) < 0.02
            and abs(other["box"][2] - row["box"][2]) < 0.02
            and abs(other["box"][3] - row["box"][3]) < 0.02
            for other in circles
        ):
            decision.update(role="content", action="remove", reason="repeated_avatar_slot")


def remove_sample_media_units(rows: list[dict], decisions: list[dict]) -> None:
    """An icon repeated beside removed labels is subject content, not ornament."""
    labels = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if row["text"] and decision["action"] == "remove"
    ]
    candidates = []
    for row, decision in zip(rows, decisions, strict=True):
        if (
            not row["asset"]
            or decision["action"] != "keep"
            or decision["reason"]
            not in {
                "transparent_flat_artwork",
                "unresolved_media_preserved",
            }
        ):
            continue
        x, y, w, h = row["box"]
        if w <= 0 or h <= 0 or w * h > 0.05:
            continue
        if any(_beside_sample_label(row["box"], label["box"]) for label in labels):
            candidates.append(row)
    if len(candidates) >= 2:
        for row, decision in zip(rows, decisions, strict=True):
            if decision["action"] != "keep" or decision["reason"] not in {
                "transparent_flat_artwork",
                "unresolved_media_preserved",
            }:
                continue
            if row in candidates or any(
                row["asset"] == seed["asset"]
                and abs(row["box"][0] - seed["box"][0]) <= 0.05
                and abs(row["box"][2] - seed["box"][2]) <= 0.02
                and abs(row["box"][3] - seed["box"][3]) <= 0.02
                for seed in candidates
            ):
                decision.update(
                    role="content", action="remove", reason="media_in_sample_content_unit"
                )


def _beside_sample_label(media: tuple, label: tuple) -> bool:
    x, y, w, h = media
    tx, ty, tw, th = label
    below = tx - 0.025 <= x + w / 2 <= tx + tw + 0.025 and -0.025 <= ty - y - h <= 0.2
    beside = -0.02 <= tx - x - w <= 0.07 and abs(ty + th / 2 - y - h / 2) <= 0.06
    return below or beside


def remove_sample_table_rules(rows: list[dict], decisions: list[dict]) -> None:
    """Repeated thin raster rules within a filled sample table are not background."""
    sample_texts = sum(
        bool(row["text"] and decision["action"] == "remove")
        for row, decision in zip(rows, decisions, strict=True)
    )
    if sample_texts < 6:
        return
    rules = [
        decision
        for row, decision in zip(rows, decisions, strict=True)
        if row["asset"]
        and decision["action"] == "keep"
        and row["box"][2] >= 0.5
        and 0 < row["box"][3] <= 0.005
        and 0.1 <= row["box"][1] <= 0.9
    ]
    if len(rules) >= 3:
        for decision in rules:
            decision.update(role="content", action="remove", reason="sample_table_rule")


def remove_subject_svg_overlays(
    rows: list[dict], decisions: list[dict], asset_slide_counts: dict[str, int]
) -> None:
    """Remove a one-off SVG icon attached to a removed subject photo and its badge.

    Repeated SVG artwork can be a template motif, so a local photo association
    alone is not enough to delete it. The SVG must overlap a removed photo and
    occur on at most two slides in the deck.
    """
    photos = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if row["type"] == "pic"
        and row["asset"].lower().endswith((".jpg", ".jpeg", ".png"))
        and row["box"][2] * row["box"][3] >= 0.16
        and decision["action"] == "remove"
    ]
    removed_icons = []
    for row, decision in zip(rows, decisions, strict=True):
        if (
            decision["reason"] != "unresolved_media_preserved"
            or not row["asset"].lower().endswith(".svg")
            or asset_slide_counts.get(row["asset"], 0) > 2
            or row["box"][2] * row["box"][3] > 0.08
        ):
            continue
        if any(_overlap_fraction(row["box"], photo["box"]) >= 0.35 for photo in photos):
            decision.update(role="content", action="remove", reason="subject_photo_svg_overlay")
            removed_icons.append(row)
    for row, decision in zip(rows, decisions, strict=True):
        if (
            decision["reason"] != "unlabelled_vector_preserved"
            or row["type"] != "sp"
            or row["preset"] != "ellipse"
            or row["text"]
        ):
            continue
        if any(
            _overlap_fraction(icon["box"], row["box"]) >= 0.8
            and row["box"][2] * row["box"][3] <= 4 * icon["box"][2] * icon["box"][3]
            for icon in removed_icons
        ):
            decision.update(role="content", action="remove", reason="subject_icon_badge")


def _overlap_fraction(box: tuple, other: tuple) -> float:
    x, y, w, h = box
    ox, oy, ow, oh = other
    intersection = max(0, min(x + w, ox + ow) - max(x, ox)) * max(
        0, min(y + h, oy + oh) - max(y, oy)
    )
    return intersection / max(w * h, 1e-9)

