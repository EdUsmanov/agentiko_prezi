import asyncio
import json
import shutil
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
import pytest


def test_template_snapshot_reuses_artifacts_not_content_and_rejects_tamper(prepared, tmp_path):
    from studio.template_cache import TemplateCache

    settings, store, p = prepared
    source = store.directory(p.id)
    analysis = {
        "template_semantics": {"status": "completed"},
        "text_zone_review": {"patterns": []},
        "storyboard": [{"title": "PRIVATE OLD USER CONTENT"}],
        "warnings": [],
    }
    cache = TemplateCache(settings)
    assert cache.save(p.template, source, analysis)
    destination = tmp_path / "next-job"
    destination.mkdir()
    shutil.copy2(source / "input.pptx", destination / "input.pptx")
    restored = cache.restore(destination / "input.pptx", destination)
    assert restored and "storyboard" not in restored[1]
    profile = restored[0]
    assert profile.sha256 == p.template.sha256
    assert all(str(source) not in row.background_image for row in profile.patterns)
    assert all(
        Path(row.background_image).is_file() for row in profile.patterns if row.background_image
    )
    entry = cache.location(profile.sha256)
    manifest = json.loads((entry / "snapshot.json").read_text())
    assert manifest["files"]
    victim = next(iter(manifest["files"]))
    (entry / "files" / victim).write_bytes(b"corrupted")
    assert cache.restore(destination / "input.pptx", destination) is None
    assert (
        TemplateCache(replace(settings, model_id="different-model")).restore(
            destination / "input.pptx", destination
        )
        is None
    )


def test_repeat_prepare_uses_template_cache_but_new_source_content(prepared, monkeypatch):
    from studio import pipeline
    from studio.template_cache import TemplateCache

    settings, store, p = prepared
    assert TemplateCache(settings).save(
        p.template,
        store.directory(p.id),
        {
            "template_semantics": {"status": "completed"},
            "text_zone_review": {"patterns": []},
            "warnings": [],
        },
    )
    new = store.create("preparation", {"template_name": "same.pptx"})
    shutil.copy2(store.directory(p.id) / "input.pptx", store.directory(new["id"]) / "input.pptx")
    monkeypatch.setattr(
        pipeline,
        "analyze_template",
        lambda *a, **k: pytest.fail("Repeated technical template analysis"),
    )

    async def intelligence(package, path, gateway, progress):
        assert package.analysis.pop("_template_snapshot")["template_semantics"]["status"] in (
            "completed",
            "not_run",
        )
        assert any("Новый исходный текст" in f.text for f in package.content.facts)
        package.analysis = {"warnings": [], "template_cache": {"hit": True}}
        return package

    monkeypatch.setattr(pipeline, "prepare_intelligence", intelligence)
    pipeline.prepare(store, new["id"], "Новый исходный текст.", "", "", 1, settings)
    assert store.get(new["id"])["state"] == "ready", store.get(new["id"]).get("error")


def test_compose_one_slide_equals_full_variant_and_cache_copies(prepared, monkeypatch):
    from studio import composer
    from studio.planner import extractive_plans

    _, _, p = prepared
    variant = extractive_plans(p).variants[0]
    expected = composer.compose_variant(variant, p)
    assert [composer.compose_slide(variant, p, i) for i in range(len(variant.slides))] == expected
    cache = composer.CompositionSession(p)
    assert cache.variant(variant) == expected
    first = cache.slide(variant, 0)
    first.title = "mutated return value"
    assert cache.slide(variant, 0) == expected[0]
    before = cache.misses
    changed = variant.model_copy(deep=True)
    changed.slides[0].title = "Другой заголовок"
    cache.variant(changed)
    assert cache.misses == before + 1 and cache.hits >= len(variant.slides)


def test_stage_version_ignores_unrelated_application_changes(tmp_path, monkeypatch):
    from studio import cache_version

    monkeypatch.setattr(cache_version, "ROOT", tmp_path)
    (tmp_path / "studio").mkdir()
    (tmp_path / "prompts").mkdir()
    target = tmp_path / "studio/template_analysis.py"
    target.write_text("classifier v1")
    (tmp_path / "studio/app.py").write_text("server v1")
    initial = cache_version.stage_version("template_analyst")
    (tmp_path / "studio/app.py").write_text("server v2")
    assert cache_version.stage_version("template_analyst") == initial
    target.write_text("classifier v2")
    assert cache_version.stage_version("template_analyst") != initial


