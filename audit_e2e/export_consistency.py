"""Compare independent source anchors across exports without claiming OCR coverage."""

from html.parser import HTMLParser
import unicodedata


class VisibleHTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        style = attrs.get("style", "").replace(" ", "").casefold()
        hidden = (
            tag in {"script", "style", "head", "template"}
            or "hidden" in attrs
            or attrs.get("aria-hidden") == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        if tag not in {"img", "br", "hr", "meta", "link", "input", "source", "wbr"}:
            self.stack.append((tag, hidden or any(value for _, value in self.stack)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not any(hidden for _, hidden in self.stack):
            self.parts.append(data)


def html_text(value):
    parser = VisibleHTMLText()
    parser.feed(value)
    return " ".join(parser.parts)


def _norm(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def assess_export_consistency(bundle):
    points = [
        p
        for p in bundle.get("reference", {}).get("points", [])
        if p.get("required") and p.get("quote")
    ]
    findings = []
    coverage = []
    requirements = bundle.get("reference", {}).get("requirements", [])
    # This contract is only suitable for independently constructed text exports.
    # Real rasterized/outlined outputs must retain an uncertainty path.
    text_contract = any(
        r.get("kind") == "text_export_agreement" and r.get("status") == "gold" for r in requirements
    )
    for name, deck in bundle.get("variants", {}).items():
        formats = {
            "pptx": _norm(" ".join(s.get("text", "") for s in deck.get("slides", []))),
            "pdf": _norm(" ".join(s.get("pdf_visible_text", "") for s in deck.get("slides", []))),
        }
        if deck.get("html"):
            formats["html"] = _norm(deck.get("html_text", ""))
        else:
            findings.append(
                {"variant": name, "status": "inconclusive", "reason": "html_evidence_unavailable"}
            )
        for point in points:
            presence = {fmt: _norm(point["quote"]) in text for fmt, text in formats.items()}
            locations = [
                s["number"]
                for s in deck.get("slides", [])
                if _norm(point["quote"]) in _norm(s.get("text", ""))
            ]
            coverage.append(
                {
                    "variant": name,
                    "point_id": point["id"],
                    "slide_numbers": locations,
                    "literal_presence": presence,
                }
            )
            if any(presence.values()) and not all(presence.values()):
                findings.append(
                    {
                        "variant": name,
                        "point_id": point["id"],
                        "source_quote": point["quote"],
                        "slide_numbers": locations,
                        "literal_presence": presence,
                        "status": "failed" if text_contract else "inconclusive",
                        "reason": "source_anchor_differs_between_exports",
                    }
                )
            elif not any(presence.values()):
                findings.append(
                    {
                        "variant": name,
                        "point_id": point["id"],
                        "status": "inconclusive",
                        "reason": "source_anchor_not_verifiable_by_text_extraction",
                    }
                )
    status = (
        "failed"
        if any(f["status"] == "failed" for f in findings)
        else "inconclusive"
        if findings or not coverage
        else "passed"
    )
    return {
        "status": status,
        "scope": "literal_source_anchor_consistency_not_visual_equivalence",
        "findings": findings,
        "coverage": coverage,
    }
