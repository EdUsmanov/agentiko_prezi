"""A selected repair consumes one sealed revision and publishes another."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from studio.presentation_service import ApplicationError, PresentationService
from studio.composition.composer import compose_variant
from studio.composition.contracts import candidates
from studio.generation.repair import run_repair
from studio.composition.layout_edits import LayoutEdit
from studio.contents.planner import assign_compositions, extractive_plans, validate_plans
from studio.checks.refinement import apply_edits
from studio.composition.render import render_variant
from studio.checks.review_snapshot import build_snapshot, load_snapshot, save_snapshot
from studio.jobs.runtime import JobRuntime


def test_selected_repair_preserves_source_and_compares_final_audits(prepared):
    settings, store, package = prepared
    plans = assign_compositions(validate_plans(extractive_plans(package), package), package)
    decks = {variant.key: compose_variant(variant, package) for variant in plans.variants}
    selected = None
    for variant in plans.variants:
        for index, plan in enumerate(variant.slides):
            current = decks[variant.key][index].pattern_id
            for pattern in candidates(package, plan, index):
                if pattern.id == current:
                    continue
                edit = LayoutEdit(variant=variant.key, slide=index + 1, pattern_id=pattern.id)
                try:
                    apply_edits(
                        package,
                        plans,
                        decks,
                        [edit],
                        {(variant.key, index + 1): [pattern.id]},
                    )
                except ValueError:
                    continue
                selected = (variant.key, index + 1)
                break
            if selected:
                break
        if selected:
            break
    assert selected is not None, "Fixture must offer one safe alternative layout"
    source = store.create("generation", {"package_id": package.id})
    source_root = store.directory(source["id"])
    (source_root / "plans.json").write_text(plans.model_dump_json(indent=2))
    variants = []
    for variant in plans.variants:
        rendering = render_variant(
            decks[variant.key],
            package.template,
            store.directory(package.id) / "input.pptx",
            source_root / variant.key,
        )
        variants.append(
            {
                "key": variant.key,
                "title": variant.title,
                "slides": len(variant.slides),
                "rendering": rendering,
                "initial_errors": 0,
            }
        )
    target = {
        "source": "variant",
        "variant": selected[0],
        "slide": selected[1],
        "code": "hierarchy",
        "severity": "warning",
        "message": "Selected layout concern",
    }
    persisted = {
        "source": "contextual_audit",
        "code": "audit_incomplete",
        "severity": "warning",
        "message": "Проверка не завершена: contextual_audit",
    }
    unsupported = {
        "source": "run",
        "code": "content_accuracy",
        "severity": "warning",
        "message": "Requires content revision",
    }
    manifest = {
        "variants": variants,
        "composition_diversity": {
            "policy": "test",
            "distinct": 3,
            "expected": 3,
            "verified": True,
            "adjustments": [],
            "signatures": {},
            "findings": [],
        },
        "engine": {"engine": "native", "status": "completed"},
        "planning_source": "extractive",
        "quality_report": {
            "status": "needs_review",
            "errors": 0,
            "warnings": 3,
            "findings": [target, persisted, unsupported],
            "export_completed": True,
            "manual_acceptance": "not_recorded",
        },
    }
    (source_root / "audit-input.json").write_text(json.dumps(manifest, ensure_ascii=False))
    snapshot = build_snapshot(store, source["id"], manifest, package, plans)
    selected_id = next(f.id for f in snapshot.findings if f.message == target["message"])
    unsupported_id = next(f.id for f in snapshot.findings if f.message == unsupported["message"])
    assert next(f for f in snapshot.findings if f.id == selected_id).action == "change_layout"
    assert next(f for f in snapshot.findings if f.id == unsupported_id).action is None
    audit_hash = save_snapshot(store, snapshot)
    store.update(source["id"], "needs_review", review_available=True, audit_hash=audit_hash)
    original_hashes = dict(snapshot.files)

    service = PresentationService(
        settings,
        store,
        JobRuntime(settings, store),
        version_operation=lambda: "fixed",
    )
    with pytest.raises(ApplicationError, match="нет безопасного исправления"):
        service.repair(source["id"], audit_hash=audit_hash, finding_ids=[unsupported_id])
    with store.connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM jobs WHERE kind='generation'").fetchone()[0]
            == 1
        )

    child, created = store.claim_repair(source["id"], audit_hash, [selected_id])
    assert created
    gateway = SimpleNamespace(settings=settings, calls=[], usage=[])
    asyncio.run(run_repair(store, child["id"], settings, gateway, package, {}, "test"))

    result = store.get(child["id"])
    assert result["state"] == "needs_review"
    comparison = result["quality_report"]["repair_comparison"]
    assert selected_id in comparison["selected_not_observed"]
    assert selected_id not in comparison["selected_still_present"]
    assert any(
        f.message == persisted["message"]
        for f in snapshot.findings
        if f.id in comparison["persisted"]
    )
    assert comparison["new"]
    assert load_snapshot(store, child["id"]).parent_generation_id == source["id"]
    assert load_snapshot(store, source["id"]).files == original_hashes
    child_root = store.directory(child["id"])
    for variant in plans.variants:
        if variant.key == selected[0]:
            assert (child_root / variant.key / "slides.json").read_bytes() != (
                source_root / variant.key / "slides.json"
            ).read_bytes()
            continue
        for name in ("deck.pptx", "deck.pdf", "deck.html", "slides.json", "slide-1.png"):
            assert (child_root / variant.key / name).read_bytes() == (
                source_root / variant.key / name
            ).read_bytes()
