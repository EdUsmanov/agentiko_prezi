"""Small geometric primitives shared by template and artwork analysis."""

from .models import Box


def box(shape):
    return Box(
        x=shape.left / 12700, y=shape.top / 12700, w=shape.width / 12700, h=shape.height / 12700
    )


def intersects(a, b):
    return (
        min(a.x + a.w, b.x + b.w) - max(a.x, b.x) > 2
        and min(a.y + a.h, b.y + b.h) - max(a.y, b.y) > 2
    )
