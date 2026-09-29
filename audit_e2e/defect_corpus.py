"""Offline native exports for the independent BAD-EXPORT defect corpus."""

import argparse
from base64 import b64encode
from copy import deepcopy
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import shutil
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

from .evidence import audit_bundle, build_bundle
from .reporting import write_json
from studio.composition.office import to_pdf

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "audit_e2e" / "fixtures" / "defect_cases.json"
VARIANTS = ("executive", "analytical", "story")
THEME = {"background": "173550", "accent": "68C5C0", "text": "FFFFFF"}
BODY_COLOR = "DCE8EF"

_COMPOSITIONS = {
    "executive": {
        "id": "decision-first-cards-v1",
        "organization": "decision first; sample size, duration and operating context together; then guardrails",
        "groups": [["decision"], ["sample", "duration", "context"], ["compliance", "caveat", "proposal"]],
    },
    "analytical": {
        "id": "measure-first-evidence-board-v1",
        "organization": "measure, sample and limitation row; context and proposal row; then decision and gate",
        "groups": [["duration", "sample", "caveat"], ["context", "proposal"], ["decision", "compliance"]],
    },
    "story": {
        "id": "chronology-to-implication-v1",
        "organization": "context and duration beside sample; caveat and proposal; then gate and decision",
        "groups": [["context", "duration", "sample"], ["caveat", "proposal"], ["compliance", "decision"]],
    },
}
_LEGACY_TARGET = {
    "omission": "omission",
    "number": "number",
    "table_binding": "table_binding",
    "units": "units",
    "hidden_text": "hidden_text",
    "duplicates": "duplicates",
    "readability": "readability",
    "template_fidelity": "template_fidelity",
    "image_missing": "omission",
    "pptx_pdf_mismatch": "export_consistency",
    "pptx_html_mismatch": "export_consistency",
}
_LEGACY_PROXY = {
    "omission": "export_consistency",
    "clipping": "hidden_text",
    "image_distorted": "omission",
}


def _sha(data):
    return sha256(data).hexdigest()


def _read_registry():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or len(data.get("categories", [])) < 19:
        raise ValueError("Defect-corpus fixture registry is incomplete or unsupported")
    for source in data.get("sources", []):
        if any(fact["quote"] not in fact["statement"] for fact in source["facts"]):
            raise ValueError(f"Unanchored independent source point: {source['id']}")
    return data


def _font_color(run, value):
    run.font.color.rgb = RGBColor.from_string(value)


def _fill(shape, value):
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(value)
    shape.line.fill.background()


def _slide_background(slide, value):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string(value)


def _add_text(slide, name, value, x, y, width, height, *, color, size, bold=False, align=None):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(width), Inches(height))
    shape.name = name
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = Inches(0.02)
    frame.margin_right = Inches(0.02)
    frame.margin_top = Inches(0.01)
    frame.margin_bottom = Inches(0.01)
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    paragraph = frame.paragraphs[0]
    paragraph.text = value
    if align is not None:
        paragraph.alignment = align
    for run in paragraph.runs:
        run.font.name = "Arial"
        run.font.size = Pt(size)
        run.font.bold = bold
        _font_color(run, color)
    return shape


def _add_title(slide, value, *, foreground):
    _add_text(
        slide,
        "Section title",
        value,
        0.62,
        0.30,
        12.0,
        0.65,
        color=foreground,
        size=25,
        bold=True,
    )


def _add_card(slide, fact, value, box, *, accent, foreground, size=17, defect=None):
    x, y, width, height = box
    bg = "FFFFFF" if foreground != "FFFFFF" else "21445E"
    card = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(x),
        Inches(y),
        Inches(width),
        Inches(height),
    )
    card.name = f"Card {fact['id']}"
    _fill(card, bg)
    label_color = accent if bg != "FFFFFF" else "287B79"
    _add_text(
        slide,
        f"Label {fact['id']}",
        fact["id"].replace("_", " ").upper(),
        x + 0.16,
        y + 0.10,
        width - 0.32,
        0.25,
        color=label_color,
        size=9,
        bold=True,
    )
    body_color = bg if defect == "lowcontrast" else foreground if bg != "FFFFFF" else "173550"
    font_size = 5 if defect == "readability" else size
    body_x, body_y = x + 0.16, y + 0.43
    body_width, body_height = width - 0.32, max(0.38, height - 0.55)
    if defect == "clipping" and fact["id"] == "compliance":
        body_x, body_width = 12.28, 3.8
    if defect == "overlap" and fact["id"] == "compliance":
        body_x, body_y, body_width, body_height = x + 0.16, y + 0.43, 5.0, 1.4
    body = _add_text(
        slide,
        f"Fact {fact['id']}",
        value,
        body_x,
        body_y,
        body_width,
        body_height,
        color=body_color,
        size=font_size,
    )
    return body


def _add_cover(prs, source):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _slide_background(slide, THEME["background"])
    strip = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(0.22), Inches(7.5)
    )
    _fill(strip, THEME["accent"])
    _add_text(
        slide,
        "Presentation title",
        source["title"],
        0.82,
        1.55,
        10.7,
        1.25,
        color="FFFFFF",
        size=36,
        bold=True,
    )
    _add_text(
        slide,
        "Source template label",
        "NORTH STAR RESEARCH  /  FIELD NOTES",
        0.84,
        6.32,
        10.4,
        0.46,
        color=THEME["accent"],
        size=12,
        bold=True,
    )
    return slide


def _source_text(source):
    lines = [f"# {source['title']}", "", *(fact["statement"] for fact in source["facts"]), "", "Source table:"]
    headers, rows = source["table"]["headers"], source["table"]["rows"]
    lines.extend(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"])
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    lines.extend(["", "Source figure: ![sample](sample-figure.png)"])
    return "\n".join(lines) + "\n"


def _make_template(path):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    _add_cover(prs, {"title": "Source visual system"})
    prs.save(path)
    return path


def _make_figure(source, path):
    figure = source["figure"]
    image = Image.new("RGB", (640, 360), "#F3F6FA")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 640, 65), fill="#173550")
    font = ImageFont.load_default()
    draw.text((24, 22), figure["title"], font=font, fill="white")
    draw.rounded_rectangle((20, 90, 620, 338), radius=14, fill="white", outline="#C8D5E2", width=3)
    draw.text((58, 118), figure["value"], font=font, fill="#173550", stroke_width=1)
    draw.text((58, 176), figure["unit"], font=font, fill="#486176")
    draw.line((58, 214, 580, 214), fill="#D6E0E9", width=3)
    draw.text((58, 251), figure["label"], font=font, fill="#486176")
    image.save(path, format="PNG")
    return path


