"""Measured space for editable horizontal charts, shared by selection and export."""

from dataclasses import dataclass
from math import ceil
import re

from studio.templates.fonts import element_font, text_width, wrap_text


@dataclass(frozen=True)
class BarLayout:
    labels: tuple[str, ...]
    heading: str
    heading_height: float
    chart_height: float
    left: float
    top: float
    width: float
    height: float
    label_size: float
    fits: bool


def wrap_category(text, font, size, width):
    # Category identifiers must survive whitespace normalization unchanged.
    # A single oversized token is rejected rather than split into new words.
    lines = []
    current = ""
    for word in text.split():
        candidate = (current + " " + word).strip()
        if current and text_width(candidate, font, size) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    return "\n".join([*lines, current])


def bar_layout(element, profile, caption_height=0):
    """Reserve actual type widths and row heights; never abbreviate source labels."""
    font = element_font(profile, element)[1]
    size = min(element.size, 16)
    width = element.box.w
    chart_height = element.box.h - caption_height
    heading_lines = (
        wrap_text(element.category_title, font, size, max(1, width - 12))
        if element.category_title
        else []
    )
    heading_height = len(heading_lines) * size * 1.25 + (8 if heading_lines else 0)
    chart_height -= heading_height
    # Leave most of the plot available for values, while wrapping long categories.
    label_width = min(
        width * 0.43, max((text_width(label, font, size) for label in element.labels), default=0)
    )
    labels = tuple(
        wrap_category(label, font, size, max(1, label_width)) for label in element.labels
    )
    left = label_width + 12
    series_count = len(element.series_values) or 1
    names = element.series_names if series_count > 1 else []
    legend_width = sum(text_width(name, font, size) + 28 for name in names)
    legend_lines = max(1, ceil(legend_width / max(1, width))) if names else 0
    bottom = size * 1.8 + legend_lines * size * 1.6
    if series_count == 1 and element.unit:
        bottom += len(wrap_text(element.unit, font, size, max(1, width - left))) * size * 1.25 + 4
    top = size * 0.5
    # Numeric labels outside bar ends need their own horizontal reserve.
    values = [value for series in element.series_values for value in series] or element.values
    suffixes = {re.sub(r"^[\s−\-+\d.,]+", "", value).strip() for value in element.value_labels}
    suffix = " " + next(iter(suffixes)) if len(suffixes) == 1 and next(iter(suffixes)) else ""
    number_width = max(
        (
            text_width(f"{value:.8f}".rstrip("0").rstrip(".") + suffix, font, size)
            for value in values
        ),
        default=size,
    )
    right = max(18, number_width + 10)
    if any(value < 0 for value in values):
        left += right
    plot_width = width - left - right
    plot_height = chart_height - top - bottom
    row_height = max(
        max((len(label.splitlines()) * size * 1.25 + 4 for label in labels), default=0),
        series_count * size * 1.15 + 4,
    )
    fits = (
        bool(labels)
        and all(
            text_width(line, font, size) <= label_width + 0.1
            for label in labels
            for line in label.splitlines()
        )
        and width >= 260
        and plot_width >= 100
        and plot_height >= max(70, len(labels) * row_height)
    )
    return BarLayout(
        labels,
        "\n".join(heading_lines),
        heading_height,
        chart_height,
        left,
        top,
        plot_width,
        plot_height,
        size,
        fits,
    )
