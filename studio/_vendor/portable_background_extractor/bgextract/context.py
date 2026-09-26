"""Classify leftover chart media by its neighbours after the first OOXML pass."""

import re


def remove_content_unit_tail(rows: list[dict], decisions: list[dict]) -> None:
    """A last unlabeled card in a removed vertical content stack is still a card."""
    removed = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if row["asset"] and decision["action"] == "remove"
    ]
    for row, decision in zip(rows, decisions, strict=True):
        if (
            not row["asset"]
            or decision["action"] != "keep"
            or decision["reason"] in {"explicit_brand_identity", "recurring_fixed_edge_asset"}
        ):
            continue
        x, y, _, h = row["box"]
        if x < 0.2 or not 0.03 <= h <= 0.12:
            continue
        peers = [
            other
            for other in removed
            if abs(other["box"][0] - x) <= 0.015
            and abs(other["box"][3] - h) <= 0.02
            and 0 < y - other["box"][1] <= 0.4
        ]
        if len(peers) >= 3:
            decision.update(role="content", action="remove", reason="last_card_in_sample_stack")


def remove_sample_style_connectors(rows: list[dict], decisions: list[dict]) -> None:
    """Example arrows below a labelled line-style specimen are foreground."""
    labels = [
        row["box"]
        for row, decision in zip(rows, decisions, strict=True)
        if decision["action"] == "remove"
        and re.search(r"line default style|line style", row["text"], re.I)
    ]
    for row, decision in zip(rows, decisions, strict=True):
        x, y, w, h = row["box"]
        if (
            decision["action"] == "keep"
            and row["type"] == "cxnSp"
            and 0.1 <= w <= 0.5
            and h <= 0.01
            and any(
                abs(x - tx) <= 0.04 and abs(w - tw) <= 0.05 and -0.01 <= y - ty - th <= 0.1
                for tx, ty, tw, th in labels
            )
        ):
            decision.update(role="content", action="remove", reason="sample_line_style_arrow")


def remove_icon_showcase_vectors(rows: list[dict], decisions: list[dict]) -> None:
    """The pictograms on a named icon catalogue slide are example content."""
    if not any(
        re.fullmatch(r"\s*(?:icons?|iconography|иконки|иконография)\s*", row["text"], re.I)
        for row in rows
    ):
        return
    for row, decision in zip(rows, decisions, strict=True):
        if row["type"] == "sp" and decision["reason"] == "unlabelled_vector_preserved":
            decision.update(role="content", action="remove", reason="sample_icon_catalogue")


def remove_vectors_inside_sample_text(rows: list[dict], decisions: list[dict]) -> None:
    """Vector bullets inside a removed paragraph box belong to that paragraph."""
    paragraphs = [
        row["box"]
        for row, decision in zip(rows, decisions, strict=True)
        if row["text"] and len(row["text"]) >= 60 and decision["action"] == "remove"
    ]
    for box in paragraphs:
        x, y, w, h = box
        members = [
            (row, decision)
            for row, decision in zip(rows, decisions, strict=True)
            if row["type"] == "sp"
            and decision["reason"] == "unlabelled_vector_preserved"
            and 0 < row["box"][2] * row["box"][3] <= 0.005
            and x - 0.01 <= row["box"][0] + row["box"][2] / 2 <= x + w + 0.01
            and y - 0.01 <= row["box"][1] + row["box"][3] / 2 <= y + h + 0.01
        ]
        if len(members) >= 3:
            for _, decision in members:
                decision.update(
                    role="content", action="remove", reason="vector_bullet_in_sample_text"
                )


def remove_percentage_chart_artwork(rows: list[dict], decisions: list[dict]) -> None:
    """Chart artwork surrounding removed percentage labels is subject content."""
    percentages = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if decision["action"] == "remove"
        and re.fullmatch(r"\s*\d+(?:[.,]\d+)?\s*%\s*", row["text"])
    ]
    if len(percentages) < 2:
        return
    for row, decision in zip(rows, decisions, strict=True):
        x, y, w, h = row["box"]
        if (
            decision["action"] != "keep"
            or not row["asset"]
            or decision["reason"] in {"explicit_brand_identity", "recurring_fixed_edge_asset"}
            or w * h >= 0.65
        ):
            continue
        if any(
            x - 0.03 <= label["box"][0] + label["box"][2] / 2 <= x + w + 0.03
            and y - 0.03 <= label["box"][1] + label["box"][3] / 2 <= y + h + 0.03
            for label in percentages
        ):
            decision.update(role="content", action="remove", reason="sample_percentage_chart")