def _text_for(source, mode):
    return {
        fact["id"]: fact["paraphrase"] if mode == "paraphrase" else fact["statement"]
        for fact in source["facts"]
    }


def _wrong_text(source, category, texts):
    result = dict(texts)
    target = next((item["target"] for item in _read_registry()["categories"] if item["id"] == category), None)
    if category in {"pptx_pdf_mismatch", "pptx_html_mismatch"}:
        target = "duration"
    if category == "omission":
        result.pop("compliance", None)
    elif category == "hidden_text":
        result.pop("compliance", None)
    elif category == "number":
        result[target] = re.sub(r"\b(\d+)\b", lambda match: str(int(match.group(1)) + 1), result[target], count=1)
    elif category == "negation":
        result[target] = re.sub(r"\bdoes not prove\b", "proves", result[target], flags=re.I)
        result[target] = re.sub(r"\bdo not prove\b", "prove", result[target], flags=re.I)
    elif category == "condition":
        result[target] = re.sub(r"\s+if\s+.+?\.?$", ".", result[target], flags=re.I)
    elif category == "provenance":
        result[target] = re.sub(r"^(?:Model proposal|Working hypothesis):\s*", "", result[target], flags=re.I)
        result[target] = re.sub(r"\bmay\b|\bmight\b|\bcould\b", "will", result[target], flags=re.I)
    return result


def _add_data_table(slide, source, defect):
    headers = list(source["table"]["headers"])
    rows = [list(row) for row in source["table"]["rows"]]
    if defect == "table_binding":
        # Keep labels in place while exchanging row values, so the label/value
        # associations are wrong instead of merely presented in a new order.
        if len(rows) > 1:
            for column in range(1, min(len(rows[0]), len(rows[1]))):
                rows[0][column], rows[1][column] = rows[1][column], rows[0][column]
    if defect == "units":
        headers[-1] = "Minutes" if headers[-1] != "Turbidity (NTU)" else "Turbidity (mm)"
    shape = slide.shapes.add_table(
        len(rows) + 1, len(headers), Inches(0.62), Inches(1.56), Inches(5.75), Inches(2.2)
    )
    shape.name = "Source data table"
    table = shape.table
    for column, value in enumerate(headers):
        table.cell(0, column).text = value
    for row_index, row in enumerate(rows, 1):
        for column, value in enumerate(row):
            table.cell(row_index, column).text = value
    for row_index, row in enumerate(table.rows):
        row.height = Inches(0.46)
        for cell in row.cells:
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor.from_string("68C5C0" if row_index == 0 else "FFFFFF")
            for paragraph in cell.text_frame.paragraphs:
                paragraph.alignment = PP_ALIGN.CENTER
                for run in paragraph.runs:
                    run.font.name = "Arial"
                    run.font.size = Pt(11)
                    run.font.bold = row_index == 0
                    _font_color(run, "173550")
    return shape


def _add_chart(slide, source, defect):
    chart_info = source["chart"]
    values = list(chart_info["values"])
    if defect == "chart_value":
        values[0] += 1
    data = CategoryChartData()
    data.categories = chart_info["categories"]
    data.add_series(chart_info["series"], values)
    chart_shape = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(6.7),
        Inches(1.30),
        Inches(6.0),
        Inches(3.72),
        data,
    )
    chart_shape.name = "Source comparison chart"
    chart = chart_shape.chart
    chart.has_title = True
    chart.chart_title.text_frame.text = chart_info["title"]
    chart.has_legend = False
    plot = chart.plots[0]
    plot.has_data_labels = True
    plot.data_labels.show_value = True
    plot.data_labels.font.size = Pt(11)
    chart.category_axis.has_title = True
    chart.category_axis.axis_title.text_frame.text = (
        "Location" if defect == "chart_axis" else chart_info["category_axis"]
    )
    chart.value_axis.has_title = True
    chart.value_axis.axis_title.text_frame.text = (
        "Minutes" if defect == "chart_axis" else chart_info["value_axis"]
    )
    return chart_shape


def _image_bytes(path, *, distorted=False):
    data = Path(path).read_bytes()
    if not distorted:
        return data
    with Image.open(BytesIO(data)) as source:
        changed = source.resize((640, 92), Image.Resampling.BICUBIC)
        target = BytesIO()
        changed.save(target, format="PNG")
        return target.getvalue()


def _add_figure(slide, source_image, *, defect=None):
    data = _image_bytes(source_image, distorted=defect == "image_distorted")
    return slide.shapes.add_picture(
        BytesIO(data),
        Inches(0.62),
        Inches(4.05),
        width=Inches(4.45),
        height=Inches(2.50),
    )


def _add_narrative_slide(prs, variant, source, texts, defect):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = THEME["background"]
    fg = THEME["text"]
    accent = THEME["accent"] if defect != "template_fidelity" else "00E5FF"
    if defect == "template_fidelity":
        bg, fg, accent = "E90074", "FFFFFF", "00E5FF"
    _slide_background(slide, bg)
    title = {"executive": "Decision first", "analytical": "Read the measures", "story": "Where it begins"}[variant]
    _add_title(slide, title, foreground=fg)
    facts = {fact["id"]: fact for fact in source["facts"]}
    if variant == "executive":
        plans = [
            ("decision", (0.62, 1.37, 12.08, 2.30), 19),
            ("sample", (0.62, 3.98, 3.55, 2.65), 18),
            ("duration", (4.42, 3.98, 3.55, 2.65), 18),
            ("context", (8.22, 3.98, 4.48, 2.65), 17),
        ]
    elif variant == "analytical":
        plans = [
            ("duration", (0.62, 1.40, 3.85, 2.10), 16),
            ("sample", (4.73, 1.40, 3.85, 2.10), 16),
            ("caveat", (8.84, 1.40, 3.83, 2.10), 15),
            ("context", (0.62, 3.76, 6.00, 2.35), 16),
            ("proposal", (6.86, 3.76, 5.81, 2.35), 16),
        ]
    else:
        plans = [
            ("context", (0.62, 1.42, 5.73, 1.72), 18),
            ("duration", (0.62, 3.40, 5.73, 1.55), 18),
            ("sample", (6.66, 1.42, 6.00, 3.53), 22),
        ]
    for fact_id, box, size in plans:
        if fact_id not in texts:
            continue
        _add_card(
            slide,
            facts[fact_id],
            texts[fact_id],
            box,
            accent=accent,
            foreground=fg,
            size=size,
            defect=defect,
        )
    if defect == "foreign_template_content":
        _add_text(
            slide,
            "Unrelated template label",
            "ORBITAL CAPITAL  /  QUARTERLY INVESTOR BRIEF",
            7.0,
            6.55,
            5.6,
            0.34,
            color=accent,
            size=9,
            bold=True,
        )
    return slide


