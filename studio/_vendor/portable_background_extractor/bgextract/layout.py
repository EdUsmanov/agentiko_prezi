"""Inherited content scaffolds and slide navigation in extracted backgrounds."""

from studio._vendor.portable_background_extractor.bgextract.context import _contained


def remove_web_link_button(rows: list[dict], decisions: list[dict]) -> None:
    """The arrow preceding a replaceable web address is part of that address."""
    links = [
        row["box"]
        for row, decision in zip(rows, decisions, strict=True)
        if decision["action"] == "remove" and "web address" in row["text"].casefold()
    ]
    for tx, ty, _, th in links:
        for row, decision in zip(rows, decisions, strict=True):
            x, y, w, h = row["box"]
            if (
                decision["action"] != "keep"
                or row["type"] != "sp"
                or row["preset"] != "rect"
                or not (0.01 <= w <= 0.05 and 0.02 <= h <= 0.08)
                or abs(x + w - tx) > 0.02
                or abs(y + h / 2 - ty - th / 2) > 0.02
            ):
                continue
            decision.update(role="content", action="remove", reason="web_link_navigation_button")
            for member, member_decision in zip(rows, decisions, strict=True):
                if member_decision["action"] == "keep" and _contained(
                    member["box"], row["box"], 0.005
                ):
                    member_decision.update(
                        role="content", action="remove", reason="web_link_navigation_glyph"
                    )


def remove_footer_status_icons(rows: list[dict], decisions: list[dict]) -> None:
    """Tightly spaced status pictograms in a mock desktop footer are sample UI."""
    icons = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if row["asset"]
        and decision["action"] == "keep"
        and decision["reason"] != "explicit_brand_identity"
        and row["box"][0] >= 0.55
        and row["box"][1] >= 0.9
        and 0 < row["box"][2] <= 0.05
        and 0 < row["box"][3] <= 0.06
    ]
    if len(icons) < 3:
        return
    for row in icons:
        peers = [
            other
            for other in icons
            if abs(other["box"][1] - row["box"][1]) <= 0.025
            and abs(other["box"][0] - row["box"][0]) <= 0.08
        ]
        if len(peers) >= 3:
            for peer, decision in zip(rows, decisions, strict=True):
                if peer in peers:
                    decision.update(
                        role="content", action="remove", reason="sample_system_status_icon"
                    )


def remove_layout_content_scaffold(rows: list[dict], decisions: list[dict]) -> None:
    """Discard inherited frames and dividers whose purpose is to hold sample fields."""
    fields = [
        row["box"]
        for row, decision in zip(rows, decisions, strict=True)
        if row["placeholder"] is not None and decision["action"] == "remove"
    ]
    if len(fields) < 2 and not any(w * h >= 0.25 for _, _, w, h in fields):
        return
    for row, decision in zip(rows, decisions, strict=True):
        if decision["action"] != "keep" or decision["reason"] != "static_inherited_artwork":
            continue
        x, y, w, h = row["box"]
        if row["type"] == "pic" and 0.1 <= w <= 0.6 and 0.4 <= h <= 0.95:
            if any(
                _contained(field, row["box"], 0.02) and field[2] * field[3] >= 0.45 * w * h
                for field in fields
            ):
                decision.update(
                    role="content", action="remove", reason="inherited_placeholder_mockup"
                )
                continue
        if row["type"] == "sp" and 0.01 <= w * h < 0.65 and 0.02 <= x and x + w < 0.98:
            if any(_contained(field, row["box"], 0.02) for field in fields):
                decision.update(role="content", action="remove", reason="inherited_content_frame")
                continue
        if row["type"] != "cxnSp" or not (0.05 < x and 0.1 < y and x + w < 0.95):
            continue
        if w >= 0.2 and h <= 0.01:
            before = sum(
                fy + fh / 2 < y and fx < x + w and fx + fw > x for fx, fy, fw, fh in fields
            )
            after = sum(fy + fh / 2 > y and fx < x + w and fx + fw > x for fx, fy, fw, fh in fields)
            crossing = sum(
                fy <= y <= fy + fh and fw <= 0.2 and fx < x + w and fx + fw > x
                for fx, fy, fw, fh in fields
            )
            if (before >= 2 and after >= 2) or crossing >= 2:
                decision.update(role="content", action="remove", reason="inherited_content_divider")
        elif h >= 0.2 and w <= 0.01:
            before = sum(
                fx + fw / 2 < x and fy < y + h and fy + fh > y for fx, fy, fw, fh in fields
            )
            after = sum(fx + fw / 2 > x and fy < y + h and fy + fh > y for fx, fy, fw, fh in fields)
            if before >= 2 and after >= 2:
                decision.update(role="content", action="remove", reason="inherited_content_divider")
    frames = [
        row["box"]
        for row, decision in zip(rows, decisions, strict=True)
        if decision["reason"] == "inherited_content_frame"
    ]
    headers = []
    for row, decision in zip(rows, decisions, strict=True):
        x, y, w, h = row["box"]
        if decision["action"] == "keep" and row["type"] == "sp" and 0 < w * h <= 0.1:
            inside = any(
                fx <= x + w / 2 <= fx + fw and fy <= y + h / 2 <= fy + fh
                for fx, fy, fw, fh in frames
            )
            header = any(
                abs(x - fx) <= 0.03
                and abs(w - fw) <= 0.03
                and -0.02 <= fy - y - h <= 0.07
                and h <= 0.12
                for fx, fy, fw, fh in frames
            )
            if inside or header:
                decision.update(
                    role="content", action="remove", reason="member_of_inherited_content_frame"
                )
                if header:
                    headers.append(row["box"])
    for row, decision in zip(rows, decisions, strict=True):
        if (
            decision["action"] == "keep"
            and row["type"] == "sp"
            and any(_contained(row["box"], header, 0.01) for header in headers)
        ):
            decision.update(
                role="content", action="remove", reason="member_of_inherited_card_header"
            )


