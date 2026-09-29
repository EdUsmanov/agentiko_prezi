from types import SimpleNamespace

import pytest

from studio.contents.brief import (
    apply_edited_draft,
    assert_draft_matches_package,
    build_draft,
    draft_hash,
)
from studio.contents.parsing import parse_content
from studio.models import BriefDraft, DraftBullet, DraftSlide, Fact, Plans, SlidePlan, VariantPlan
from studio.security import InputRejected


def test_brief_hash_covers_visible_copy_and_evidence():
    draft = BriefDraft(
        slides=[
            DraftSlide(
                title="План",
                purpose="content",
                bullets=[DraftBullet(text="Проверить спрос", fact_ids=["draft1"], proposed=True)],
            )
        ]
    )
    changed = draft.model_copy(deep=True)
    changed.slides[0].bullets[0].text = "Проверить спрос в пилоте"
    assert draft_hash(draft) != draft_hash(changed)
    changed = draft.model_copy(deep=True)
    changed.slides[0].bullets[0].fact_ids = ["f1"]
    assert draft_hash(draft) != draft_hash(changed)


def test_build_draft_marks_model_proposals_without_changing_source():
    source = parse_content("Команда создаёт сервис презентаций.")
    evidence = source.model_copy(deep=True)
    evidence.facts.append(Fact(id="draft1", text="Предлагается пилот.", source="model_proposal"))
    variants = [
        VariantPlan(
            key=key,
            title=key,
            slides=[SlidePlan(title="Пилот", purpose="content", fact_ids=["summary-1-1"])],
        )
        for key in ("executive", "analytical", "story")
    ]
    package = SimpleNamespace(
        original_content=source,
        brief_evidence=evidence,
        content=source.model_copy(
            update={"facts": [Fact(id="summary-1-1", text="Начать пилот")]}, deep=True
        ),
        prepared_plans=Plans(variants=variants),
        analysis={
            "editorial": {
                "plan": {
                    "slides": [
                        {
                            "title": "Пилот",
                            "purpose": "content",
                            "bullets": [
                                {"text": "Начать пилот", "evidence": [{"fact_id": "draft1"}]}
                            ],
                        }
                    ]
                },
                "provenance": [{"fact_id": "summary-1-1", "evidence": [{"fact_id": "draft1"}]}],
            }
        },
    )
    draft = build_draft(package)
    assert draft.slides[0].bullets[0].proposed
    assert draft.slides[0].bullets[0].fact_ids == ["draft1"]
    assert len(package.original_content.facts) == 1
    package.input_mode = "brief"
    package.draft = draft
    assert_draft_matches_package(package)
    package.prepared_plans.variants[1].slides[0].title = "Другой заголовок"
    with pytest.raises(ValueError):
        assert_draft_matches_package(package)


def test_extractive_brief_draft_cites_direct_source_facts():
    source = parse_content("Команда открывает пилот. Руководитель проверяет результат.")
    slides = [
        SlidePlan(title=f"Этап {index}", fact_ids=[fact.id])
        for index, fact in enumerate(source.facts, 1)
    ]
    package = SimpleNamespace(
        brief_evidence=source.model_copy(deep=True),
        content=source,
        prepared_plans=Plans(
            variants=[
                VariantPlan(key=key, title=key, slides=slides)
                for key in ("executive", "analytical", "story")
            ]
        ),
        analysis={},
    )
    draft = build_draft(package)
    assert [bullet.text for slide in draft.slides for bullet in slide.bullets] == [
        fact.text for fact in source.facts
    ]
    assert [bullet.fact_ids for slide in draft.slides for bullet in slide.bullets] == [
        [fact.id] for fact in source.facts
    ]
    assert not any(bullet.proposed for slide in draft.slides for bullet in slide.bullets)


def test_extractive_brief_draft_cites_direct_source_table():
    source = parse_content(
        "# Данные\n| Команда | Заявки | Выполнено | Часы |\n|---|---:|---:|---:|\n"
        "| Север | 40 | 34 | 18 |\n| Центр | 36 | 33 | 15 |\n| Юг | 28 | 24 | 21 |"
    )
    package = SimpleNamespace(
        brief_evidence=source.model_copy(deep=True),
        content=source,
        prepared_plans=Plans(
            variants=[
                VariantPlan(
                    key=key,
                    title=key,
                    slides=[
                        SlidePlan(
                            title="Таблица",
                            fact_ids=[source.facts[0].id],
                            table_id=source.tables[0].id,
                        )
                    ],
                )
                for key in ("executive", "analytical", "story")
            ]
        ),
        analysis={},
    )
    draft = build_draft(package)
    bullet = draft.slides[0].bullets[0]
    assert bullet.fact_ids == [source.facts[0].id]
    assert "Север | 40 | 34 | 18" in bullet.text


def test_edited_draft_keeps_original_and_rejects_unknown_sources():
    source = parse_content("Команда создаёт сервис презентаций.")
    draft = BriefDraft(
        slides=[
            DraftSlide(
                title="Пилот",
                bullets=[DraftBullet(text="Провести пилот", fact_ids=["draft1"], proposed=True)],
            )
        ]
    )
    derived, provenance = apply_edited_draft(source, draft, allowed_fact_ids={"draft1"})
    assert source.facts[0].text == "Команда создаёт сервис презентаций."
    assert derived.facts[0].source == "user_approved_draft"
    assert provenance[0]["source_fact_ids"] == ["draft1"]
    draft.slides[0].bullets[0].fact_ids = ["unknown"]
    with pytest.raises(InputRejected):
        apply_edited_draft(source, draft, allowed_fact_ids={"draft1"})
    draft.slides[0].bullets[0].fact_ids = ["draft999"]
    with pytest.raises(InputRejected):
        apply_edited_draft(source, draft, allowed_fact_ids={"draft1"})
    draft.slides[0].bullets[0].fact_ids = ["approved-99-99"]
    with pytest.raises(InputRejected):
        apply_edited_draft(source, draft, allowed_fact_ids={"draft1"})
