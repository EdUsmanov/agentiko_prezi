"""Reviewable slide copy for the explicit short-brief workflow."""

import hashlib
import json

from studio.models import BriefDraft, ContentModel, DraftBullet, DraftSlide, Fact
from studio.security import InputRejected, scan_text


def draft_hash(draft: BriefDraft) -> str:
    """Bind approval to every visible title, bullet and source reference."""
    value = BriefDraft.model_validate(draft).model_dump(mode="json")
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_draft(package) -> BriefDraft:
    evidence = package.brief_evidence
    plans = package.prepared_plans
    if plans is None or evidence is None:
        raise ValueError("Для краткого брифа нет проверенного плана и текста")
    canonical = plans.variants[0]
    if any(
        [(s.title, s.purpose, s.fact_ids) for s in variant.slides]
        != [(s.title, s.purpose, s.fact_ids) for s in canonical.slides]
        for variant in plans.variants[1:]
    ):
        raise ValueError("Варианты меняют одобренный текст или порядок слайдов")
    evidence = ContentModel.model_validate(evidence)
    sources = {f.id: f for f in evidence.facts}
    facts = {f.id: f for f in package.content.facts}
    tables = {t.id: t for t in package.content.tables}
    editorial = package.analysis.get("editorial")
    direct = not editorial
    citations = {
        row["fact_id"]: [item["fact_id"] for item in row["evidence"]]
        for row in (editorial or {}).get("provenance", [])
    }
    slides = []
    for slide in canonical.slides:
        bullets = []
        for fact_id in slide.fact_ids:
            fact = facts.get(fact_id)
            if fact is None:
                raise ValueError("План ссылается на неизвестный текст")
            ids = citations.get(fact_id, [])
            if (
                direct
                and fact_id in sources
                and fact.text == sources[fact_id].text
                and fact.source == sources[fact_id].source
            ):
                ids = [fact_id]
            value = fact.text
            if fact.source in tables:
                table = tables[fact.source]
                value = "\n".join(" | ".join(row) for row in [table.headers] + table.rows)
                if direct:
                    original = next((t for t in evidence.tables if t.id == fact.source), None)
                    if original is None or (table.headers, table.rows) != (
                        original.headers,
                        original.rows,
                    ):
                        raise ValueError("Таблица слайда отличается от исходных материалов")
                else:
                    original_table_id = editorial["plan"]["slides"][len(slides)].get(
                        "source_table_id"
                    )
                    ids = [f.id for f in sources.values() if f.source == original_table_id]
            if not ids or any(fid not in sources for fid in ids):
                raise ValueError("Текст слайда не привязан к исходным материалам")
            bullets.append(
                DraftBullet(
                    text=value,
                    fact_ids=ids,
                    proposed=any(sources[fid].source == "model_proposal" for fid in ids),
                )
            )
        slides.append(DraftSlide(title=slide.title, purpose=slide.purpose, bullets=bullets))
    return BriefDraft(slides=slides)


def assert_draft_matches_package(package) -> None:
    if package.input_mode != "brief":
        return
    if package.draft is None or package.draft != build_draft(package):
        raise ValueError("Утверждаемый текст отличается от подготовленного плана")


def apply_edited_draft(
    source: ContentModel, draft: BriefDraft, *, allowed_fact_ids: set[str] | None = None
) -> tuple[ContentModel, list[dict]]:
    """Treat changed copy as user-approved input, never as a changed source fact."""
    draft = BriefDraft.model_validate(draft)
    if (
        sum(len(slide.bullets) for slide in draft.slides) > 300
        or sum(len(b.text) for slide in draft.slides for b in slide.bullets) > 120_000
    ):
        raise InputRejected("Черновик слишком велик для подготовки презентации")
    known = {f.id for f in source.facts} | (allowed_fact_ids or set())
    facts = []
    provenance = []
    for si, slide in enumerate(draft.slides, 1):
        clean_title, suspicious = scan_text(slide.title)
        if suspicious or clean_title.strip() != slide.title.strip():
            raise InputRejected("Заголовок черновика содержит недопустимые инструкции")
        for bi, bullet in enumerate(slide.bullets, 1):
            clean, suspicious = scan_text(bullet.text)
            if suspicious or clean.strip() != bullet.text.strip():
                raise InputRejected("Текст черновика содержит недопустимые инструкции")
            if any(fid not in known for fid in bullet.fact_ids):
                raise InputRejected("Черновик ссылается на неизвестный исходный факт")
            fid = f"approved-{si}-{bi}"
            facts.append(
                Fact(
                    id=fid,
                    text=bullet.text,
                    section=slide.title,
                    source="user_approved_draft",
                )
            )
            provenance.append({"fact_id": fid, "source_fact_ids": bullet.fact_ids})
    if not facts:
        raise InputRejected("В подтверждаемом черновике нет содержательных тезисов")
    content = ContentModel(
        title=draft.slides[0].title,
        facts=facts,
        warnings=list(source.warnings),
        quarantined=list(source.quarantined),
    )
    return content, provenance