def _add_evidence_slide(prs, source, source_image, defect):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = "E90074" if defect == "template_fidelity" else THEME["background"]
    _slide_background(slide, bg)
    title_color = "FFFFFF"
    _add_title(slide, "Measured evidence", foreground=title_color)
    _add_data_table(slide, source, defect)
    _add_chart(slide, source, defect)
    if defect != "image_missing":
        _add_figure(slide, source_image, defect=defect)
    return slide


def _add_conclusion_slide(prs, variant, source, texts, defect):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    if defect == "backgroundloss":
        bg, fg = "FFFFFF", "173550"
    elif defect == "template_fidelity":
        bg, fg = "E90074", "FFFFFF"
    else:
        bg, fg = THEME["background"], THEME["text"]
    accent = "00E5FF" if defect == "template_fidelity" else THEME["accent"]
    _slide_background(slide, bg)
    title = {
        "executive": "What must hold",
        "analytical": "Decision conditions",
        "story": "What the record permits",
    }[variant]
    _add_title(slide, title, foreground=fg)
    facts = {fact["id"]: fact for fact in source["facts"]}
    if variant == "executive":
        plans = [
            ("compliance", (0.62, 1.45, 3.70, 4.70), 17),
            ("caveat", (4.55, 1.45, 3.70, 4.70), 16),
            ("proposal", (8.47, 1.45, 4.20, 4.70), 17),
        ]
    elif variant == "analytical":
        plans = [
            ("decision", (0.62, 1.55, 7.75, 3.30), 18),
            ("compliance", (8.65, 1.55, 4.02, 3.30), 16),
        ]
    else:
        plans = [
            ("caveat", (0.62, 1.45, 5.80, 2.05), 17),
            ("proposal", (6.72, 1.45, 5.95, 2.05), 16),
            ("compliance", (0.62, 3.82, 3.70, 2.25), 15),
            ("decision", (4.55, 3.82, 8.12, 2.25), 17),
        ]
    if defect == "overlap":
        if variant == "analytical":
            plans = [
                ("decision", (0.62, 1.55, 7.75, 3.30), 18),
                ("compliance", (6.00, 1.55, 6.67, 3.30), 16),
            ]
    for fact_id, box, size in plans:
        if fact_id not in texts:
            continue
        _add_card(
            slide,
            facts[fact_id],
            texts[fact_id],
            box,
            accent=accent,
            foreground=fg,
            size=size,
            defect=defect,
        )
    if defect == "hidden_text" and "compliance" not in texts:
        hidden = slide.shapes.add_textbox(Inches(14), Inches(6.4), Inches(4), Inches(0.6))
        hidden.name = "Hidden source point"
        hidden.text_frame.text = next(f["quote"] for f in source["facts"] if f["id"] == "compliance")
        hidden._element.xpath(".//p:cNvPr")[0].set("hidden", "1")
        slide.notes_slide.notes_text_frame.text = hidden.text_frame.text
    if defect == "foreign_template_content":
        _add_text(
            slide,
            "Unrelated template label",
            "ORBITAL CAPITAL  /  QUARTERLY INVESTOR BRIEF",
            0.62,
            6.55,
            6.0,
            0.32,
            color=accent,
            size=9,
            bold=True,
        )
    return slide


def _make_deck(source, source_image, variant, category, mode, *, pdf_export=False, style=None):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    defect = category if mode == "defect" else None
    texts = _text_for(source, mode)
    if defect:
        texts = _wrong_text(source, defect, texts)
    if pdf_export:
        texts = _wrong_text(source, "number", _text_for(source, "clean"))
        defect = None
    if style:
        # Cosmetics controls keep the same card order and geometry across variants.
        theme = style
    else:
        theme = THEME
    _add_cover(prs, source)
    if style:
        _add_narrative_slide(prs, "executive", source, texts, None)
        _add_evidence_slide(prs, source, source_image, None)
        _add_conclusion_slide(prs, "executive", source, texts, None)
        _restyle_cosmetic(prs, theme)
        return prs
    if defect == "template_fidelity":
        _restyle_cover(prs, {"background": "E90074", "accent": "00E5FF", "text": "FFFFFF"})
    narrative = _add_narrative_slide(prs, variant, source, texts, defect)
    _add_evidence_slide(prs, source, source_image, defect)
    conclusion = _add_conclusion_slide(prs, variant, source, texts, defect)
    if defect == "lowcontrast":
        _set_cards_low_contrast((narrative, conclusion))
    return prs


def _restyle_cosmetic(prs, style):
    for slide in prs.slides:
        _slide_background(slide, style["background"])
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                for paragraph in shape.text_frame.paragraphs:
                    for run in paragraph.runs:
                        run.font.name = style["font"]
                        if run.font.color.type is None:
                            _font_color(run, "FFFFFF")
            if shape.shape_type == 1:
                try:
                    shape.fill.fore_color.rgb = RGBColor.from_string(style["accent"])
                except (AttributeError, ValueError):
                    pass


def _restyle_cover(prs, theme):
    slide = prs.slides[0]
    _slide_background(slide, theme["background"])
    for shape in slide.shapes:
        if shape.name == "Source template label":
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    _font_color(run, theme["accent"])
        elif shape.name == "Presentation title":
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    _font_color(run, theme["text"])
        elif shape.name == "Shape 1":
            _fill(shape, theme["accent"])


def _set_cards_low_contrast(slides):
    for slide in slides:
        for shape in slide.shapes:
            if shape.name.startswith("Fact "):
                for paragraph in shape.text_frame.paragraphs:
                    for run in paragraph.runs:
                        _font_color(run, "173550")


def _html_color(fill, default):
    try:
        value = fill.fore_color.rgb
        return f"#{value}" if value else default
    except (AttributeError, TypeError, ValueError):
        return default


