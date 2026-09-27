"""Read validated PPTX/POTX without changing the uploaded source package."""

from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from defusedxml import ElementTree as SafeET
from pptx import Presentation
from .security import validate_pptx, presentation_content_type, PPTX_MAIN, POTX_MAIN


def open_presentation(path):
    validate_pptx(path)
    with ZipFile(path) as source:
        if presentation_content_type(source) != POTX_MAIN:
            return Presentation(path)
        # python-pptx does not accept the template main content type. Normalize
        # only that declaration in a private in-memory copy. The source hash,
        # themes, masters, layouts, media and embedded fonts stay untouched.
        types = SafeET.fromstring(source.read("[Content_Types].xml"))
        for node in types:
            if node.get("PartName") == "/ppt/presentation.xml":
                node.set("ContentType", PPTX_MAIN)
        normalized = BytesIO()
        with ZipFile(normalized, "w", ZIP_DEFLATED) as target:
            for entry in source.infolist():
                raw = (
                    SafeET.tostring(types, encoding="utf-8", xml_declaration=True)
                    if entry.filename == "[Content_Types].xml"
                    else source.read(entry)
                )
                target.writestr(entry, raw)
    normalized.seek(0)
    # Saving this object yields a real presentation, not a renamed POTX.
    return Presentation(normalized)
