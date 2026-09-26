"""Pixel and OOXML geometry checks for one safe text rectangle.

Coordinates in slide models come from the background extractor. The VL grid is
only an additional obstacle hint; it never certifies that a cell is readable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class ZoneConfig:
    """Audited defaults; all distances are pixels at the analysis resolution."""

    tile: int = 16
    minimum_width: int = 320
    minimum_height: int = 64
    edge_margin: int = 40
    object_clearance: int = 24
    minimum_contrast: float = 4.5
    flat_fraction: float = 0.60
    flat_color_difference: int = 25
    smooth_edge_fraction: float = 0.003
    fallback_minimum_width: int = 288
    fallback_area_ratio: float = 0.08
    fallback_panel_area_ratio: float = 0.035


def _analysis_image(source: Image.Image, tile: int) -> Image.Image:
    """Use whole tiles; preserve source coordinates separately in the result."""
    if source.width < tile or source.height < tile:
        raise ValueError("The background image is smaller than one search tile")
    width = source.width // tile * tile
    height = max(tile, round(source.height / tile) * tile)
    return source.convert("RGB").resize((width, height))


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Expand obstacles by a uniform clearance using a summed-area table."""
    padded = np.pad(mask.astype(np.int32), ((radius, radius), (radius, radius)))
    summed = np.pad(padded, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    diameter = 2 * radius + 1
    return (
        summed[diameter:, diameter:]
        - summed[:-diameter, diameter:]
        - summed[diameter:, :-diameter]
        + summed[:-diameter, :-diameter]
    ) > 0


def _largest_rectangle(grid: np.ndarray, config: ZoneConfig) -> list[int] | None:
    """Largest all-true rectangle via histogram stacks, O(rows × columns)."""
    heights = [0] * grid.shape[1]
    best: tuple[int, int, int, int, int] | None = None
    min_cols = math.ceil(config.minimum_width / config.tile)
    min_rows = math.ceil(config.minimum_height / config.tile)
    for bottom in range(grid.shape[0]):
        for col in range(grid.shape[1]):
            heights[col] = heights[col] + 1 if grid[bottom, col] else 0
        stack: list[tuple[int, int]] = []
        for col in range(grid.shape[1] + 1):
            current = heights[col] if col < grid.shape[1] else 0
            start = col
            while stack and stack[-1][1] > current:
                left, height = stack.pop()
                start = left
                width = col - left
                if width < min_cols or height < min_rows:
                    continue
                candidate = (width * height, left, bottom + 1 - height, col, bottom + 1)
                if best is None or candidate > best:
                    best = candidate
            if current and (not stack or stack[-1][1] < current):
                stack.append((start, current))
    if best is None:
        return None
    _, left, top, right, bottom = best
    return [v * config.tile for v in (left, top, right, bottom)]


def _dominant_color(array: np.ndarray) -> tuple[np.ndarray, float]:
    height, width = array.shape[:2]
    sample = np.asarray(Image.fromarray(array).resize((160, round(height / width * 160))))
    unique, counts = np.unique(sample.reshape(-1, 3), axis=0, return_counts=True)
    index = int(counts.argmax())
    return unique[index], float(counts[index] / counts.sum())


def _luminance(array: np.ndarray) -> np.ndarray:
    rgb = array.astype(np.float32) / 255
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return linear @ np.array([0.2126, 0.7152, 0.0722])


def _edge_fraction(luma: np.ndarray, box: list[int]) -> float:
    left, top, right, bottom = box
    patch = luma[top:bottom, left:right]
    if patch.size == 0:
        return 0.0
    dx = np.abs(np.diff(patch, axis=1)) > 0.07
    dy = np.abs(np.diff(patch, axis=0)) > 0.07
    return float((dx.sum() + dy.sum()) / (dx.size + dy.size))


def _protected_mask(
    slide: dict[str, Any] | None,
    width: int,
    height: int,
    original_size: tuple[int, int],
) -> np.ndarray:
    """Block exact OOXML boxes, including identity inherited from layouts/masters."""
    mask = np.zeros((height, width), dtype=bool)
    if slide is None:
        return mask
    original_width, original_height = original_size
    for region in slide.get("protectedRegions", []):
        left = round(region["x"] * width / original_width)
        top = round(region["y"] * height / original_height)
        right = round((region["x"] + region["width"]) * width / original_width)
        bottom = round((region["y"] + region["height"]) * height / original_height)
        left, right = max(0, min(width, left)), max(0, min(width, right))
        top, bottom = max(0, min(height, top)), max(0, min(height, bottom))
        if right > left and bottom > top:
            mask[top:bottom, left:right] = True
    return mask


def _vl_mask(cells: set[str], width: int, height: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=bool)
    for cell in cells:
        if len(cell) != 2 or cell[0] not in "ABCDEFGH" or cell[1] not in "12345678":
            raise ValueError(f"Invalid VL grid cell: {cell!r}")
        col, row = ord(cell[0]) - ord("A"), int(cell[1]) - 1
        left, right = round(col * width / 8), round((col + 1) * width / 8)
        top, bottom = round(row * height / 8), round((row + 1) * height / 8)
        mask[top:bottom, left:right] = True
    return mask


def _flat_panels(
    slide: dict[str, Any] | None,
    width: int,
    height: int,
    luma: np.ndarray,
    minimum_contrast: float,
) -> np.ndarray:
    """Recover truly uniform OOXML panels when VL marks a whole photo slide."""
    panels = np.zeros((height, width), dtype=bool)
    if slide is None:
        return panels
    for obj in slide.get("objects", []):
        if obj.get("type") != "sp" or obj.get("role") not in {"decoration", "background"}:
            continue
        x, y, w, h = obj["box"]  # normalized x, y, width, height
        if not 0.05 <= w * h <= 0.7:
            continue
        left, top = max(0, round(x * width)), max(0, round(y * height))
        right, bottom = min(width, round((x + w) * width)), min(height, round((y + h) * height))
        if right - left < 320 or bottom - top < 96:
            continue
        inner = [left + 4, top + 4, right - 4, bottom - 4]
        if _edge_fraction(luma, inner) > 0.002:
            continue
        patch = luma[top:bottom, left:right]
        white = float((1.05 / (patch + 0.05)).min())
        black = float(((patch + 0.05) / 0.05).min())
        if max(white, black) < minimum_contrast:
            continue
        panels[top:bottom, left:right] = True
    return panels


def _find_box(
    array: np.ndarray, blocked: np.ndarray, config: ZoneConfig
) -> tuple[list[int] | None, str | None, float | None, float | None]:
    height, width = blocked.shape
    expanded = _dilate(blocked, config.object_clearance)
    luma = _luminance(array)
    candidates: list[tuple[int, str, list[int], float]] = []
    for color in ("white", "black"):
        contrast = 1.05 / (luma + 0.05) if color == "white" else (luma + 0.05) / 0.05
        free = ~expanded & (contrast >= config.minimum_contrast)
        margin = config.edge_margin
        if margin:
            free[:margin] = False
            free[-margin:] = False
            free[:, :margin] = False
            free[:, -margin:] = False
        # A tile is usable only when *every* pixel passes both checks.
        tiles = free.reshape(
            height // config.tile, config.tile, width // config.tile, config.tile
        ).all(axis=(1, 3))
        box = _largest_rectangle(tiles, config)
        if box is not None:
            left, top, right, bottom = box
            assert free[top:bottom, left:right].all()
            candidates.append(
                (
                    (right - left) * (bottom - top),
                    color,
                    box,
                    float(contrast[top:bottom, left:right].min()),
                )
            )
    if not candidates:
        return None, None, None, None
    _, color, box, contrast = max(candidates)
    return box, color, round(contrast, 2), _edge_fraction(luma, box)


def _local_surface_obstacles(array: np.ndarray, tile: int) -> np.ndarray:
    """Mark visible edges and textured tiles when coarse VL blocks open space.

    This does not replace semantic VL detection. It is a conservative fallback
    for a clear patch inside an otherwise occupied 8×8 cell: solid title panels,
    empty cards, or the smooth center of a decorative contour background.
    """
    height, width = array.shape[:2]
    pixels = array.astype(np.int16)
    busy = np.zeros((height, width), dtype=bool)
    horizontal = np.max(np.abs(pixels[:, 1:] - pixels[:, :-1]), axis=2) > 8
    vertical = np.max(np.abs(pixels[1:] - pixels[:-1]), axis=2) > 8
    busy[:, 1:] |= horizontal
    busy[:, :-1] |= horizontal
    busy[1:] |= vertical
    busy[:-1] |= vertical
    texture = (
        pixels.reshape(height // tile, tile, width // tile, tile, 3).std(axis=(1, 3)).max(axis=2)
        > 4
    )
    busy |= np.repeat(np.repeat(texture, tile, axis=0), tile, axis=1)
    return busy


def _inside_design_panel(
    box: list[int], slide: dict[str, Any] | None, width: int, height: int
) -> bool:
    """Allow a shorter title zone only when OOXML confirms a designed panel."""
    if slide is None:
        return False
    left, top, right, bottom = box
    area = (right - left) * (bottom - top)
    for obj in slide.get("objects", []):
        if obj.get("type") != "sp" or obj.get("role") not in {"decoration", "background"}:
            continue
        x, y, w, h = obj["box"]
        if not 0.05 <= w * h <= 0.7:
            continue
        ox1, oy1 = x * width, y * height
        ox2, oy2 = (x + w) * width, (y + h) * height
        overlap = max(0, min(right, ox2) - max(left, ox1)) * max(
            0, min(bottom, oy2) - max(top, oy1)
        )
        if overlap >= 0.7 * area:
            return True
    return False


def analyze_image(
    source: Image.Image,
    slide: dict[str, Any] | None = None,
    vl_cells: set[str] | None = None,
    config: ZoneConfig = ZoneConfig(),
) -> dict[str, Any]:
    """Return a safe candidate or an explicit abstention for one PNG slide."""
    image = _analysis_image(source, config.tile)
    width, height = image.size
    array = np.asarray(image)
    structural = _protected_mask(slide, width, height, source.size)
    base, modal_fraction = _dominant_color(array)
    pixel_guard = modal_fraction >= config.flat_fraction
    luma = _luminance(array)
    global_edges = _edge_fraction(luma, [0, 0, width, height])
    cells = len(vl_cells) if vl_cells is not None else None
    reason = None
    if pixel_guard:
        # On a flat slide the exact pixels give more precision than 8×8 cells.
        blocked = (
            np.max(np.abs(array.astype(np.int16) - base), axis=2) > config.flat_color_difference
        ) | structural
        mode = "flat_pixel_mask"
    elif global_edges < config.smooth_edge_fraction and (vl_cells is None or cells == 64):
        # A smooth gradient can be wrongly marked as occupied in every VL cell.
        blocked = structural
        mode = "smooth_surface"
    elif vl_cells is None:
        # A complex image needs semantic object recognition to avoid photos.
        blocked = None
        mode = "complex_surface_requires_vl"
        reason = "vl_required_for_complex_surface"
    else:
        vl = _vl_mask(vl_cells, width, height)
        blocked = vl | structural
        mode = "vl_and_geometry"
        if cells >= 60:
            panels = _flat_panels(slide, width, height, luma, config.minimum_contrast)
            if panels.any():
                # VL can mark a whole photo slide; recover only its uniform
                # panel while retaining every protected identity/decor box.
                blocked = (vl & ~panels) | structural
                mode = "vl_with_flat_panel"
    if blocked is None:
        analysis_box, color, contrast, edges = None, None, None, None
    else:
        analysis_box, color, contrast, edges = _find_box(array, blocked, config)
    current_area = (
        (analysis_box[2] - analysis_box[0]) * (analysis_box[3] - analysis_box[1]) / (width * height)
        if analysis_box
        else 0.0
    )
    if mode in {"vl_and_geometry", "vl_with_flat_panel"} and current_area < 0.10:
        # An 8×8 cell can contain an object and still contain a usable flat
        # patch. Recover it only after checking every local pixel and the
        # exact protected geometry; keep the original stricter result if better.
        local_blocked = _local_surface_obstacles(array, config.tile) | structural
        local_config = replace(config, minimum_width=config.fallback_minimum_width)
        local_box, local_color, local_contrast, local_edges = _find_box(
            array, local_blocked, local_config
        )
        if local_box is not None:
            local_area = (
                (local_box[2] - local_box[0]) * (local_box[3] - local_box[1]) / (width * height)
            )
            supported_panel = _inside_design_panel(local_box, slide, width, height)
            enough_space = local_area >= config.fallback_area_ratio or (
                supported_panel and local_area >= config.fallback_panel_area_ratio
            )
            if enough_space and local_area > current_area:
                analysis_box, color, contrast, edges = (
                    local_box,
                    local_color,
                    local_contrast,
                    local_edges,
                )
                mode = "local_surface_fallback"
    if analysis_box is None and reason is None:
        reason = "no_clear_rectangle"
    if analysis_box is None:
        box = None
        area_ratio = 0.0
    else:
        sx, sy = source.width / width, source.height / height
        left, top, right, bottom = analysis_box
        box = [round(left * sx), round(top * sy), round(right * sx), round(bottom * sy)]
        area_ratio = (right - left) * (bottom - top) / (width * height)
    return {
        "box": box,
        "analysis_box": analysis_box,
        "text_color": color,
        "area_ratio": round(area_ratio, 4),
        "minimum_contrast": contrast,
        "edge_fraction": round(edges, 4) if edges is not None else None,
        "reason": reason,
        "recognition_mode": mode,
        "vl_cells": cells,
        "protected_regions": len(slide.get("protectedRegions", [])) if slide else 0,
        "analysis_size": [width, height],
        "source_size": list(source.size),
    }