def test_editorial_truncation_switches_to_checkpointed_outline(monkeypatch, tmp_path):
    from studio.editorial import prepare_editorial
    from studio import narrative
    from studio.content import parse_content
    from studio.models import Constraints
    from studio.gateway import ModelResponseTruncated

    monkeypatch.setattr(
        narrative,
        "narrative_storyboard",
        lambda p: p.analysis.update(slide_budget={"status": "adjusted"}),
    )
    p = SimpleNamespace(
        content=parse_content("Один факт. Второй факт."),
        original_content=None,
        template=SimpleNamespace(patterns=[]),
        constraints=Constraints(slides=2, count_mode="exact", summarize=True),
        analysis={},
    )

    class Gateway:
        settings = SimpleNamespace(data_dir=tmp_path, model_id="test")
        calls = []
        stages = []

        async def json_request(self, stage, payload, **kwargs):
            self.stages.append(stage)
            if stage == "editorial":
                raise ModelResponseTruncated("too large")
            if stage == "editorial_outline":
                return {
                    "slides": [
                        {"title": "Обзор", "purpose": "cover", "fact_ids": ["f1"]},
                        {"title": "Детали", "purpose": "content", "fact_ids": ["f2"]},
                    ],
                    "omitted": [],
                }
            if stage == "editorial_slides":
                return {
                    "slides": [
                        {
                            "title": "Обзор",
                            "purpose": "cover",
                            "bullets": [{"text": "Один факт.", "evidence": [{"fact_id": "f1"}]}],
                        },
                        {
                            "title": "Детали",
                            "purpose": "content",
                            "bullets": [{"text": "Второй факт.", "evidence": [{"fact_id": "f2"}]}],
                        },
                    ]
                }
            assert stage == "editorial_review"
            return {
                "claims": [
                    {"claim_id": c["claim_id"], "supported": True, "meaning_preserved": True}
                    for c in payload["claims"]
                ],
                "narrative_coherent": True,
            }

    g = Gateway()
    assert asyncio.run(prepare_editorial(p, g))
    assert g.stages == ["editorial", "editorial_outline", "editorial_slides", "editorial_review"]
    assert len(p.analysis["editorial"]["plan"]["slides"]) == 2


def test_visual_repair_rechecks_only_changed_pixels(prepared, tmp_path):
    from studio.visual import review_visuals

    _, _, p = prepared
    folder = tmp_path / "executive"
    folder.mkdir()
    pattern = p.template.patterns[0]
    scenes = [
        {
            "title": "Title " + str(i),
            "layout": "columns",
            "purpose": "content",
            "elements": [],
            "pattern_id": pattern.id,
        }
        for i in range(3)
    ]
    (folder / "slides.json").write_text(json.dumps(scenes))
    for i in range(1, 4):
        (folder / f"slide-{i}.png").write_bytes(b"pixels" + bytes([i]))

    class Gateway:
        settings = SimpleNamespace(
            mode="api", visual_review=True, model_id="test", data_dir=tmp_path
        )
        requests = []

        async def json_request(self, stage, payload, **kw):
            self.requests.append(payload["image_order"])
            return {"checked_slides": payload["image_order"], "findings": []}

    g = Gateway()
    results = [{"key": "executive", "slides": 3, "rendering": {"native_render": True}}]
    first = asyncio.run(review_visuals(results, tmp_path, g, None, package=p))
    assert first["checked"] == 3
    g.requests = []
    (folder / "slide-2.png").write_bytes(b"changed pixels")
    second = asyncio.run(review_visuals(results, tmp_path, g, None, package=p))
    assert second["status"] == "completed" and second["checked"] == 3 and g.requests == [[2]]
    assert sum(b.get("cache_hit", False) for b in second["batches"]) == 2
