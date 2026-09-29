"""Count-aware, grounded editorial planning from immutable user evidence."""

from studio.contents.narrative_data import (
    DataRow as DataRow,
    choose_visualization as choose_visualization,
)
from studio.contents.narrative_layout import narrative_storyboard as narrative_storyboard

import re
from collections import Counter
from typing import Literal
from pydantic import Field
from studio.models import StrictModel
from studio.contents.numeric_text import normalize_numeric_typography


class Excerpt(StrictModel):
    fact_id: str
    quotes: list[str] = Field(min_length=1, max_length=12)


class NarrativeSlide(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    excerpts: list[Excerpt] = Field(min_length=1, max_length=300)
    relationship: Literal["none", "comparison", "time", "share", "table"] = "none"
    rows: list[DataRow] = Field(default_factory=list, max_length=20)


class Narrative(StrictModel):
    slides: list[NarrativeSlide] = Field(min_length=1, max_length=30)


def numbers(text):
    return Counter(
        re.sub(r"\s", "", value).replace(",", ".")
        for value in re.findall(r"[-−+]?\d+(?:[.,]\d+)?\s*%?", normalize_numeric_typography(text))
    )


def validate_narrative(raw, content):
    parsed = Narrative.model_validate(raw)
    facts = {f.id: f for f in content.facts}
    ids = [e.fact_id for slide in parsed.slides for e in slide.excerpts]
    if ids != list(facts):
        raise ValueError("Every source fact must occur once, in source order")
    for slide in parsed.slides:
        evidence = "\n".join(facts[e.fact_id].text for e in slide.excerpts)
        if numbers(slide.title) - numbers(evidence):
            raise ValueError("Unsupported title number")
        tables = [
            facts[e.fact_id].source
            for e in slide.excerpts
            if facts[e.fact_id].source != "user_text"
        ]
        if len(tables) > 1 or tables and slide.rows:
            raise ValueError("One complete source table per slide")
        for e in slide.excerpts:
            fact = facts[e.fact_id]
            cursor = 0
            for quote in e.quotes:
                pos = fact.text.find(quote, cursor)
                if not quote.strip() or pos < 0:
                    raise ValueError("Summary must use ordered verbatim excerpts")
                cursor = pos + len(quote)
            summary = " … ".join(e.quotes)
            units = r"(?i)\b(?:млн|млрд|тыс|руб|доллар\w*|евро|кг|км|человек|сотрудник\w*)\b|[₽$€%]"
            if Counter(re.findall(units, fact.text)) != Counter(re.findall(units, summary)):
                raise ValueError("Measurement units must survive the summary")
            if numbers(fact.text) != numbers(summary):
                raise ValueError("All original numbers and units must survive the summary")
            # A shortened qualification can invert a claim despite literal quotes.
            if (
                fact.source != "user_text"
                or fact.kind == "caveat"
                or re.search(
                    r"\b(?:не|нет|без|если|кроме|только|менее|более|пример|условн\w*|not|unless|except)\b",
                    fact.text,
                    re.I,
                )
            ) and summary != fact.text:
                raise ValueError("Keep qualified statements and source tables intact")
        allowed = {e.fact_id for e in slide.excerpts}
        for row in slide.rows:
            if (
                row.fact_id not in allowed
                or row.label not in facts[row.fact_id].text
                or row.value not in facts[row.fact_id].text
            ):
                raise ValueError("Data labels and values must quote the same source fact")
        if slide.rows and (len(slide.rows) < 2 or slide.relationship == "none"):
            raise ValueError("A quantitative comparison needs at least two grounded rows")
        if len({r.label for r in slide.rows}) != len(slide.rows):
            raise ValueError("Ambiguous duplicate categories")
    from studio.security_gate import check_text_fields

    check_text_fields(narrative=parsed.model_dump_json())
    return parsed.model_dump()


async def prepare_narrative(package, gateway, progress=None):
    if not package.constraints.summarize:
        return False
    if gateway.settings.mode != "api":
        package.analysis.setdefault("warnings", []).append(
            "Модель отключена: смысловое сокращение недоступно; исходный текст сохранён."
        )
        return False
    from studio.contents.editorial import prepare_editorial

    return await prepare_editorial(package, gateway, progress)
