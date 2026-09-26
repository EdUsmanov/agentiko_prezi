"""Resolve slide inheritance before analysis or deterministic OOXML editing."""

import posixpath
import zipfile
from copy import deepcopy
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET
from defusedxml.ElementTree import fromstring as safe_fromstring

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def related_part(package: zipfile.ZipFile, part: str, suffix: str) -> str | None:
    path = PurePosixPath(part)
    rels = str(path.parent / "_rels" / f"{path.name}.rels")
    if rels not in package.namelist():
        return None
    for rel in safe_fromstring(package.read(rels)):
        if rel.get("Type", "").endswith(suffix) and rel.get("TargetMode") != "External":
            target = rel.get("Target", "")
            return (
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.normpath(str(path.parent / target))
            )
    return None


def inheritance_parts(package: zipfile.ZipFile, part: str) -> list[str]:
    result = [part]
    for suffix in ("/slideLayout", "/slideMaster"):
        parent = related_part(package, result[-1], suffix)
        if parent and parent in package.namelist():
            result.append(parent)
    return result


def effective_theme(
    package: zipfile.ZipFile, part: str
) -> tuple[ET.Element | None, dict[str, str]]:
    chain = inheritance_parts(package, part)
    theme = None
    color_map = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
    for source in reversed(chain):
        root = safe_fromstring(package.read(source))
        for path in (f"{{{P}}}clrMap", f"{{{P}}}clrMapOvr/{{{A}}}overrideClrMapping"):
            mapping = root.find(path)
            if mapping is not None:
                color_map.update(mapping.attrib)
        target = related_part(package, source, "/theme")
        if target and target in package.namelist():
            theme = safe_fromstring(package.read(target))
    return theme, color_map


def _placeholder(shape: ET.Element) -> ET.Element | None:
    return shape.find(f".//{{{P}}}ph")


def _match_placeholder(shape: ET.Element, parent: ET.Element, master: bool) -> ET.Element | None:
    ph = _placeholder(shape)
    if ph is None:
        return None
    candidates = [
        (node, _placeholder(node))
        for node in parent.findall(f".//{{{P}}}sp") + parent.findall(f".//{{{P}}}pic")
    ]
    if not master:
        for node, candidate in candidates:
            if candidate is not None and candidate.get("idx", "0") == ph.get("idx", "0"):
                return node
    kind = ph.get("type", "body")
    kind = "title" if kind == "ctrTitle" else kind
    for node, candidate in candidates:
        if candidate is not None and candidate.get("type", "body") == kind:
            return node
    return None


def resolved_slide(package: zipfile.ZipFile, part: str, *, flatten: bool = True) -> ET.Element:
    """Materialize placeholder geometry and inherited run defaults without sample text."""
    chain = inheritance_parts(package, part)
    roots = [safe_fromstring(package.read(name)) for name in chain]
    root = roots[0]
    for shape in root.findall(f".//{{{P}}}sp") + root.findall(f".//{{{P}}}pic"):
        ancestors = []
        previous = shape
        for index, parent in enumerate(roots[1:]):
            matched = _match_placeholder(previous, parent, index > 0)
            if matched is not None:
                ancestors.append(matched)
                previous = matched
        properties = shape.find(f"{{{P}}}spPr")
        if properties is None:
            properties = ET.SubElement(shape, f"{{{P}}}spPr")
        for ancestor in ancestors:
            source = ancestor.find(f"{{{P}}}spPr/{{{A}}}xfrm")
            if source is not None:
                transform = properties.find(f"{{{A}}}xfrm")
                if transform is None:
                    properties.insert(0, deepcopy(source))
                else:
                    for child in source:
                        if transform.find(child.tag) is None:
                            transform.append(deepcopy(child))
        body = shape.find(f"{{{P}}}txBody")
        if body is not None:
            _resolve_text(body, shape, ancestors, roots, package)
    if flatten:
        tree = root.find(f".//{{{P}}}spTree")
        if tree is not None:
            flatten_groups(tree)
    return root