def remove_labelled_card_backings(rows: list[dict], decisions: list[dict]) -> None:
    """A card enclosing a removed icon beside a removed label is foreground."""
    media = [
        row["box"]
        for row, decision in zip(rows, decisions, strict=True)
        if row["asset"] and decision["action"] == "remove"
    ]
    labels = [
        row["box"]
        for row, decision in zip(rows, decisions, strict=True)
        if row["text"] and decision["action"] == "remove"
    ]
    for row, decision in zip(rows, decisions, strict=True):
        x, y, w, h = row["box"]
        if (
            decision["action"] != "keep"
            or row["type"] != "sp"
            or row["preset"] not in {"rect", "roundRect"}
            or not (0.002 <= w * h <= 0.1 and w <= 0.4 and h <= 0.25 and 0.1 < y < 0.9)
        ):
            continue
        icon_inside = any(
            x <= mx + mw / 2 <= x + w and y <= my + mh / 2 <= y + h for mx, my, mw, mh in media
        )
        label_beside = any(
            x + w - 0.05 <= tx + tw / 2 <= x + w + 0.2 and abs(ty + th / 2 - y - h / 2) <= 0.05
            for tx, ty, tw, th in labels
        )
        if icon_inside and label_beside:
            decision.update(role="content", action="remove", reason="labelled_icon_card")


def remove_content_panel_members(rows: list[dict], decisions: list[dict]) -> None:
    """Small labels and pictograms inside removed sample panels are foreground."""
    panels = [
        row["box"]
        for row, decision in zip(rows, decisions, strict=True)
        if decision["reason"]
        in {"asset_behind_sample_content", "subject_mockup_unit", "repeated_foreground_media_units"}
        and row["box"][2] * row["box"][3] >= 0.04
    ]
    for row, decision in zip(rows, decisions, strict=True):
        x, y, w, h = row["box"]
        if (
            decision["action"] != "keep"
            or decision["reason"] == "explicit_brand_identity"
            or w * h > 0.05
            or not panels
        ):
            continue
        if any(
            px - 0.01 <= x + w / 2 <= px + pw + 0.01 and py - 0.01 <= y + h / 2 <= py + ph + 0.03
            for px, py, pw, ph in panels
        ):
            decision.update(
                role="content", action="remove", reason="member_of_removed_sample_panel"
            )
    if sum(w * h >= 0.08 for _, _, w, h in panels) < 3:
        return
    footers = []
    for row, decision in zip(rows, decisions, strict=True):
        x, y, w, h = row["box"]
        if (
            decision["action"] == "keep"
            and row["asset"]
            and decision["reason"] != "explicit_brand_identity"
            and x >= 0.35
            and y >= 0.82
            and w >= 0.3
            and h <= 0.18
        ):
            decision.update(role="content", action="remove", reason="sample_card_grid_footer")
            footers.append(row["box"])
    for row, decision in zip(rows, decisions, strict=True):
        if decision["action"] != "keep" or decision["reason"] == "explicit_brand_identity":
            continue
        if any(_contained(row["box"], footer, 0.01) for footer in footers):
            decision.update(role="content", action="remove", reason="member_of_sample_grid_footer")


def remove_placeholder_companions(rows: list[dict], decisions: list[dict]) -> None:
    """Repeated icons and their frames belong to the adjacent content placeholders."""
    placeholders = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if row["placeholder"] is not None and decision["action"] == "remove"
    ]
    if len(placeholders) < 3:
        return
    candidate_ids = set()
    for row, decision in zip(rows, decisions, strict=True):
        if decision["action"] != "keep" or decision["reason"] == "explicit_brand_identity":
            continue
        x, y, w, h = row["box"]
        if not (0 < w <= 0.12 and 0 < h <= 0.14 and 0.002 <= w * h <= 0.02):
            continue
        beside = any(
            -0.10 <= placeholder["box"][0] - x - w <= 0.07
            and abs(placeholder["box"][1] + placeholder["box"][3] / 2 - y - h / 2) <= 0.05
            for placeholder in placeholders
        )
        if beside:
            candidate_ids.add(row["id"])
    if len(candidate_ids) < 3:
        return
    for row, decision in zip(rows, decisions, strict=True):
        if row["id"] in candidate_ids or (
            decision["action"] == "keep"
            and decision["reason"] != "explicit_brand_identity"
            and any(
                _contained(row["box"], candidate["box"], 0.015)
                for candidate in rows
                if candidate["id"] in candidate_ids
            )
        ):
            decision.update(role="content", action="remove", reason="placeholder_icon_unit")


def remove_subject_mockups(rows: list[dict], decisions: list[dict]) -> None:
    """A phone/browser frame around removed subject media is part of that mockup."""
    subject = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if row["asset"] and decision["action"] == "remove"
    ]
    frames = []
    for row, decision in zip(rows, decisions, strict=True):
        x, y, w, h = row["box"]
        if (
            row["type"] != "pic"
            or decision["action"] != "keep"
            or decision["reason"] == "explicit_brand_identity"
            or w < 0.12
            or h < 0.2
            or w * h >= 0.65
        ):
            continue
        if any(
            _contained(item["box"], row["box"], 0.03)
            and item["box"][2] * item["box"][3] >= 0.4 * w * h
            for item in subject
        ):
            frames.append(row)
    for row, decision in zip(rows, decisions, strict=True):
        if decision["action"] != "keep" or decision["reason"] == "explicit_brand_identity":
            continue
        if any(
            row["id"] == frame["id"]
            or (
                row["box"][2] * row["box"][3] < frame["box"][2] * frame["box"][3]
                and _contained(row["box"], frame["box"], 0.03)
            )
            for frame in frames
        ):
            decision.update(role="content", action="remove", reason="subject_mockup_unit")


