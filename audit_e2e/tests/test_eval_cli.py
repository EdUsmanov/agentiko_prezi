"""Experiment identity and failure accounting must never manufacture a green run."""

import json
from types import ModuleType
import sys

import pytest

from audit_e2e import cli
from audit_e2e.reporting import (
    aggregate_status,
    render_report,
    seal_bundle,
    source_snapshot,
    verify_bundle,
)


def test_source_snapshot_includes_uncommitted_code_and_excludes_keys(tmp_path):
    (tmp_path / "studio").mkdir()
    source = tmp_path / "studio/code.py"
    source.write_text("VERSION = 1\n")
    (tmp_path / ".env").write_text("API_KEY=do-not-copy")
    before = source_snapshot(tmp_path)
    assert ".env" not in before["files"]
    source.write_text("VERSION = 2\n")
    assert source_snapshot(tmp_path)["tree_sha256"] != before["tree_sha256"]


def test_source_snapshot_tracks_audit_inputs_but_not_generated_results(tmp_path):
    audit = tmp_path / "audit_e2e"
    for name in ("cli.py", "prompts/judge.md", "fixtures/cases.json", "Dockerfile"):
        path = audit / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("version 1")
    before = source_snapshot(tmp_path)
    assert len(before["files"]) == 4
    results = audit / "results"
    results.mkdir()
    (results / "run.json").write_text("generated output")
    assert source_snapshot(tmp_path)["tree_sha256"] == before["tree_sha256"]
    (audit / "prompts/judge.md").write_text("version 2")
    assert source_snapshot(tmp_path)["tree_sha256"] != before["tree_sha256"]


def test_changed_or_missing_evidence_cannot_be_rejudged(tmp_path):
    (tmp_path / "bundle.json").write_text('{"case_id":"x"}')
    (tmp_path / "slide.png").write_bytes(b"first")
    expected = seal_bundle(tmp_path)
    verify_bundle(tmp_path, expected)
    (tmp_path / "slide.png").write_bytes(b"other")
    with pytest.raises(ValueError, match="changed"):
        verify_bundle(tmp_path, expected)
    (tmp_path / "slide.png").unlink()
    with pytest.raises(ValueError, match="changed"):
        verify_bundle(tmp_path, expected)


@pytest.mark.parametrize(
    "values,expected",
    [
        ([], "inconclusive"),
        (["passed", "inconclusive"], "inconclusive"),
        (["passed", "failed"], "failed"),
        (["passed", "passed"], "passed"),
    ],
)
def test_aggregate_never_hides_incomplete_or_failed_cases(values, expected):
    assert aggregate_status(values) == expected


def test_rejudge_can_resolve_old_inconclusive_without_erasing_execution_failure(
    tmp_path, monkeypatch
):
    bundle = tmp_path / "case"
    bundle.mkdir()
    (bundle / "bundle.json").write_text("{}")
    module = ModuleType("audit_e2e.judge")
    module.judge_cases = lambda *args, **kwargs: {
        "status": "passed",
        "usage": {},
        "cases": [{"case_id": "a", "status": "passed"}],
    }
    monkeypatch.setitem(sys.modules, module.__name__, module)
    document = {
        "status": "inconclusive",
        "cases": [
            {
                "case_id": "a",
                "bundle": "case/bundle.json",
                "artifact_sha256": seal_bundle(bundle),
                "execution_status": "passed",
                "deterministic": {"status": "passed"},
            }
        ],
    }
    cli._judge(tmp_path, document, timeout=1, calibration=None)
    assert document["status"] == "passed"
    document["cases"][0]["deterministic"]["status"] = "failed"
    cli._judge(tmp_path, document, timeout=1, calibration=None)
    assert document["status"] == "failed"


def test_run_lookup_and_evidence_paths_cannot_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "bundle.json").write_text("{}")
    run = tmp_path / "run"
    run.mkdir()
    doc = {
        "schema_version": 1,
        "cases": [{"bundle": "../outside/bundle.json", "artifact_sha256": seal_bundle(outside)}],
    }
    (run / "run.json").write_text(json.dumps(doc))
    directory, parsed = cli.read_run(str(run))
    assert directory == run
    with pytest.raises(ValueError, match="within"):
        cli.bundle_paths(directory, parsed)


def test_limits_are_positive_and_live_cli_documents_required_limits():
    for name in ("--max-requests", "--timeout"):
        with pytest.raises(SystemExit):
            cli.parser().parse_args(["run", "--mode", "live", name, "0"])
    parsed = cli.parser().parse_args(
        ["run", "--mode", "live", "--max-requests", "10", "--timeout", "60"]
    )
    assert parsed.max_requests == 10 and parsed.timeout == 60