def _html_for_deck(pptx_path, html_path, *, mismatch_quote=None, replacement_quote=None):
    prs = Presentation(str(pptx_path))
    slide_html = []
    page_w, page_h = prs.slide_width, prs.slide_height
    for slide_number, slide in enumerate(prs.slides, 1):
        try:
            background = _html_color(slide.background.fill, "#ffffff")
        except (AttributeError, TypeError, ValueError):
            background = "#ffffff"
        objects = []
        for shape in slide.shapes:
            left, top = shape.left / page_w * 100, shape.top / page_h * 100
            width, height = shape.width / page_w * 100, shape.height / page_h * 100
            if left >= 100 or top >= 100:
                continue
            style = (
                f"left:{left:.3f}%;top:{top:.3f}%;width:{width:.3f}%;height:{height:.3f}%;"
            )
            if getattr(shape, "has_table", False):
                rows = "".join(
                    "<tr>" + "".join(f"<td>{escape(cell.text)}</td>" for cell in row.cells) + "</tr>"
                    for row in shape.table.rows
                )
                objects.append(f'<table style="{style}">{rows}</table>')
            elif getattr(shape, "has_chart", False):
                chart = shape.chart
                labels = []
                try:
                    categories = [str(item.label) for item in chart.plots[0].categories]
                except (AttributeError, IndexError, TypeError):
                    categories = []
                for series in chart.series:
                    labels.extend(
                        f"{escape(category)}: {escape(str(value))}"
                        for category, value in zip(categories, series.values)
                    )
                title = chart.chart_title.text_frame.text if chart.has_title else ""
                axis = chart.value_axis.axis_title.text_frame.text if chart.value_axis.has_title else ""
                objects.append(
                    f'<figure style="{style}"><figcaption>{escape(title)} — {escape(axis)}</figcaption>'
                    f"<p>{escape('; '.join(labels))}</p></figure>"
                )
            elif shape.shape_type == 13:
                encoded = b64encode(shape.image.blob).decode("ascii")
                mime = shape.image.content_type or "image/png"
                objects.append(f'<img alt="Source figure" src="data:{mime};base64,{encoded}" style="{style}">')
            elif getattr(shape, "has_text_frame", False):
                props = shape._element.xpath(".//p:cNvPr")
                if any(item.get("hidden") in {"1", "true"} for item in props):
                    continue
                text = escape(shape.text).replace("\n", "<br>")
                if not text:
                    continue
                fill = _html_color(shape.fill, "transparent")
                objects.append(f'<div style="{style}background:{fill}">{text}</div>')
        slide_html.append(
            f'<section class="slide" aria-label="Slide {slide_number}" style="background:{background}">'
            + "".join(objects)
            + "</section>"
        )
    document = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Presentation export</title>"
        "<style>body{margin:0;background:#d6dde4;font:18px Arial,sans-serif}.deck{padding:24px}"
        ".slide{position:relative;overflow:hidden;width:min(1280px,96vw);aspect-ratio:16/9;margin:0 auto 24px;"
        "box-shadow:0 3px 18px #66778855;color:#fff}.slide>div,.slide>table,.slide>figure,.slide>img{"
        "position:absolute;box-sizing:border-box;padding:8px;overflow:hidden}.slide table{border-collapse:collapse;"
        "background:white;color:#173550}.slide td{border:1px solid #173550;padding:5px}figure{margin:0;"
        "background:#fff;color:#173550}</style></head><body><main class=\"deck\">"
        + "".join(slide_html)
        + "</main></body></html>"
    )
    if mismatch_quote and replacement_quote:
        original = escape(mismatch_quote)
        replacement = escape(replacement_quote)
        if original not in document:
            raise ValueError("HTML mismatch control could not locate its independent source anchor")
        document = document.replace(original, replacement, 1)
    Path(html_path).write_text(document, encoding="utf-8")


