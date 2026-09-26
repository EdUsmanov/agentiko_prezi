"""Minimal OOXML slide ordering for the portable color extractor."""

from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET
from defusedxml.ElementTree import fromstring as safe_fromstring

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def _relationships(package: zipfile.ZipFile, part: str) -> dict[str, str]:
    path = PurePosixPath(part)
    rels_path = str(path.parent / "_rels" / f"{path.name}.rels")
    if rels_path not in package.namelist():
        return {}
    result = {}
    for relation in safe_fromstring(package.read(rels_path)).findall(f"{{{PR}}}Relationship"):
        if relation.get("TargetMode") == "External":
            continue
        target = relation.get("Target", "")
        resolved = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(str(path.parent / target))
        )
        result[relation.get("Id", "")] = resolved
    return result


def ordered_slides(
    package: zipfile.ZipFile, names: set[str], presentation: ET.Element
) -> list[str]:
    relationships = _relationships(package, "ppt/presentation.xml")
    ordered = [
        target
        for slide in presentation.findall(f"{{{P}}}sldIdLst/{{{P}}}sldId")
        if (target := relationships.get(slide.get(f"{{{R}}}id", ""))) in names
    ]
    if ordered:
        return ordered
    return sorted(
        (name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
        key=lambda name: int(re.search(r"\d+", name).group()),
    )