def _resolve_text(
    body: ET.Element,
    shape: ET.Element,
    ancestors: list[ET.Element],
    roots: list[ET.Element],
    package: zipfile.ZipFile,
) -> None:
    ph = _placeholder(shape)
    kind = ph.get("type", "body") if ph is not None else "other"
    style = (
        "titleStyle"
        if kind in {"title", "ctrTitle"}
        else "bodyStyle"
        if ph is not None
        else "otherStyle"
    )
    for paragraph in body.findall(f"{{{A}}}p"):
        ppr = paragraph.find(f"{{{A}}}pPr")
        level = int(ppr.get("lvl", "0")) + 1 if ppr is not None else 1
        sources = [ppr.find(f"{{{A}}}defRPr") if ppr is not None else None]
        for ancestor in [shape, *ancestors]:
            tx = ancestor.find(f"{{{P}}}txBody")
            if tx is not None:
                sources.extend(
                    [
                        tx.find(f"{{{A}}}p/{{{A}}}pPr/{{{A}}}defRPr")
                        if ancestor is not shape
                        else None,
                        tx.find(f"{{{A}}}lstStyle/{{{A}}}lvl{level}pPr/{{{A}}}defRPr"),
                    ]
                )
        if len(roots) > 2:
            sources.append(
                roots[-1].find(f"{{{P}}}txStyles/{{{P}}}{style}/{{{A}}}lvl{level}pPr/{{{A}}}defRPr")
            )
        presentation = safe_fromstring(package.read("ppt/presentation.xml"))
        sources.append(
            presentation.find(f"{{{P}}}defaultTextStyle/{{{A}}}lvl{level}pPr/{{{A}}}defRPr")
        )
        defaults = ET.Element(f"{{{A}}}rPr")
        for source in reversed([node for node in sources if node is not None]):
            defaults.attrib.update(source.attrib)
            for child in source:
                existing = defaults.find(child.tag)
                if existing is not None:
                    defaults.remove(existing)
                defaults.append(deepcopy(child))
        for run in list(paragraph):
            if run.tag not in {f"{{{A}}}r", f"{{{A}}}fld"}:
                continue
            properties = run.find(f"{{{A}}}rPr")
            if properties is None:
                properties = ET.Element(f"{{{A}}}rPr")
                run.insert(0, properties)
            for key, value in defaults.attrib.items():
                if key not in properties.attrib:
                    properties.set(key, value)
            for child in defaults:
                if properties.find(child.tag) is None:
                    properties.append(deepcopy(child))
            # CT_TextCharacterProperties is an ordered OOXML sequence, not an unordered map.
            order = (
                "ln",
                "noFill",
                "solidFill",
                "gradFill",
                "blipFill",
                "pattFill",
                "grpFill",
                "effectLst",
                "effectDag",
                "highlight",
                "uLnTx",
                "uLn",
                "uFillTx",
                "uFill",
                "latin",
                "ea",
                "cs",
                "sym",
                "hlinkClick",
                "hlinkMouseOver",
                "rtl",
                "extLst",
            )
            properties[:] = sorted(
                properties,
                key=lambda node: (
                    order.index(node.tag.rsplit("}", 1)[-1])
                    if node.tag.rsplit("}", 1)[-1] in order
                    else len(order)
                ),
            )


def has_complex_groups(root: ET.Element) -> bool:
    return any(
        any(transform.get(key, "0") not in {"0", "false"} for key in ("rot", "flipH", "flipV"))
        for transform in root.findall(f".//{{{P}}}grpSp/{{{P}}}grpSpPr/{{{A}}}xfrm")
    )


def flatten_groups(tree: ET.Element) -> None:
    """Bake axis-aligned group scale/translation into children, preserving paint order.

    Rotated/reflected groups stay intact and are excluded from native materialization;
    the renderer remains authoritative for these complex backgrounds.
    """
    for group in list(tree):
        if group.tag != f"{{{P}}}grpSp":
            continue
        transform = group.find(f"{{{P}}}grpSpPr/{{{A}}}xfrm")
        if transform is None or any(
            transform.get(key, "0") not in {"0", "false"} for key in ("rot", "flipH", "flipV")
        ):
            continue
        if has_complex_groups(group):
            continue
        flatten_groups(group)
        off, ext = transform.find(f"{{{A}}}off"), transform.find(f"{{{A}}}ext")
        child_off, child_ext = transform.find(f"{{{A}}}chOff"), transform.find(f"{{{A}}}chExt")
        if any(node is None for node in (off, ext, child_off, child_ext)):
            continue
        sx = int(ext.get("cx", "0")) / max(1, int(child_ext.get("cx", "1")))
        sy = int(ext.get("cy", "0")) / max(1, int(child_ext.get("cy", "1")))
        position = list(tree).index(group)
        for node in list(group):
            if node.tag in {f"{{{P}}}nvGrpSpPr", f"{{{P}}}grpSpPr"}:
                continue
            xfrm = node.find(f"{{{P}}}spPr/{{{A}}}xfrm")
            if xfrm is None:
                xfrm = node.find(f"{{{P}}}xfrm")
            if xfrm is not None:
                origin, size = xfrm.find(f"{{{A}}}off"), xfrm.find(f"{{{A}}}ext")
                if origin is not None and size is not None:
                    for axis, extent, scale in (("x", "cx", sx), ("y", "cy", sy)):
                        origin.set(
                            axis,
                            str(
                                round(
                                    int(off.get(axis, "0"))
                                    + (int(origin.get(axis, "0")) - int(child_off.get(axis, "0")))
                                    * scale
                                )
                            ),
                        )
                        size.set(extent, str(round(int(size.get(extent, "0")) * scale)))
            for properties in node.findall(".//*[@sz]"):
                if properties.tag in {f"{{{A}}}rPr", f"{{{A}}}defRPr", f"{{{A}}}endParaRPr"}:
                    properties.set("sz", str(round(int(properties.get("sz")) * sy)))
            for column in node.findall(f".//{{{A}}}tblGrid/{{{A}}}gridCol"):
                column.set("w", str(round(int(column.get("w", "0")) * sx)))
            for row in node.findall(f".//{{{A}}}tbl/{{{A}}}tr"):
                row.set("h", str(round(int(row.get("h", "0")) * sy)))
            for properties in node.findall(f".//{{{A}}}tcPr") + node.findall(f".//{{{A}}}bodyPr"):
                for key, scale in (
                    ("marL", sx),
                    ("marR", sx),
                    ("marT", sy),
                    ("marB", sy),
                    ("lIns", sx),
                    ("rIns", sx),
                    ("tIns", sy),
                    ("bIns", sy),
                ):
                    if properties.get(key, "").isdigit():
                        properties.set(key, str(round(int(properties.get(key)) * scale)))
            tree.insert(position, node)
            position += 1
        tree.remove(group)
