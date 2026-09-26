"""Recover a smooth canvas when sample artwork is baked into its raster pixels."""

from __future__ import annotations

import io
from collections.abc import Iterable

from PIL import Image, UnidentifiedImageError


def _corner_color(image: Image.Image) -> tuple[float, float, float]:
    w, h = image.size
    pixels = [image.getpixel(point) for point in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    return tuple(sum(pixel[channel] for pixel in pixels) / 4 for channel in range(3))


def smooth_light_canvas(payload: bytes) -> bool:
    """Limit reconstruction to very light, nearly uniform-edge illustration canvases."""
    try:
        with Image.open(io.BytesIO(payload)) as source:
            if source.width < 300 or source.height < 200:
                return False
            if source.mode in {"RGBA", "LA"} and source.getchannel("A").getextrema()[0] < 250:
                return False
            image = source.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return False
    base = _corner_color(image)
    w, h = image.size
    corners = [image.getpixel(point) for point in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    if (
        min(base) <= 200
        or max(abs(pixel[channel] - base[channel]) for pixel in corners for channel in range(3))
        >= 20
    ):
        return False
    thumbnail = image.copy()
    thumbnail.thumbnail((128, 72))
    pixels = list(thumbnail.get_flattened_data())
    near_base = sum(
        max(abs(pixel[channel] - base[channel]) for channel in range(3)) <= 40 for pixel in pixels
    )
    return near_base / len(pixels) >= 0.95


def large_white_content_surface(payload: bytes) -> list[dict]:
    """Find a pale full-slide card when VL overlooks its very faint outline."""
    with Image.open(io.BytesIO(payload)) as source:
        image = source.convert("RGB")
    base = _corner_color(image)
    if min(base) >= 252:
        return []
    w, h = image.size
    points = [(0.3, 0.5), (0.5, 0.5), (0.7, 0.5), (0.5, 0.1)]
    if all(min(image.getpixel((int(w * x), int(h * y)))) >= 254 for x, y in points):
        return [
            {
                "x1": 0,
                "y1": 0,
                "x2": 1000,
                "y2": 1000,
                "reason": "large pale content card distinct from the outer canvas",
            }
        ]
    return []


def _basis(u: float, v: float) -> tuple[float, ...]:
    return (1.0, u, v, u * u, u * v, v * v)


def _solve(matrix: list[list[float]], target: list[float]) -> list[float]:
    size = len(target)
    augmented = [row[:] + [value] for row, value in zip(matrix, target, strict=True)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda index: abs(augmented[index][col]))
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        scale = augmented[col][col]
        if abs(scale) < 1e-8:
            return [target[0] / max(1.0, matrix[0][0]), *([0.0] * (size - 1))]
        for index in range(col, size + 1):
            augmented[col][index] /= scale
        for row in range(size):
            if row == col:
                continue
            factor = augmented[row][col]
            for index in range(col, size + 1):
                augmented[row][index] -= factor * augmented[col][index]
    return [augmented[row][size] for row in range(size)]


def _outside_regions(u: float, v: float, regions: list[dict]) -> bool:
    return not any(
        max(0, region["x1"] / 1000 - 0.02) <= u <= min(1, region["x2"] / 1000 + 0.02)
        and max(0, region["y1"] / 1000 - 0.02) <= v <= min(1, region["y2"] / 1000 + 0.02)
        for region in regions
    )


def _fit(image: Image.Image, regions: list[dict]) -> list[list[float]]:
    matrix = [[0.0] * 6 for _ in range(6)]
    targets = [[0.0] * 6 for _ in range(3)]
    samples = 0
    for yi in range(37):
        v = yi / 36
        for xi in range(65):
            u = xi / 64
            if not _outside_regions(u, v, regions):
                continue
            pixel = image.getpixel((round(u * (image.width - 1)), round(v * (image.height - 1))))
            if min(pixel) < 190:
                continue
            features = _basis(u * 2 - 1, v * 2 - 1)
            for row in range(6):
                for col in range(6):
                    matrix[row][col] += features[row] * features[col]
                for channel in range(3):
                    targets[channel][row] += features[row] * pixel[channel]
            samples += 1
    if samples < 20:
        base = _corner_color(image)
        return [[color, 0, 0, 0, 0, 0] for color in base]
    for index in range(6):
        matrix[index][index] += 1e-6
    return [_solve(matrix, target) for target in targets]


def reconstruct_regions(payload: bytes, regions: Iterable[dict]) -> bytes:
    """Fit the unaffected pixels and remove every baked card or chart from the canvas."""
    regions = list(regions)
    if not regions:
        return payload
    with Image.open(io.BytesIO(payload)) as source:
        original = source.convert("RGB")
        image_format = "JPEG" if source.format == "JPEG" else "PNG"
    base = _corner_color(original)
    coefficients = _fit(original, regions)
    grid = Image.new("RGB", (129, 73))
    pixels = grid.load()
    for yi in range(73):
        v = yi / 36 - 1
        for xi in range(129):
            u = xi / 64 - 1
            features = _basis(u, v)
            pixels[xi, yi] = tuple(
                round(
                    max(
                        0,
                        min(
                            255,
                            max(
                                base[channel] - 35,
                                min(
                                    base[channel] + 35,
                                    sum(
                                        coefficients[channel][index] * features[index]
                                        for index in range(6)
                                    ),
                                ),
                            ),
                        ),
                    )
                )
                for channel in range(3)
            )
    image = grid.resize(original.size, Image.Resampling.BICUBIC)
    output = io.BytesIO()
    if image_format == "JPEG":
        image.save(output, format="JPEG", quality=98, subsampling=0)
    else:
        image.save(output, format="PNG")
    return output.getvalue()

