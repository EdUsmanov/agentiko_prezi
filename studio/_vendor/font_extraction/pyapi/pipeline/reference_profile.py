"""OOXML slide ordering and relationship helpers needed by the font kit."""

from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def _ordered_slides(
    package: zipfile.ZipFile, names: set[str], presentation: ET.Element
) -> list[str]:
    rels = _relationships(package, "ppt/presentation.xml")
    ordered = []
    for slide in presentation.findall(f"{{{P}}}sldIdLst/{{{P}}}sldId"):
        target = rels.get(slide.attrib.get(f"{{{R}}}id", ""))
        if target and target in names:
            ordered.append(target)
    if ordered:
        return ordered
    return sorted(
        (name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
        key=lambda name: int(re.search(r"\d+", name).group()),
    )


def _relationships(package: zipfile.ZipFile, part: str) -> dict[str, str]:
    return {identifier: target for identifier, _, target in _relationship_rows(package, part)}


def _relationship_rows(package: zipfile.ZipFile, part: str) -> list[tuple[str, str, str]]:
    path = PurePosixPath(part)
    rels_part = str(path.parent / "_rels" / f"{path.name}.rels")
    if rels_part not in package.namelist():
        return []
    root = ET.fromstring(package.read(rels_part))
    return [
        (
            relation.attrib.get("Id", ""),
            relation.attrib.get("Type", ""),
            _resolve_part(part, relation.attrib.get("Target", "")),
        )
        for relation in root.findall(f"{{{PR}}}Relationship")
    ]


def _resolve_part(source: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(str(PurePosixPath(source).parent / target))
