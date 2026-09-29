"""Pure template color and shape geometry utilities."""

from studio.models import Box

EMU = 12700


def luminance(color):
    values = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    values = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
    return sum(x * w for x, w in zip(values, [0.2126, 0.7152, 0.0722]))


def contrast(a, b):
    light, dark = sorted([luminance(a), luminance(b)], reverse=True)
    return (light + 0.05) / (dark + 0.05)


def minimum_text_contrast(size, bold=False):
    """WCAG contrast threshold for text measured in typographic points."""
    return 3.0 if size >= 18 or bold and size >= 14 else 4.5


def color_value(color):
    try:
        return "#" + str(color.rgb) if color.type and color.rgb else None
    except (AttributeError, ValueError, TypeError):
        return None


def walk_shapes(shapes, sx=1.0, sy=1.0, ox=0.0, oy=0.0):
    for shape in shapes:
        box = Box(
            x=(ox + shape.left * sx) / EMU,
            y=(oy + shape.top * sy) / EMU,
            w=shape.width * sx / EMU,
            h=shape.height * sy / EMU,
        )
        yield shape, box
        if hasattr(shape, "shapes"):
            xf = shape._element.grpSpPr.xfrm
            if xf is not None and xf.chExt is not None:
                nsx, nsy = shape.width / max(xf.chExt.cx, 1), shape.height / max(xf.chExt.cy, 1)
                yield from walk_shapes(
                    shape.shapes,
                    sx * nsx,
                    sy * nsy,
                    ox + sx * (shape.left - xf.chOff.x * nsx),
                    oy + sy * (shape.top - xf.chOff.y * nsy),
                )
