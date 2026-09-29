"""Effective per-shape text styles, including empty POTX placeholders.

The archive must pass validate_pptx before this adapter is constructed.
No external relationships are followed. The vendored resolver handles theme
mapping and color transforms; dominant run color is weighted by actual text.
"""

from collections import Counter
from zipfile import ZipFile
from defusedxml.ElementTree import fromstring
from studio._vendor.color_extraction.pipeline.ooxml_resolution import (
    inheritance_parts,
    effective_theme,
)
from studio._vendor.color_extraction.pipeline.reference_color_elements import _text_color
from studio._vendor.color_extraction.pipeline.reference_color_values import theme_colors
from studio._vendor.color_extraction.pipeline.reference_font_usage_styles import (
    _parent_shape,
    _role,
    _source_properties,
    _effective_font,
    _theme_fonts,
)

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def native_styles(path):
    styles = {}
    with ZipFile(path) as archive:
        presentation = fromstring(archive.read("ppt/presentation.xml"))
        for part in archive.namelist():
            if not part.endswith(".xml") or not part.startswith(
                ("ppt/slides/slide", "ppt/slideLayouts/slideLayout")
            ):
                continue
            names = inheritance_parts(archive, part)
            roots = [fromstring(archive.read(name)) for name in names]
            origins = [
                "master"
                if r.tag == P + "sldMaster"
                else "layout"
                if r.tag == P + "sldLayout"
                else "slide"
                for r in roots
            ]
            master = next((r for r in roots if r.tag == P + "sldMaster"), None)
            theme, mapping = effective_theme(archive, part)
            palette = theme_colors(theme)
            for shape in roots[0].findall(".//" + P + "sp"):
                identity = shape.find(P + "nvSpPr/" + P + "cNvPr")
                if identity is None:
                    continue
                chain = [(origins[0], shape)]
                previous = shape
                for origin, root in zip(origins[1:], roots[1:]):
                    parent = _parent_shape(previous, root, master=origin == "master")
                    if parent is not None:
                        chain.append((origin, parent))
                        previous = parent
                colors, sizes, faces = Counter(), Counter(), Counter()
                role = _role(shape, "shape")
                for paragraph in shape.findall(P + "txBody/" + A + "p"):
                    runs = [r for r in paragraph if r.tag in (A + "r", A + "fld")]
                    for run in runs or [None]:
                        weight = max(1, len(run.findtext(A + "t", "") if run is not None else ""))
                        values, _, _ = _text_color(
                            paragraph,
                            run,
                            chain,
                            master,
                            presentation,
                            role,
                            origins[0],
                            theme,
                            palette,
                            mapping,
                        )
                        for color in values:
                            if (
                                color["opacity"] == 1
                                and "component" not in color
                                and "position" not in color
                            ):
                                colors[color["hex"].upper()] += weight
                        properties = _source_properties(
                            paragraph, run, chain, master, presentation, role, origins[0]
                        )
                        resolved = _effective_font(properties, "latin", role, _theme_fonts(theme))
                        if resolved:
                            family, origin, _, weight_value, italic = resolved
                            faces[(family, weight_value, italic, origin)] += weight
                        size = next((n.get("sz") for _, n in properties if n.get("sz")), None)
                        if size and size.isdigit() and 0 < int(size) <= 40000:
                            sizes[int(size) / 100] += weight
                family, weight_value, italic, origin = (
                    faces.most_common(1)[0][0] if faces else ("", None, None, "")
                )
                styles[(part, int(identity.get("id")))] = {
                    "family": family,
                    "bold": weight_value == 700 if weight_value is not None else None,
                    "italic": italic,
                    "origin": origin,
                    "color": colors.most_common(1)[0][0] if colors else "",
                    "size": sizes.most_common(1)[0][0] if sizes else 0,
                }
    return styles
