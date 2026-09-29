"""Inspectable background/identity model shared by generation and the corpus audit."""

import logging
import time
import zipfile
from collections import Counter
from io import BytesIO
from xml.etree import ElementTree as ET

from studio._vendor.portable_background_extractor.bgextract.archive_safety import validate_archive
from studio._vendor.portable_background_extractor.bgextract.features import P
from studio._vendor.portable_background_extractor.bgextract.package import active_slide_parts
from studio._vendor.portable_background_extractor.bgextract.resolution import inheritance_parts, resolved_slide
from studio._vendor.portable_background_extractor.bgextract.roles import background_roles, protected_background_regions

logger = logging.getLogger(__name__)


def inspect_template_backgrounds(reference: bytes) -> dict:
    started = time.monotonic()
    with zipfile.ZipFile(BytesIO(reference)) as package:
        validate_archive(package)
        ordered = active_slide_parts(package)
        parts = {name: package.read(name) for name in package.namelist()}
        chains = [inheritance_parts(package, part) for part in ordered]
        for part in ordered:
            parts[part] = ET.tostring(resolved_slide(package, part, flatten=False))
    size = ET.fromstring(parts["ppt/presentation.xml"]).find(f"{{{P}}}sldSz")
    if size is None:
        raise ValueError("Template has no slide dimensions")
    width, height = int(size.get("cx", "0")), int(size.get("cy", "0"))
    roles = background_roles(parts, width, height)
    slides = []
    for index, chain in enumerate(chains, 1):
        objects = []
        show_parent = True
        for part in chain:
            if show_parent:
                objects.extend({**row, "part": part} for row in roles.get(part, []))
            show_parent = show_parent and ET.fromstring(parts[part]).get(
                "showMasterSp", "1"
            ) not in {"0", "false"}
        slides.append(
            {
                "sourceSlideNumber": index,
                "objects": objects,
                "protectedRegions": protected_background_regions(objects, width, height),
            }
        )
    counts = Counter(row["role"] for rows in roles.values() for row in rows)
    logger.info(
        "Template background and corporate identity classified",
        extra={
            "event": "pipeline.template_background.classified",
            "stage": "template-materialization",
            "slide_count": len(slides),
            "part_count": len(roles),
            "object_role_counts": dict(counts),
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return {"schemaVersion": 1, "slides": slides, "parts": roles}

