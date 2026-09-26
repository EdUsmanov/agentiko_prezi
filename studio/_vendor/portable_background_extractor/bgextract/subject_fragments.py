"""Conservative rules for detached foreground fragments."""

import re

BRAND = re.compile(r"logo|logotype|wordmark|brand(?:ing)?\b|логотип|айдентик", re.I)


def preserve_masked_photo_collage(rows: list[dict], decisions: list[dict]) -> None:
    """Keep two adjacent backdrop photos revealed through a full-slide art mask.

    The images sit below a transparent, limited-palette overlay in OOXML order.
    Together they cover the lower slide edge, making one designed surface rather
    than two independently replaceable picture slots.
    """
    for mask_index, mask in enumerate(rows):
        x, y, w, h = mask["box"]
        image = mask["image"]
        if not (
            mask["type"] == "pic"
            and decisions[mask_index]["role"] == "background"
            and x <= 0.02
            and y <= 0.02
            and w >= 0.98
            and h >= 0.98
            and 0.2 <= image.get("alpha", 0) <= 0.8
            and image.get("colors", 1000) <= 8
        ):
            continue
        photos = [
            (index, row)
            for index, row in enumerate(rows[:mask_index])
            if row["type"] == "pic"
            and decisions[index]["reason"] == "interior_subject_raster"
            and row["image"].get("alpha", 1) == 0
            and 0.3 <= row["box"][2] <= 0.7
            and row["box"][3] >= 0.65
            and row["box"][1] + row["box"][3] >= 0.98
        ]
        if len(photos) != 2:
            continue
        left, right = sorted(photos, key=lambda item: item[1]["box"][0])
        lx, _, lw, _ = left[1]["box"]
        rx, _, rw, _ = right[1]["box"]
        if lx > 0.02 or abs(lx + lw - rx) > 0.02 or rx + rw < 0.98:
            continue
        for index, _ in photos:
            decisions[index].update(
                role="background", action="keep", reason="masked_photo_backdrop"
            )


def subject_fragment_reason(row: dict, inherited: bool, data_slide: bool) -> str | None:
    meta, text = row["metadata"], row["text"]
    x, y, w, h = row["box"]
    if (
        not inherited
        and row["asset"]
        and row["type"] == "pic"
        and 0.2 < x < 0.7
        and y < 0.02
        and 0.5 < w < 0.9
        and h >= 0.95
        and row["image"].get("alpha") == 0
        and row["image"].get("colors", 1000) <= 2
        and not BRAND.search(meta)
    ):
        return "opaque_flat_subject_panel"
    if (
        not inherited
        and row["asset"]
        and 0.15 < x < 0.85
        and 0.2 < y < 0.85
        and 0.02 < w < 0.05
        and h < 0.03
        and row["image"].get("alpha", 0) >= 0.5
        and row["image"].get("colors", 1000) <= 4
        and row["recurring"] == 1
        and not BRAND.search(meta)
    ):
        return "isolated_raster_content_fragment"
    if (
        not inherited
        and row["type"] == "sp"
        and row["preset"] == "ellipse"
        and not text
        and (0.05 < x < 0.8 if data_slide else 0.15 < x < 0.8)
        and 0.15 < y < 0.8
        and 0 < w < 0.025
        and 0 < h < 0.04
        and not row["groups"]
    ):
        return "isolated_interior_data_dot"
    return None


def remove_closing_subject_icons(rows: list[dict], decisions: list[dict]) -> None:
    """A group of large flat illustrations beside a thank-you message is subject art."""
    if not re.search(
        r"\bthank(?:s| you)\b|спасибо|благодар", " ".join(r["text"] for r in rows), re.I
    ):
        return
    candidates = [
        row
        for row, decision in zip(rows, decisions, strict=True)
        if decision["action"] == "keep"
        and decision["reason"] == "transparent_flat_artwork"
        and row["type"] == "pic"
        and row["box"][2] >= 0.15
        and row["box"][3] >= 0.2
        and not BRAND.search(row["metadata"])
    ]
    if len(candidates) < 2:
        return
    ids = {row["id"] for row in candidates}
    for row, decision in zip(rows, decisions, strict=True):
        if row["id"] in ids:
            decision.update(role="content", action="remove", reason="closing_subject_illustration")


def remove_screenshot_header_strips(rows: list[dict], decisions: list[dict]) -> None:
    """Remove detached UI chrome above a discarded wide screenshot.

    A single accent stripe may be genuine design. Require two aligned raster
    strips immediately above the same removed screenshot before discarding them.
    """
    for screenshot, screenshot_role in zip(rows, decisions, strict=True):
        sx, sy, sw, sh = screenshot["box"]
        if not (
            screenshot_role["action"] == "remove"
            and screenshot["type"] == "pic"
            and screenshot_role["reason"] == "interior_subject_raster"
            and sw >= 0.8
            and sh >= 0.5
            and sy >= 0.2
        ):
            continue
        strips = []
        for index, (row, decision) in enumerate(zip(rows, decisions, strict=True)):
            x, y, w, h = row["box"]
            if (
                decision["action"] == "keep"
                and decision["reason"] == "transparent_flat_artwork"
                and row["type"] == "pic"
                and row["recurring"] == 1
                and w >= 0.6
                and h <= 0.08
                and x >= sx - 0.05
                and x + w <= sx + sw + 0.05
                and 0 <= sy - (y + h) <= 0.08
                and not BRAND.search(row["metadata"])
            ):
                strips.append(index)
        if len(strips) < 2:
            continue
        for index in strips:
            decisions[index].update(
                role="content", action="remove", reason="screenshot_header_strip"
            )