def _contained(inner: tuple, outer: tuple, margin: float) -> bool:
    x, y, w, h = inner
    ox, oy, ow, oh = outer
    return (
        w > 0
        and h > 0
        and ox - margin <= x
        and oy - margin <= y
        and x + w <= ox + ow + margin
        and y + h <= oy + oh + margin
    )


def _remove_sample_content_surfaces(rows: list[dict], decisions: list[dict]) -> None:
    """A large picture behind many sample fields is a content panel, not a background."""
    labels = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if row["text"] and decision["action"] == "remove"
    ]
    for row, decision in zip(rows, decisions, strict=True):
        if (
            not row["asset"]
            or decision["action"] != "keep"
            or decision["reason"] == "explicit_brand_identity"
        ):
            continue
        x, y, w, h = row["box"]
        if not (0.05 <= w * h <= 0.9 and x >= 0 and y > 0.12):
            continue
        inside = sum(
            x <= label["box"][0] + label["box"][2] / 2 <= x + w
            and y <= label["box"][1] + label["box"][3] / 2 <= y + h
            for label in labels
        )
        if inside >= 3:
            decision.update(role="content", action="remove", reason="asset_behind_sample_content")


def _remove_content_grid_dividers(rows: list[dict], decisions: list[dict]) -> None:
    """Discard an interior grid once its sample content has been removed."""
    labels = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if row["text"] and decision["action"] == "remove"
    ]
    if len(labels) < 4:
        return
    lines = []
    for row, decision in zip(rows, decisions, strict=True):
        if row["type"] != "cxnSp" or decision["action"] != "keep":
            continue
        x, y, w, h = row["box"]
        if not (0.1 < x < 0.9 and 0.1 < y < 0.9 and x + w < 0.95 and y + h < 0.95):
            continue
        if (w >= 0.2 and h <= 0.005) or (h >= 0.2 and w <= 0.005):
            lines.append((row, decision))
    if len(lines) < 2:
        return
    for row, decision in lines:
        x, y, w, h = row["box"]
        if w >= h:
            before = any(label["box"][1] + label["box"][3] / 2 < y for label in labels)
            after = any(label["box"][1] + label["box"][3] / 2 > y for label in labels)
        else:
            before = any(label["box"][0] + label["box"][2] / 2 < x for label in labels)
            after = any(label["box"][0] + label["box"][2] / 2 > x for label in labels)
        if before and after:
            decision.update(role="content", action="remove", reason="sample_content_grid_divider")


def refine_content_media(rows: list[dict], decisions: list[dict]) -> None:
    removed_media = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if row["asset"] and decision["action"] == "remove"
    ]
    labels = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if row["text"] and decision["action"] == "remove"
    ]
    for row, decision in zip(rows, decisions, strict=True):
        if (
            not row["asset"]
            or decision["action"] != "keep"
            or decision["reason"] == "explicit_brand_identity"
        ):
            continue
        x, y, w, h = row["box"]
        same_unit = sum(
            other["asset"] == row["asset"]
            and abs(other["box"][0] - x) <= 0.02
            and abs(other["box"][2] - w) <= 0.02
            and abs(other["box"][3] - h) <= 0.02
            for other in removed_media
        )
        if same_unit >= 2 and 0.15 < y < 0.9 and w * h <= 0.02:
            decision.update(role="content", action="remove", reason="peer_of_removed_icon_unit")
            continue
        peers = sum(
            abs(other["box"][2] - w) <= 0.025 and abs(sum(other["box"][1::2]) - y - h) <= 0.06
            for other in removed_media
        )
        if peers >= 3 and decision["reason"] != "recurring_fixed_edge_asset":
            decision.update(role="content", action="remove", reason="peer_of_removed_chart_media")
            continue
        if not (w >= 0.6 and 0 < h <= 0.03 and 0.25 <= y <= 0.85):
            continue
        above = below = 0
        for label in labels:
            tx, ty, tw, th = label["box"]
            if not x <= tx + tw / 2 <= x + w:
                continue
            center = ty + th / 2
            if 0 < y - center <= 0.18:
                above += 1
            elif 0 < center - y - h <= 0.18:
                below += 1
        if above >= 2 and below >= 2:
            decision.update(role="content", action="remove", reason="sample_timeline_rule")