def _make_case(source, template_path, case_id, category, mode, case_root, *, cosmetic_style=None, validate=True):
    case_root.mkdir(parents=True, exist_ok=False)
    input_dir = case_root / "input"
    result_dir = case_root / "result"
    input_dir.mkdir()
    result_dir.mkdir()
    source_text = _source_text(source)
    image_path = _make_figure(source, input_dir / "sample-figure.png")
    variants = []
    pdf_hashes, html_hashes, pptx_hashes = {}, {}, {}
    canonical = None
    for variant in VARIANTS:
        variant_dir = result_dir / variant
        variant_dir.mkdir()
        deck_path = variant_dir / "deck.pptx"
        pdf_path = variant_dir / "deck.pdf"
        html_path = variant_dir / "deck.html"
        if category == "duplicates" and mode == "defect" and canonical:
            deck_path.write_bytes(canonical.read_bytes())
        else:
            prs = _make_deck(
                source,
                image_path,
                variant,
                category,
                mode,
                style=cosmetic_style,
            )
            prs.save(deck_path)
            if category == "duplicates" and mode == "defect":
                canonical = deck_path
        pdf_deck = deck_path
        temporary_pdf_deck = None
        if category == "pptx_pdf_mismatch" and mode == "defect":
            temporary_pdf_deck = variant_dir / "pdf-export-source.pptx"
            _make_deck(source, image_path, variant, "number", "defect", pdf_export=True).save(
                temporary_pdf_deck
            )
            pdf_deck = temporary_pdf_deck
        if not to_pdf(pdf_deck, variant_dir, timeout=45):
            raise RuntimeError("LibreOffice is required to render all native defect controls")
        produced_pdf = variant_dir / f"{pdf_deck.stem}.pdf"
        if produced_pdf != pdf_path:
            shutil.move(produced_pdf, pdf_path)
            temporary_pdf_deck.unlink(missing_ok=True)
        _html_for_deck(
            deck_path,
            html_path,
            mismatch_quote=next(
                (fact["quote"] for fact in source["facts"] if fact["id"] == "duration"),
                None,
            )
            if category == "pptx_html_mismatch" and mode == "defect"
            else None,
            replacement_quote=re.sub(
                r"\b(\d+)\b",
                lambda match: str(int(match.group(1)) + 1),
                next(
                    (fact["quote"] for fact in source["facts"] if fact["id"] == "duration"),
                    "",
                ),
                count=1,
            )
            if category == "pptx_html_mismatch" and mode == "defect"
            else None,
        )
        pptx_hashes[variant] = _sha(deck_path.read_bytes())
        pdf_hashes[variant] = _sha(pdf_path.read_bytes())
        html_hashes[variant] = _sha(html_path.read_bytes())
        variants.append(variant)
    table = source["table"]
    facts = deepcopy(source["facts"])
    requirements = [
        {
            "id": "source-table",
            "kind": "table",
            "status": "gold",
            "headers": table["headers"],
            "rows": table["rows"],
            "text": table["text"],
        },
        {
            "id": "source-chart",
            "kind": "chart",
            "status": "gold",
            **source["chart"],
        },
        {
            "id": "source-figure",
            "kind": "image",
            "status": "gold",
            "text": "Retain the submitted source figure and its source pixels.",
        },
        {
            "id": "template-fidelity",
            "kind": "template-fidelity",
            "status": "gold",
            "applicability": "synthetic_reference_control",
            "text": source["template"]["text_contract"],
        },
        {
            "id": "visual-control",
            "kind": "visual",
            "status": "gold",
            "minimum_image_width_inches": 4,
            "text": "Keep the source figure at least 4 inches wide and body text readable.",
        },
        {
            "id": "variant-presentations",
            **_read_registry()["diversity_requirement"],
        },
        {
            "id": "native-export-text-agreement",
            "kind": "text_export_agreement",
            "status": "gold",
            "text": "Preserve required source anchors consistently in the native PPTX, PDF, and HTML text exports.",
        },
    ]
    digest = _sha(source_text.encode("utf-8"))
    image_digest = _sha(image_path.read_bytes())
    case = {
        "id": case_id,
        "slides": 4,
        "variants": variants,
        "content": source_text,
        "content_source": {"file": "authored-control-source", "sha256": digest},
        "reference": {
            "version": "defect-corpus-1",
            "status": "gold",
            "provenance": {"method": "authored_native_control", "review_status": "approved"},
            "points": facts,
            "requirements": requirements,
        },
        "template": template_path,
        "template_source": {
            "source": "authored fictional navy-and-teal control template",
            "format": "PPTX",
            "sha256": _sha(template_path.read_bytes()),
            "source_slides": 1,
            "layouts": 1,
        },
        "source_hashes": {"content": digest, "images": {image_path.name: image_digest}},
        "registry_hashes": {"template": _sha(template_path.read_bytes()), "content": digest},
        "images": [image_path],
        "images_source": [{"file": image_path.name, "sha256": image_digest}],
        "image_origin": "authored_native_control",
        "synthetic": True,
        "template_origin": "authored_control_template",
        "template_fidelity_applicability": "synthetic_reference_control",
        "template_preview_slides": [1],
    }
    bundle_dir = case_root / "bundle"
    bundle = build_bundle(case, result_dir, bundle_dir)
    control_metadata = {
        "schema_version": 1,
        "producer_class": "native_rendered_control",
        "case_family_id": source["id"],
        "rubric_id": "source-organization-and-composition-1",
        "composition_ids": {variant: spec["id"] for variant, spec in _COMPOSITIONS.items()},
        "organization_rules": {variant: spec["organization"] for variant, spec in _COMPOSITIONS.items()},
        "grouped_source_points": {variant: spec["groups"] for variant, spec in _COMPOSITIONS.items()},
        "source_point_ids": [fact["id"] for fact in facts],
        "source_content_sha256": digest,
        "source_ledger_sha256": _sha(json.dumps(facts, sort_keys=True).encode("utf-8")),
        "artifacts": {
            "pptx_sha256": pptx_hashes,
            "pdf_sha256": pdf_hashes,
            "html_sha256": html_hashes,
        },
        "localization": _localizations(category, source),
        "expected_label_file": "../labels.expected.json",
    }
    metadata_path = case_root / "control-metadata.json"
    write_json(metadata_path, control_metadata)
    audit = audit_bundle(bundle, bundle_dir) if validate else {"status": "not_run", "findings": []}
    finding_categories = sorted({finding["category"] for finding in audit["findings"]})
    return {
        "case_id": case_id,
        "bundle_path": str((bundle_dir / "bundle.json").resolve()),
        "control_metadata_path": str(metadata_path.resolve()),
        "source_hash": digest,
        "composition_ids": control_metadata["composition_ids"],
        "artifacts": control_metadata["artifacts"],
        "audit": audit,
        "observed_finding_categories": finding_categories,
        "case_root": str(case_root.resolve()),
        "bundle": bundle,
    }


def _localizations(category, source):
    target = next((fact["id"] for fact in source["facts"] if category in {"number", "negation", "condition", "provenance", "omission", "hidden_text"} and fact["id"] == {"number": "duration", "negation": "caveat", "condition": "decision", "provenance": "proposal", "omission": "compliance", "hidden_text": "compliance"}.get(category)), None)
    if category in {"table_binding", "units"}:
        return {variant: {"slide": 3, "object": "Source data table"} for variant in VARIANTS}
    if category in {"chart_value", "chart_axis"}:
        return {variant: {"slide": 3, "object": "Source comparison chart"} for variant in VARIANTS}
    if category in {"image_missing", "image_distorted"}:
        return {variant: {"slide": 3, "object": "Source figure"} for variant in VARIANTS}
    if category in {"pptx_pdf_mismatch", "pptx_html_mismatch"}:
        return {variant: {"slide": 2, "source_point_id": "duration"} for variant in VARIANTS}
    if category in {"template_fidelity", "backgroundloss", "foreign_template_content"}:
        return {variant: {"slide": 1 if category == "template_fidelity" else 4, "region": "slide background or template label"} for variant in VARIANTS}
    if category in {"readability", "clipping", "overlap", "lowcontrast"}:
        point = "caveat" if category == "overlap" else "compliance"
        return {variant: {"slide": 4, "source_point_id": point} for variant in VARIANTS}
    if category == "duplicates":
        return {variant: {"slide": "all", "region": "whole exported presentation"} for variant in VARIANTS}
    return {variant: {"slide": 4, "source_point_id": target} for variant in VARIANTS}