def remove_navigation_dots(rows: list[dict], decisions: list[dict]) -> None:
    """Footer progress dots describe the example deck, not its reusable background."""
    dots = [
        row
        for row in rows
        if row["type"] == "sp"
        and row["preset"] == "ellipse"
        and not row["text"]
        and 0.5 <= row["box"][1] <= 0.96
        and 0 < row["box"][2] <= 0.025
        and 0 < row["box"][3] <= 0.04
    ]
    remove = set()
    for dot in dots:
        peers = [
            other
            for other in dots
            if abs(other["box"][1] - dot["box"][1]) <= 0.01
            and abs(other["box"][2] - dot["box"][2]) <= 0.005
        ]
        if 3 <= len(peers) <= 10 and (
            dot["box"][1] >= 0.82 or all(peer["box"][0] < 0.3 for peer in peers)
        ):
            remove.update(other["id"] for other in peers)
    for row, decision in zip(rows, decisions, strict=True):
        if row["id"] in remove:
            decision.update(role="content", action="remove", reason="sample_slide_navigation")


def remove_numbered_timeline_rule(rows: list[dict], decisions: list[dict]) -> None:
    """Remove an axis through replaceable numbered steps or circular subject slots."""
    steps = [
        (row["box"], row["text"].strip().isdigit(), decision["reason"] == "repeated_avatar_slot")
        for row, decision in zip(rows, decisions, strict=True)
        if decision["action"] == "remove"
        and row["type"] == "sp"
        and row["preset"] == "ellipse"
        and (row["text"].strip().isdigit() or decision["reason"] == "repeated_avatar_slot")
    ]
    if len(steps) < 2:
        return
    for row, decision in zip(rows, decisions, strict=True):
        x, y, w, h = row["box"]
        if decision["action"] != "keep" or row["type"] != "cxnSp" or w < 0.5 or h > 0.02:
            continue
        intersecting = [
            (numbered, subject_slot)
            for (sx, sy, sw, sh), numbered, subject_slot in steps
            if abs(y - sy - sh / 2) <= 0.07 and x - 0.02 <= sx + sw / 2 <= x + w + 0.02
        ]
        if (
            sum(numbered for numbered, _ in intersecting) >= 3
            or sum(subject_slot for _, subject_slot in intersecting) >= 2
        ):
            decision.update(role="content", action="remove", reason="numbered_timeline_axis")