@pytest.mark.parametrize("changed", [False, True])
def test_comparison_records_input_and_evaluator_versions_and_rejects_source_drift(
    tmp_path, monkeypatch, changed
):
    from audit_e2e import judge

    snapshots = iter([{"tree_sha256": "before"}, {"tree_sha256": "after" if changed else "before"}])
    monkeypatch.setattr(cli, "source_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(cli, "environment", lambda: {"renderer": "pinned-test-renderer"})
    monkeypatch.setattr(
        cli,
        "read_run",
        lambda side: (
            tmp_path,
            {"id": side, "source_snapshot": {"tree_sha256": side}, "scope": "saved_exports"},
        ),
    )
    monkeypatch.setattr(cli, "bundle_paths", lambda *args: [])
    monkeypatch.setattr(
        judge, "compare_cases", lambda *args, **kwargs: {"status": "passed", "cases": []}
    )
    output = tmp_path / "comparison"
    result = cli.main(
        [
            "compare",
            "--baseline",
            "baseline",
            "--candidate",
            "candidate",
            "--timeout",
            "30",
            "--output",
            str(output),
        ]
    )
    document = json.loads((output / "comparison.json").read_text())
    assert result == (2 if changed else 0)
    assert document["evaluation_source_unchanged"] is (not changed)
    assert document["environment"]["renderer"] == "pinned-test-renderer"
    assert document["input_runs"]["baseline"]["source_snapshot"]["tree_sha256"] == "baseline"
    assert document["input_runs"]["candidate"]["source_snapshot"]["tree_sha256"] == "candidate"


def test_generator_usage_keeps_missing_usage_unknown_and_reports_partial_totals():
    cases = [
        {
            "execution": {
                "model_requests": 2,
                "generator_usage": {
                    "reported_requests": 1,
                    "input_tokens": 12,
                    "output_tokens": 4,
                },
            }
        }
    ]
    usage = cli.generator_usage(cases)
    assert usage["status"] == "partial"
    assert usage["physical_requests"] == 2
    assert usage["input_tokens"] == 12 and usage["output_tokens"] == 4
    assert usage["cached_input_tokens"] is None
    assert cli.generator_usage([])["input_tokens"] is None


@pytest.mark.parametrize(
    "counts,exit_code", [({"missed": 1}, 1), ({"inconclusive": 1}, 2), ({"clean": 1}, 0)]
)
def test_native_control_build_exit_does_not_hide_detector_failures(
    tmp_path, monkeypatch, counts, exit_code
):
    module = ModuleType("audit_e2e.defect_corpus")
    module.build_defect_corpus = lambda *args, **kwargs: {
        "manifest_path": str(tmp_path / "manifest.json"),
        "labels_path": str(tmp_path / "labels.json"),
        "validation_path": str(tmp_path / "validation.json"),
        "counts": {"total_cases": 1},
        "matrix": {"counts": counts},
    }
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(cli, "environment", lambda: {})
    monkeypatch.setattr(cli, "source_snapshot", lambda: {"tree_sha256": "stable"})
    assert cli.main(["build-defects", "--output", str(tmp_path), "--timeout", "10"]) == exit_code


def test_report_rejects_comparison_evidence_outside_run(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("not JSON: must reject path before reading")
    report = tmp_path / "report"
    report.mkdir()
    with pytest.raises(ValueError, match="within"):
        render_report(
            report,
            {"cases": [{"comparison_bundles": {"baseline": "../outside.json"}}]},
        )


def test_comparison_report_decodes_blind_sides_and_links_findings(tmp_path):
    render_report(
        tmp_path,
        {
            "blind_mapping": {"case": {"story": "baseline_B"}},
            "cases": [
                {
                    "case_id": "case",
                    "comparisons": [
                        {
                            "variant": "story",
                            "winners": {"design": "A"},
                            "rationale": "<script>untrusted</script>",
                            "sides": {
                                "A": {"findings": [{"category": "readability", "slide_number": 2}]}
                            },
                        }
                    ],
                }
            ],
        },
    )
    html = (tmp_path / "report.html").read_text()
    assert "candidate · story" in html
    assert "#case-candidate-story-2" in html
    assert "&quot;design&quot;: &quot;candidate&quot;" in html
    assert "<script>untrusted</script>" not in html


def test_source_drift_does_not_erase_recorded_case_failure(tmp_path, monkeypatch):
    from audit_e2e import corpus, runtime

    case = {"id": "binghamton-content"}
    monkeypatch.setattr(corpus, "load_cases", lambda suite: [case])
    monkeypatch.setattr(corpus, "materialize_case", lambda *a, **kw: dict(case))
    monkeypatch.setattr(runtime, "execute_case", lambda *a, **kw: {"status": "failed"})
    monkeypatch.setattr(cli, "environment", lambda **kw: {})
    snapshots = iter([{"tree_sha256": value, "files": {"x": value}} for value in ("a", "b", "b")])
    monkeypatch.setattr(cli, "source_snapshot", lambda: next(snapshots))
    args = cli.parser().parse_args(["run", "--mode", "replay", "--output", str(tmp_path / "run")])
    directory, document = cli.run_experiment(args)
    assert document["status"] == "failed"
    assert document["source_unchanged"] is False
    assert json.loads((directory / "run.json").read_text())["status"] == "failed"


def test_elapsed_run_budget_cannot_produce_a_passing_result(tmp_path, monkeypatch):
    from contextlib import nullcontext

    from audit_e2e import corpus, evidence, runtime

    clock = [0.0]
    case = {"id": "binghamton-content"}

    def audit(*args):
        clock[0] = 2.0
        return {"status": "passed"}

    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(corpus, "load_cases", lambda suite: [case])
    monkeypatch.setattr(corpus, "materialize_case", lambda *a, **kw: dict(case))
    monkeypatch.setattr(runtime, "execute_case", lambda *a, **kw: {"status": "passed"})
    monkeypatch.setattr(runtime, "_hard_deadline", lambda seconds: nullcontext())
    monkeypatch.setattr(evidence, "build_bundle", lambda *a: {})
    monkeypatch.setattr(evidence, "audit_bundle", audit)
    monkeypatch.setattr(cli, "seal_bundle", lambda path: {})
    monkeypatch.setattr(cli, "environment", lambda **kw: {})
    monkeypatch.setattr(cli, "source_snapshot", lambda: {"tree_sha256": "same", "files": {}})
    args = cli.parser().parse_args(
        ["run", "--mode", "replay", "--timeout", "1", "--output", str(tmp_path / "run")]
    )
    _, document = cli.run_experiment(args)
    assert document["cases"][0]["status"] == "passed"
    assert document["status"] == "inconclusive"
    assert document["deadline_exhausted"] is True