def _write_source_limited(source, template_path, case_id, case_root, *, validate=True):
    case_root.mkdir(parents=True, exist_ok=False)
    result_dir = case_root / "result"
    result_dir.mkdir()
    content = f"# {source['title']}\n\n{source['content']}\n"
    digest = _sha(content.encode())
    variants = []
    for index, variant in enumerate(VARIANTS):
        variant_dir = result_dir / variant
        variant_dir.mkdir()
        prs = Presentation()
        prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        _slide_background(slide, THEME["background"])
        if index == 0:
            box = (0.75, 2.0, 4.8, 2.0)
        elif index == 1:
            box = (4.25, 2.0, 4.8, 2.0)
        else:
            box = (7.8, 2.0, 4.8, 2.0)
        card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, *(Inches(v) for v in box))
        _fill(card, "21445E")
        _add_text(slide, "Single source point", source["quote"], *box, color="FFFFFF", size=26, bold=True)
        deck = variant_dir / "deck.pptx"
        prs.save(deck)
        if not to_pdf(deck, variant_dir, timeout=45):
            raise RuntimeError("LibreOffice is required to render source-limited controls")
        html_path = variant_dir / "deck.html"
        _html_for_deck(deck, html_path)
        variants.append(variant)
    point = {
        "id": source["point_id"],
        "statement": source["statement"],
        "quote": source["quote"],
        "required": True,
        "origin": "user_text",
        "numbers": [{"value": source["number"], "unit": source["unit"]}],
    }
    facts = [point]
    case = {
        "id": case_id,
        "slides": 1,
        "variants": variants,
        "content": content,
        "content_source": {"file": "authored-source-limited-control", "sha256": digest},
        "reference": {
            "version": "defect-corpus-1",
            "status": "gold",
            "provenance": {"method": "authored_native_control", "review_status": "approved"},
            "points": facts,
            "requirements": [
                {"id": "variant-presentations", **_read_registry()["diversity_requirement"]},
                {"id": "native-export-text-agreement", "kind": "text_export_agreement", "status": "gold", "text": "Preserve required source anchors across exports."},
            ],
        },
        "template": template_path,
        "template_source": {"source": "authored fictional control template", "format": "PPTX", "sha256": _sha(template_path.read_bytes()), "source_slides": 1, "layouts": 1},
        "source_hashes": {"content": digest, "images": {}},
        "images": [],
        "images_source": [],
        "synthetic": True,
        "template_origin": "authored_control_template",
        "template_fidelity_applicability": "synthetic_reference_control",
        "template_preview_slides": [1],
    }
    bundle_dir = case_root / "bundle"
    bundle = build_bundle(case, result_dir, bundle_dir)
    metadata = {
        "schema_version": 1,
        "producer_class": "native_rendered_control",
        "case_family_id": source["id"],
        "rubric_id": "source-organization-and-composition-1",
        "source_scope": "one measured source point; insufficient material to validate three meaningful organizations",
        "composition_ids": {variant: f"single-point-{index + 1}" for index, variant in enumerate(VARIANTS)},
        "source_point_ids": [point["id"]],
        "source_content_sha256": digest,
        "source_ledger_sha256": _sha(json.dumps(facts, sort_keys=True).encode()),
    }
    path = case_root / "control-metadata.json"
    write_json(path, metadata)
    audit = audit_bundle(bundle, bundle_dir) if validate else {"status": "not_run", "findings": []}
    return {
        "case_id": case_id,
        "bundle_path": str((bundle_dir / "bundle.json").resolve()),
        "control_metadata_path": str(path.resolve()),
        "source_hash": digest,
        "composition_ids": metadata["composition_ids"],
        "artifacts": {},
        "audit": audit,
        "observed_finding_categories": sorted({item["category"] for item in audit["findings"]}),
        "case_root": str(case_root.resolve()),
        "bundle": bundle,
    }


def _write_cosmetic_control(source, template_path, style, case_id, case_root):
    row = _make_case(
        source,
        template_path,
        case_id,
        "cosmetic_duplicate",
        "clean",
        case_root,
        cosmetic_style=style,
    )
    return row


def _failed_localizations(finding):
    """Return variants and pairs established by failed evidence only."""
    if finding.get("severity") != "failed":
        return set(), set()
    evidence = finding.get("evidence", {})
    variants, pairs = set(), set()
    if evidence.get("variant"):
        variants.add(evidence["variant"])
    if finding.get("category") == "duplicates":
        for pair in evidence.get("pairs", []):
            if len(pair) == 2:
                pairs.add(tuple(sorted(pair)))
                variants.update(pair)
    assessment = evidence.get("assessment", {})
    for item in assessment.get("findings", []):
        if item.get("status") == "failed" and item.get("variant"):
            variants.add(item["variant"])
    for item in assessment.get("pairs", []):
        if item.get("status") == "failed":
            pair = item.get("variants", [])
            variants.update(pair)
            if len(pair) == 2:
                pairs.add(tuple(sorted(pair)))
    return variants, pairs


def _score_validation(records, labels):
    labels_by_id = {item["case_id"]: item for item in labels}
    matrix = []
    for row in records:
        label = labels_by_id[row["case_id"]]
        findings = row["audit"]["findings"]
        found = {item["category"] for item in findings}
        expected = label.get("expected_category")
        target = _LEGACY_TARGET.get(expected)
        proxy = _LEGACY_PROXY.get(expected)
        if expected == "presentation_diversity":
            target = "presentation_diversity"
        target_findings = [
            item for item in findings if target and item.get("category") == target
        ]
        target_severity = {item.get("severity") for item in target_findings}
        expected_variants = set(label.get("expected_localization", {}))
        located_variants = set()
        failed_pairs = set()
        for finding in target_findings:
            variants, pairs = _failed_localizations(finding)
            located_variants.update(variants)
            failed_pairs.update(pairs)
        all_locations = located_variants >= expected_variants
        if row["audit"].get("status") == "not_run":
            outcome = "not_run"
        elif label["expected_status"] == "inconclusive":
            outcome = (
                "inconclusive"
                if row["audit"]["status"] == "inconclusive"
                else "false_positive"
                if row["audit"]["status"] == "failed"
                else "false_success"
            )
        elif label["expected_status"] == "passed":
            outcome = "false_positive" if any(item["severity"] == "failed" for item in findings) else "inconclusive" if row["audit"]["status"] == "inconclusive" else "clean"
        elif target and target in found and "failed" in target_severity and all_locations:
            if target == "presentation_diversity":
                assessment = next(
                    (item.get("evidence", {}).get("assessment", {}) for item in target_findings if item.get("evidence", {}).get("assessment")),
                    {},
                )
                all_pairs_failed = len(assessment.get("pairs", [])) == 3 and all(
                    pair.get("status") == "failed" for pair in assessment.get("pairs", [])
                )
                outcome = "detected" if all_pairs_failed else "inconclusive"
            elif target == "duplicates":
                outcome = "detected" if len(failed_pairs) == 3 else "inconclusive"
            else:
                outcome = "detected"
        elif target and target in found and "failed" in target_severity:
            outcome = "inconclusive"
        elif proxy and proxy in found:
            outcome = "inconclusive"
        elif target and target in found and "inconclusive" in target_severity:
            outcome = "inconclusive"
        else:
            outcome = "missed"
        matrix.append(
            {
                "case_id": row["case_id"],
                "expected_status": label["expected_status"],
                "expected_category": expected,
                "legacy_detection_target": target,
                "observed_categories": sorted(found),
                "outcome": outcome,
                "audit_status": row["audit"]["status"],
                "target_severity": sorted(severity for severity in target_severity if severity),
                "failed_target_variants": sorted(located_variants),
                "expected_target_variants": sorted(expected_variants),
                "failed_target_pairs": [list(pair) for pair in sorted(failed_pairs)],
                "all_expected_variant_localizations_present": all_locations,
                "unrelated_failed_categories": sorted(
                    item["category"] for item in findings if item["severity"] == "failed" and item["category"] != target
                ),
            }
        )
    counts = {
        key: sum(item["outcome"] == key for item in matrix)
        for key in ("detected", "missed", "inconclusive", "false_positive", "false_success", "clean", "not_run")
    }
    return {
        "scorer": "audit_bundle-artifact-findings-1",
        "label_access": "scoring layer only; audit_bundle ran on artifacts without expected label data",
        "counts": counts,
        "cases": matrix,
    }


