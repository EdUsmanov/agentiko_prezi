"""Package evidence for background roles; media is identified by its OOXML relationship."""

import posixpath
from collections import defaultdict
from io import BytesIO
from xml.etree import ElementTree as ET

from PIL import Image, UnidentifiedImageError

from studio._vendor.portable_background_extractor.bgextract.resolution import flatten_groups

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
OBJECTS = {"sp", "pic", "cxnSp", "graphicFrame", "grpSp"}


def identity(node: ET.Element) -> ET.Element | None:
    return node.find(f".//{{{P}}}cNvPr")


def shape_id(node: ET.Element) -> str:
    item = identity(node)
    return item.get("id", "") if item is not None else ""


def metadata(node: ET.Element) -> str:
    item = identity(node)
    return (
        " ".join(item.get(key, "") for key in ("name", "descr", "title")).casefold()
        if item is not None
        else ""
    )


def visible_text(node: ET.Element) -> str:
    return " ".join(t.text or "" for t in node.findall(f".//{{{A}}}t")).strip()


def geometry(node: ET.Element, width: int, height: int) -> tuple[float, float, float, float]:
    xfrm = node.find(f".//{{{A}}}xfrm")
    if xfrm is None:
        xfrm = node.find(f"{{{P}}}xfrm")
    if xfrm is None:
        return (0, 0, 0, 0)
    off, ext = xfrm.find(f"{{{A}}}off"), xfrm.find(f"{{{A}}}ext")
    if off is None or ext is None:
        return (0, 0, 0, 0)
    return (
        int(off.get("x", "0")) / max(1, width),
        int(off.get("y", "0")) / max(1, height),
        int(ext.get("cx", "0")) / max(1, width),
        int(ext.get("cy", "0")) / max(1, height),
    )


def relationships(parts: dict[str, bytes], part: str) -> dict[str, str]:
    folder, name = posixpath.split(part)
    payload = parts.get(f"{folder}/_rels/{name}.rels")
    if not payload:
        return {}
    return {
        rel.get("Id", ""): (
            rel.get("Target", "").lstrip("/")
            if rel.get("Target", "").startswith("/")
            else posixpath.normpath(posixpath.join(folder, rel.get("Target", "")))
        )
        for rel in ET.fromstring(payload)
        if rel.get("TargetMode") != "External"
    }


def image_features(payload: bytes) -> dict:
    try:
        with Image.open(BytesIO(payload)) as source:
            # A small thumbnail is sufficient for alpha/palette evidence, never for export.
            image = source.convert("RGBA")
            image.thumbnail((96, 96))
            pixels = list(image.get_flattened_data())
            opaque = [p for p in pixels if p[3] > 200]
            colors = {(p[0] // 16, p[1] // 16, p[2] // 16) for p in opaque}
            transparent = sum(p[3] < 250 for p in pixels) / max(1, len(pixels))
            return {"alpha": round(transparent, 3), "colors": len(colors)}
    except (UnidentifiedImageError, OSError, ValueError):
        # Vector/unsupported assets remain unknown; decoding failure never licenses deletion.
        return {}


def collect_objects(parts: dict[str, bytes], width: int, height: int) -> dict[str, list[dict]]:
    result = {}
    media_cache: dict[str, dict] = {}
    occurrences: dict[tuple, set[str]] = defaultdict(set)
    for part, payload in parts.items():
        if not part.endswith(".xml") or not part.startswith(
            ("ppt/slides/", "ppt/slideLayouts/", "ppt/slideMasters/")
        ):
            continue
        root = ET.fromstring(payload)
        tree = root.find(f".//{{{P}}}spTree")
        if tree is None:
            continue
        groups = _group_members(tree)
        flatten_groups(tree)
        rels = relationships(parts, part)
        rows = []
        for node in tree.iter():
            kind = node.tag.rsplit("}", 1)[-1]
            if kind not in OBJECTS:
                continue
            sid = shape_id(node)
            box = geometry(node, width, height)
            ph = node.find(f".//{{{P}}}ph") if kind != "grpSp" else None
            blip = node.find(f".//{{{A}}}blip") if kind != "grpSp" else None
            embed = (
                next(
                    (
                        item.get(f"{{{R}}}embed")
                        for item in blip.iter()
                        if item.get(f"{{{R}}}embed")
                    ),
                    "",
                )
                if blip is not None
                else ""
            )
            asset = rels.get(embed, "")
            if asset and asset not in media_cache:
                media_cache[asset] = image_features(parts[asset]) if asset in parts else {}
            text = visible_text(node) if kind != "grpSp" else ""
            preset = node.find(f"{{{P}}}spPr/{{{A}}}prstGeom")
            signature = (asset or text, *(round(value, 2) for value in box))
            if asset or text:
                occurrences[signature].add(part)
            rows.append(
                {
                    "preset": preset.get("prst", "") if preset is not None else "",
                    "outlineOnly": (
                        node.find(f"{{{P}}}spPr/{{{A}}}noFill") is not None
                        and node.find(f"{{{P}}}spPr/{{{A}}}ln") is not None
                    ),
                    "id": sid,
                    "type": kind,
                    "metadata": metadata(node),
                    "text": text,
                    "box": box,
                    "placeholder": dict(ph.attrib) if ph is not None else None,
                    "asset": asset,
                    "image": media_cache.get(asset, {}),
                    "groups": groups.get(sid, []),
                    "signature": signature,
                    "connections": [
                        n.get("id")
                        for n in node.iter()
                        if n.tag in {f"{{{A}}}stCxn", f"{{{A}}}endCxn"}
                    ],
                }
            )
        result[part] = rows
    for rows in result.values():
        for row in rows:
            # Multiple slots on one slide are not evidence of recurring corporate identity.
            row["recurring"] = len(occurrences[row.pop("signature")])
    return result


def _group_members(tree: ET.Element) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = defaultdict(list)
    for group in tree.findall(f".//{{{P}}}grpSp"):
        info = {"id": shape_id(group), "metadata": metadata(group), "text": visible_text(group)}
        for child in group.iter():
            if child is not group and child.tag.rsplit("}", 1)[-1] in OBJECTS:
                result[shape_id(child)].append(info)
    return dict(result)

