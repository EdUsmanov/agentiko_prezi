import asyncio
import shutil
import time

import pytest

from studio.content import parse_content
from studio.storyboard import prepare_storyboard, planned_slide_count
from studio.planner import extractive_plans, validate_plans, planning_schema, assign_compositions


def table_text(count=4):
    return "# Отчёт\n" + "\n".join(
        f"## Раздел {i}\n| Категория | Значение |\n|---|---|\n| A | {i + 1} |" for i in range(count)
    )


def conflicting_package(package, count=4, mode="exact"):
    package.content = parse_content(table_text(count))
    package.constraints.slides = 4
    package.constraints.count_mode = mode
    package.analysis = {"warnings": [], "model_mode": "extractive", "planning_source": "extractive"}
    # A real structural role with valid geometry, independent of classifier output.
    cover = package.template.patterns[0].model_copy(deep=True)
    cover.id = "budget-cover"
    cover.role = cover.purpose = "cover"
    package.template.patterns.append(cover)
    return package


@pytest.mark.parametrize("mode", ["exact", "maximum", "default"])
def test_conflict_is_warning_and_all_facts_tables_survive(prepared, mode):
    from studio.composer import compose_variant
    from studio.audit import audit_scenes

    _, _, package = prepared
    conflicting_package(package, mode=mode)
    before = package.constraints.model_dump()
    prepare_storyboard(package)
    budget = package.analysis["slide_budget"]
    assert budget["status"] == "adjusted"
    assert budget["requested"] == 4 and budget["planned"] == 6
    assert package.constraints.model_dump() == before
    plans = assign_compositions(validate_plans(extractive_plans(package), package), package)
    assert planned_slide_count(package) == 6
    assert planning_schema(package)["$defs"]["VariantPlan"]["properties"]["slides"]["maxItems"] == 6
    for variant in plans.variants:
        assert [f for s in variant.slides for f in s.fact_ids] == [
            f.id for f in package.content.facts
        ]
        assert {s.table_id for s in variant.slides if s.table_id} == {
            t.id for t in package.content.tables
        }
        assert sum(s.layout == "divider" for s in variant.slides) == 1
        findings = audit_scenes(compose_variant(variant, package), package)
        assert not [f for f in findings if f.code in ("slide_count", "coverage")]
        assert any(f.code == "slide_count_adjusted" and f.severity == "warning" for f in findings)
    plans.variants[0].slides.pop()
    with pytest.raises(ValueError, match="количество"):
        validate_plans(plans, package)


def test_adjusted_budget_does_not_disable_image_coverage(prepared):
    from studio.composer import compose_variant
    from studio.audit import audit_scenes
    from studio.models import UploadedImage

    _, _, p = prepared
    conflicting_package(p)
    prepare_storyboard(p)
    plans = assign_compositions(validate_plans(extractive_plans(p), p), p)
    scenes = compose_variant(plans.variants[0], p)
    p.images = [
        UploadedImage(
            id="missing", name="asset.png", path="/unused.png", sha256="a" * 64, width=10, height=10
        )
    ]
    assert any(f.code == "image_coverage" for f in audit_scenes(scenes, p))


@pytest.mark.parametrize("requested", [1, 2, 4])
def test_unheaded_tables_do_not_fall_back_to_lossy_splitter(prepared, requested):
    _, _, p = prepared
    conflicting_package(p, count=6)
    p.constraints.slides = requested
    for fact in p.content.facts:
        fact.section = ""
    prepare_storyboard(p)
    plans = validate_plans(extractive_plans(p), p)
    assert p.analysis["slide_budget"]["status"] == "adjusted"
    assert all(len({s.table_id for s in v.slides if s.table_id}) == 6 for v in plans.variants)
    assert all(
        [f for s in v.slides for f in s.fact_ids] == [f.id for f in p.content.facts]
        for v in plans.variants
    )


def test_over_runtime_cap_keeps_analysis_without_unsafe_plan(prepared):
    _, _, p = prepared
    conflicting_package(p, count=31)
    prepare_storyboard(p)
    assert p.analysis["slide_budget"]["status"] == "needs_input"
    assert p.analysis["slide_budget"]["required"] > 30
    assert not p.analysis.get("storyboard")
    assert len(p.content.tables) == 31


@pytest.mark.parametrize("count", [4, 31])
def test_preparation_saves_package_and_warning_instead_of_failing(prepared, monkeypatch, count):
    from studio import pipeline

    settings, store, baseline = prepared

    async def intelligence(package, *args):
        conflicting_package(package, count)
        prepare_storyboard(package)
        blocked = package.analysis["slide_budget"]["status"] == "needs_input"
        package.prepared_plans = (
            None
            if blocked
            else assign_compositions(validate_plans(extractive_plans(package), package), package)
        )
        package.analysis.update(
            planning_status="needs_input" if blocked else "completed",
            planned_slides=None if blocked else planned_slide_count(package),
        )
        return package

    monkeypatch.setattr(pipeline, "prepare_intelligence", intelligence)
    job = store.create("preparation", {"template_name": "budget.pptx"})
    shutil.copyfile(
        store.directory(baseline.id) / "input.pptx", store.directory(job["id"]) / "input.pptx"
    )
    pipeline.prepare(store, job["id"], table_text(count), "", "", 4, settings)
    ready = store.get(job["id"])
    assert ready["state"] == "ready", ready
    assert "error" not in ready
    assert any("Запрошено 4" in x["message"] for x in ready["diagnostics"])
    p = pipeline.load_package(store, job["id"])
    assert p.constraints.slides == 4
    if count == 31:
        assert p.prepared_plans is None
        return
    run = store.create("generation", {"package_id": p.id, "deadline_at": time.time() + 300})
    asyncio.run(pipeline.generate(store, run["id"], settings))
    done = store.get(run["id"])
    assert done["state"] == "needs_review", done
    assert all(v["slides"] == 6 for v in done["variants"])
    assert any("Запрошено 4" in warning for warning in done["warnings"])


def test_api_requires_explicit_count_acceptance(prepared, monkeypatch):
    from fastapi.testclient import TestClient
    from studio import app as app_module

    settings, _, p = prepared
    conflicting_package(p)
    prepare_storyboard(p)
    monkeypatch.setattr(app_module, "load_package", lambda *_: p)
    with TestClient(app_module.create_app(settings)) as client:
        reply = client.post("/api/generate", json={"package_id": p.id})
        assert reply.status_code == 409
        assert "Подтвердите" in reply.json()["detail"]

        # Accepting the visible count gets past the API gate. Never spawn an OS
        # worker with this in-memory fixture; real exports are covered above.
        async def no_worker(*args, **kwargs):
            raise RuntimeError("test: OS worker disabled")

        monkeypatch.setattr(app_module.asyncio, "create_subprocess_exec", no_worker)
        accepted = client.post(
            "/api/generate", json={"package_id": p.id, "accept_adjusted_slide_count": True}
        )
        assert accepted.status_code == 202, accepted.text
        p.analysis["slide_budget"]["status"] = "needs_input"
        reply = client.post(
            "/api/generate", json={"package_id": p.id, "accept_adjusted_slide_count": True}
        )
        assert reply.status_code == 409
