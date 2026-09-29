"""Inspect OOXML image relationships without evaluating Picture.image.

SVG-only and linked pictures are valid shapes but python-pptx's image property
raises when the conventional raster r:embed is absent. Never fetch links.
"""

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
SVG = "{http://schemas.microsoft.com/office/drawing/2016/SVG/main}"


def is_picture(shape):
    return shape._element.tag == P + "pic"


def embedded_blip_blob(blip, part):
    if blip is None:
        return None
    ids = [blip.get(R + "embed")] + [n.get(R + "embed") for n in blip.iter(SVG + "svgBlip")]
    for rid in ids:
        rel = part.rels.get(rid) if rid else None
        if rel and not rel.is_external and rel.reltype.endswith("/image"):
            image_part = rel.target_part
            if image_part.content_type.startswith("image/"):
                return image_part.blob
    return None


def embedded_picture_blob(shape):
    if not is_picture(shape):
        return None
    return embedded_blip_blob(shape._element.find(P + "blipFill/" + A + "blip"), shape.part)