def _verify_labels_labels(records, labels):
    ids = {row["case_id"] for row in records}
    label_ids = {row["case_id"] for row in labels}
    if ids != label_ids:
        raise ValueError("Expected-label registry does not match the generated case set")
    for row in records:
        bundle_path = Path(row["bundle_path"])
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        if bundle.get("case_id") != row["case_id"]:
            raise ValueError(f"Unexpected case identity in bundle: {bundle_path}")
        for variant in bundle.get("variants", {}).values():
            for key in ("pptx", "pdf", "html"):
                candidate = (bundle_path.parent / variant.get(key, "")).resolve()
                if key in variant and not candidate.is_relative_to(bundle_path.parent.resolve()):
                    raise ValueError(f"Artifact escaped its bundle: {candidate}")
                if key in variant and not candidate.is_file():
                    raise FileNotFoundError(candidate)


def build_defect_corpus(output, *, categories=None, include_support=True, validate=True):
    """Build native PPTX/PDF/HTML controls and a sealed expected-label matrix.

    `categories` limits the main defect families for bounded local previews. Labels
    are written outside every judge bundle. The offline audit sees only bundles.
    """
    registry = _read_registry()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_ids = {item["id"] for item in registry["categories"]}
    requested = source_ids if categories is None else set(categories)
    unknown = requested - source_ids
    if unknown:
        raise ValueError(f"Unknown defect categories: {', '.join(sorted(unknown))}")
    template_dir = output / "shared"
    template_dir.mkdir(exist_ok=False)
    template_path = _make_template(template_dir / "source-template.pptx")
    sources = registry["sources"]
    records, labels = [], []
    sequence = 0

    def next_id():
        nonlocal sequence
        sequence += 1
        return f"dc-{sequence:04d}"

    for index, category in enumerate(registry["categories"]):
        if category["id"] not in requested:
            continue
        source = sources[index % len(sources)]
        family_id = f"{category['id']}-{source['id']}"
        for mode, label_kind, expected_status in (
            ("clean", "clean", "passed"),
            ("defect", category["id"], "failed"),
            ("paraphrase", "permissible_transformation", "passed"),
        ):
            case_id = next_id()
            case_root = output / case_id
            record = _make_case(
                source,
                template_path,
                case_id,
                category["id"],
                mode,
                case_root,
                validate=validate,
            )
            records.append(record)
            label = {
                "case_id": case_id,
                "family_id": family_id,
                "control_kind": label_kind,
                "expected_status": expected_status,
                "expected_category": category["id"] if mode == "defect" else label_kind,
                "composition_count": 3,
                "composition_ids": record["composition_ids"],
                "source_hash": record["source_hash"],
                "expected_localization": _localizations(category["id"], source) if mode == "defect" else {},
            }
            labels.append(label)

    if include_support:
        for control, source in zip(registry["cosmetic_controls"], sources, strict=True):
            case_id = next_id()
            style_by_variant = dict(zip(VARIANTS, (control["style_a"], control["style_b"], control["style_c"]), strict=True))
            # Each delivery is styled independently while its exported facts, order and geometry match.
            # _make_case's single style parameter is expanded here into three generated native exports.
            record = _make_cosmetic_case(
                source,
                template_path,
                case_id,
                output / case_id,
                style_by_variant,
                validate=validate,
            )
            records.append(record)
            labels.append({"case_id": case_id, "family_id": control["id"], "control_kind": "cosmetic_only_duplicate", "expected_status": "failed", "expected_category": "presentation_diversity", "composition_count": 1, "composition_ids": record["composition_ids"], "source_hash": record["source_hash"], "expected_localization": {variant: {"slide": "all", "region": "same content topology with cosmetic changes"} for variant in VARIANTS}})
        for source in registry["source_limited"]:
            case_id = next_id()
            record = _write_source_limited(
                source, template_path, case_id, output / case_id, validate=validate
            )
            records.append(record)
            labels.append({"case_id": case_id, "family_id": source["id"], "control_kind": "source_limited", "expected_status": "inconclusive", "expected_category": "source_limited", "composition_count": 0, "composition_ids": record["composition_ids"], "source_hash": record["source_hash"], "expected_localization": {}})

    _verify_labels_labels(records, labels)
    matrix = _score_validation(records, labels) if validate else {"scorer": "not_run", "cases": [], "counts": {}}
    label_path = output / "labels.expected.json"
    write_json(label_path, {"schema_version": 1, "warning": "Controller-only expected labels; never copy into a judge packet.", "cases": labels})
    validation_path = output / "validation.json"
    write_json(validation_path, matrix)
    case_manifest = [
        {
            "case_id": row["case_id"],
            "bundle_path": row["bundle_path"],
            "control_metadata_path": row["control_metadata_path"],
            "source_hash": row["source_hash"],
            "composition_ids": row["composition_ids"],
            "artifacts": row["artifacts"],
        }
        for row in records
    ]
    counts = {
        "defect_categories": len(requested),
        "main_cases": len(requested) * 3,
        "cosmetic_only_duplicate_controls": min(3, len(registry["cosmetic_controls"])) if include_support else 0,
        "source_limited_controls": len(registry["source_limited"]) if include_support else 0,
        "total_cases": len(records),
        "native_presentations": len(records) * len(VARIANTS),
        "rendered_pdf_exports": sum(len(row["bundle"]["variants"]) for row in records),
        "html_exports": sum(sum("html" in item for item in row["bundle"]["variants"].values()) for row in records),
        "unique_source_families": len({row["source_hash"] for row in records}),
        "composition_profiles_per_defect_category": 3,
    }
    manifest_path = output / "defect-corpus.json"
    write_json(
        manifest_path,
        {
            "schema_version": 1,
            "contract_version": "independent-native-defect-corpus-1",
            "fixture_path": str(FIXTURE),
            "producer_class": "native_rendered_control",
            "categories": sorted(requested),
            "case_index": case_manifest,
            "counts": counts,
            "expected_labels_path": str(label_path),
            "validation_path": str(validation_path),
            "historical_observations_path": str((ROOT / "audit_e2e" / "fixtures" / "historical_observations.json").resolve()),
        },
    )
    return {
        "manifest_path": str(manifest_path),
        "labels_path": str(label_path),
        "validation_path": str(validation_path),
        "counts": counts,
        "matrix": matrix,
        "cases": [
            {
                "case_id": row["case_id"],
                "bundle_path": row["bundle_path"],
                "control_metadata_path": row["control_metadata_path"],
                "expected_label": labels[index],
                "composition_ids": row["composition_ids"],
            }
            for index, row in enumerate(records)
        ],
    }


