"""Fail-closed job admission on known injection signatures, not a full classifier."""

from zipfile import ZipFile
from defusedxml import ElementTree as ET
from .security import scan_text, validate_pptx


class PromptInjectionDetected(ValueError):
    def __init__(self, findings):
        super().__init__(
            "Обнаружена подозрительная инструкция. Обработка материалов заблокирована."
        )
        self.findings = findings

    def public(self):
        return {
            "code": "prompt_injection_detected",
            "message": str(self),
            "count": len(self.findings),
            "sources": sorted({f["source"] for f in self.findings}),
        }


def check_text_fields(**fields):
    findings = [
        finding for source, text in fields.items() for finding in scan_text(text or "", source)[1]
    ]
    if findings:
        raise PromptInjectionDetected(findings)


def check_template(path):
    validate_pptx(path)
    a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    findings = []
    with ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".xml"):
                continue
            root = ET.fromstring(archive.read(name))
            # Join runs inside paragraphs: splitting a command across runs
            # must not bypass the detector. Includes tables, notes and masters.
            for paragraph in root.iter(a + "p"):
                text = "".join(n.text or "" for n in paragraph.iter(a + "t"))
                findings.extend(scan_text(text, "template")[1])
            # Layout/object names can also be included in model inventories.
            for node in root.iter():
                for key in ("name", "descr", "title"):
                    if node.get(key):
                        findings.extend(scan_text(node.get(key), "template")[1])
    if findings:
        raise PromptInjectionDetected(findings)


def check_package(package, source):
    check_text_fields(
        content=package.content.model_dump_json(),
        audience=package.constraints.audience,
        instructions=package.constraints.instructions,
        template_name=package.template.name,
    )
    if package.content.quarantined:
        raise PromptInjectionDetected(package.content.quarantined)
    check_template(source)
