"""Read and write self-contained background-only PowerPoint packages."""

from __future__ import annotations

import posixpath
import zipfile
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from studio._vendor.portable_background_extractor.bgextract.archive_safety import validate_archive
from studio._vendor.portable_background_extractor.bgextract.raster_cleanup import clean_identity_png
from studio._vendor.portable_background_extractor.bgextract.raster_regions import reconstruct_regions
from studio._vendor.portable_background_extractor.bgextract.resolution import flatten_groups, resolved_slide
from studio._vendor.portable_background_extractor.bgextract.roles import apply_background_roles

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
SLIDE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
STRUCTURAL_RELATIONS = ("/slideLayout", "/slideMaster", "/theme")
NON_BACKGROUND_RELATIONS = ("/notesSlide", "/notesMaster", "/comments", "/commentAuthors")

ET.register_namespace("p", P)
ET.register_namespace("a", "http://schemas.openxmlformats.org/drawingml/2006/main")
ET.register_namespace("r", R)


def _rels_part(part: str) -> str:
    if not part:
        return "_rels/.rels"
    path = PurePosixPath(part)
    return str(path.parent / "_rels" / f"{path.name}.rels")


def _resolve(source: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source), target))


def active_slide_parts(package: zipfile.ZipFile) -> list[str]:
    """Use presentation order rather than numeric XML filenames."""
    names = set(package.namelist())
    presentation = ET.fromstring(package.read("ppt/presentation.xml"))
    relations = ET.fromstring(package.read("ppt/_rels/presentation.xml.rels"))
    targets = {
        rel.get("Id", ""): _resolve("ppt/presentation.xml", rel.get("Target", ""))
        for rel in relations
        if rel.get("Type") == SLIDE_REL and rel.get("TargetMode") != "External"
    }
    return [
        targets[node.get(f"{{{R}}}id", "")]
        for node in presentation.findall(f"{{{P}}}sldIdLst/{{{P}}}sldId")
        if targets.get(node.get(f"{{{R}}}id", "")) in names
    ]


def _xml(root: ET.Element) -> bytes:
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    if namespace in {PR, CT}:
        ET.register_namespace("", namespace)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _is_drawing_part(name: str) -> bool:
    return name.endswith(".xml") and name.startswith(
        ("ppt/slides/", "ppt/slideLayouts/", "ppt/slideMasters/")
    )


def _filter_relationships(parts: dict[str, bytes], source: str) -> None:
    rels_part = _rels_part(source)
    if rels_part not in parts:
        return
    drawing = ET.fromstring(parts[source])
    used = {
        value
        for node in drawing.iter()
        for attribute, value in node.attrib.items()
        if attribute.startswith(f"{{{R}}}")
    }
    relations = ET.fromstring(parts[rels_part])
    for relation in list(relations):
        kind = relation.get("Type", "")
        if kind.endswith(NON_BACKGROUND_RELATIONS) or (
            not kind.endswith(STRUCTURAL_RELATIONS) and relation.get("Id") not in used
        ):
            relations.remove(relation)
    parts[rels_part] = _xml(relations)


def _reachable_parts(parts: dict[str, bytes]) -> set[str]:
    """Discard orphaned media, notes and content parts after relationship cleanup."""
    pending = ["ppt/presentation.xml"]
    if "_rels/.rels" in parts:
        pending.append("")
    visited: set[str] = set()
    keep = {"[Content_Types].xml"}
    while pending:
        source = pending.pop()
        if source in visited:
            continue
        visited.add(source)
        if source:
            keep.add(source)
        rels_part = _rels_part(source)
        if rels_part not in parts:
            continue
        keep.add(rels_part)
        for relation in ET.fromstring(parts[rels_part]):
            if relation.get("TargetMode") == "External":
                continue
            target = _resolve(source, relation.get("Target", ""))
            if target in parts:
                pending.append(target)
    return keep


def extract_background_pptx(reference: bytes, model: dict) -> bytes:
    """Keep exact OOXML backgrounds and design artwork, removing example content."""
    with zipfile.ZipFile(BytesIO(reference)) as package:
        validate_archive(package)
        parts = {info.filename: package.read(info.filename) for info in package.infolist()}
        slides = active_slide_parts(package)
        if not slides:
            raise ValueError("Presentation contains no active slides")
        for part in slides:
            parts[part] = _xml(resolved_slide(package, part, flatten=False))

    for part, roles in model["parts"].items():
        if not _is_drawing_part(part) or part not in parts:
            continue
        root = ET.fromstring(parts[part])
        tree = root.find(f".//{{{P}}}spTree")
        if tree is not None:
            flatten_groups(tree)
        apply_background_roles(root, roles)
        parts[part] = _xml(root)
        _filter_relationships(parts, part)
    _filter_relationships(parts, "ppt/presentation.xml")

    content_types = ET.fromstring(parts["[Content_Types].xml"])
    for entry in content_types.findall(f"{{{CT}}}Override"):
        if entry.get("PartName") == "/ppt/presentation.xml":
            entry.set(
                "ContentType",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
            )
    keep = _reachable_parts(parts)
    for asset, regions in model.get("rasterRegions", {}).items():
        if asset in keep:
            parts[asset] = reconstruct_regions(parts[asset], regions)
    identity_pngs = {
        item["asset"]
        for roles in model["parts"].values()
        for item in roles
        if item["action"] == "keep"
        and item["role"] == "identity"
        and item["asset"].lower().endswith(".png")
    }
    for asset in sorted(identity_pngs & keep):
        cleaned, phrases = clean_identity_png(parts[asset])
        if phrases:
            parts[asset] = cleaned
            model.setdefault("rasterCleanup", []).append(
                {"asset": asset, "removedSamplePhrases": phrases}
            )
    for entry in list(content_types):
        part = entry.get("PartName", "").lstrip("/")
        if part and part not in keep:
            content_types.remove(entry)
    parts["[Content_Types].xml"] = _xml(content_types)

    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, payload in parts.items():
            if name in keep:
                archive.writestr(name, payload)
    return output.getvalue()