def _make_cosmetic_case(source, template_path, case_id, case_root, styles, *, validate=True):
    case_root.mkdir(parents=True, exist_ok=False)
    input_dir, result_dir = case_root / "input", case_root / "result"
    input_dir.mkdir()
    result_dir.mkdir()
    image_path = _make_figure(source, input_dir / "sample-figure.png")
    content = _source_text(source)
    digest, image_digest = _sha(content.encode()), _sha(image_path.read_bytes())
    table = source["table"]
    requirements = [
        {"id": "source-table", "kind": "table", "status": "gold", "headers": table["headers"], "rows": table["rows"]},
        {"id": "source-figure", "kind": "image", "status": "gold", "text": "Retain the submitted source figure."},
        {"id": "visual-control", "kind": "visual", "status": "gold", "minimum_image_width_inches": 4},
        {"id": "variant-presentations", **_read_registry()["diversity_requirement"]},
        {"id": "native-export-text-agreement", "kind": "text_export_agreement", "status": "gold", "text": "Preserve source anchors across exports."},
    ]
    case = {
        "id": case_id,
        "slides": 4,
        "variants": list(VARIANTS),
        "content": content,
        "content_source": {"file": "authored-control-source", "sha256": digest},
        "reference": {"version": "defect-corpus-1", "status": "gold", "provenance": {"method": "authored_native_control", "review_status": "approved"}, "points": deepcopy(source["facts"]), "requirements": requirements},
        "template": template_path,
        "template_source": {"source": "authored fictional control template", "format": "PPTX", "sha256": _sha(template_path.read_bytes()), "source_slides": 1, "layouts": 1},
        "source_hashes": {"content": digest, "images": {image_path.name: image_digest}},
        "images": [image_path],
        "images_source": [{"file": image_path.name, "sha256": image_digest}],
        "image_origin": "authored_native_control",
        "synthetic": True,
        "template_origin": "authored_control_template",
        "template_fidelity_applicability": "synthetic_reference_control",
        "template_preview_slides": [1],
    }
    asset_hashes, variants = {}, []
    texts = _text_for(source, "clean")
    for variant, style in styles.items():
        variant_dir = result_dir / variant
        variant_dir.mkdir()
        prs = Presentation()
        prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
        _add_cover(prs, source)
        # Same executive organization, block geometry and reading order in every delivery.
        _add_narrative_slide(prs, "executive", source, texts, None)
        _add_evidence_slide(prs, source, image_path, None)
        _add_conclusion_slide(prs, "executive", source, texts, None)
        _restyle_cosmetic(prs, style)
        deck = variant_dir / "deck.pptx"
        prs.save(deck)
        if not to_pdf(deck, variant_dir, timeout=45):
            raise RuntimeError("LibreOffice is required to render cosmetic duplicate controls")
        html_path = variant_dir / "deck.html"
        _html_for_deck(deck, html_path)
        asset_hashes[variant] = {key: _sha((variant_dir / f"deck.{key}").read_bytes()) for key in ("pptx", "pdf", "html")}
        variants.append(variant)
    bundle_dir = case_root / "bundle"
    bundle = build_bundle(case, result_dir, bundle_dir)
    metadata = {
        "schema_version": 1,
        "producer_class": "native_rendered_control",
        "case_family_id": "cosmetic-only-duplicate",
        "rubric_id": "source-organization-and-composition-1",
        "composition_ids": {variant: "same-decision-first-cards-v1" for variant in VARIANTS},
        "organization_rules": {variant: "identical fact groups, order and geometry; only palette/font changed" for variant in VARIANTS},
        "grouped_source_points": {variant: _COMPOSITIONS["executive"]["groups"] for variant in VARIANTS},
        "source_point_ids": [item["id"] for item in source["facts"]],
        "source_content_sha256": digest,
        "source_ledger_sha256": _sha(json.dumps(source["facts"], sort_keys=True).encode()),
        "artifacts": asset_hashes,
        "localization": {variant: {"slide": "all", "region": "same content topology"} for variant in VARIANTS},
        "expected_label_file": "../labels.expected.json",
    }
    metadata_path = case_root / "control-metadata.json"
    write_json(metadata_path, metadata)
    audit = audit_bundle(bundle, bundle_dir) if validate else {"status": "not_run", "findings": []}
    return {
        "case_id": case_id,
        "bundle_path": str((bundle_dir / "bundle.json").resolve()),
        "control_metadata_path": str(metadata_path.resolve()),
        "source_hash": digest,
        "composition_ids": metadata["composition_ids"],
        "artifacts": asset_hashes,
        "audit": audit,
        "observed_finding_categories": sorted({item["category"] for item in audit["findings"]}),
        "case_root": str(case_root.resolve()),
        "bundle": bundle,
    }


def main():
    parser = argparse.ArgumentParser(description="Build offline native BAD-EXPORT controls")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--category", action="append", dest="categories")
    parser.add_argument("--no-support-controls", action="store_true")
    parser.add_argument("--no-validation", action="store_true")
    args = parser.parse_args()
    result = build_defect_corpus(
        args.output,
        categories=args.categories,
        include_support=not args.no_support_controls,
        validate=not args.no_validation,
    )
    print(json.dumps({key: result[key] for key in ("manifest_path", "labels_path", "validation_path", "counts", "matrix")}, indent=2))


if __name__ == "__main__":
    main()
