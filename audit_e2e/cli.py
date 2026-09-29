"""Bounded experiments; existing production HTTP paths and independent exported evidence."""

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

from .reporting import (
    AUDIT_ROOT,
    RESULTS,
    aggregate_status,
    environment,
    now,
    render_report,
    seal_bundle,
    source_snapshot,
    verify_bundle,
    write_json,
)

RUNS = RESULTS / "evaluations"
EXIT_CODES = {"passed": 0, "failed": 1, "inconclusive": 2}


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def read_run(value):
    path = Path(value)
    if not path.exists():
        path = RUNS / value
    if path.is_dir():
        path = path / "run.json"
    document = json.loads(path.read_text())
    if document.get("schema_version") != 1 or not document.get("cases"):
        raise ValueError("Not a versioned evaluation run with cases")
    return path.parent.resolve(), document


def bundle_paths(directory, document):
    paths = []
    for row in document["cases"]:
        if not row.get("bundle"):
            continue
        path = (directory / row["bundle"]).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Evidence must stay within the run directory")
        verify_bundle(path.parent, row.get("artifact_sha256"))
        paths.append(path)
    if not paths:
        raise ValueError("No complete evidence bundles to judge")
    return paths


def _safe_error(exc, secret=""):
    message = str(exc)
    if secret:
        message = message.replace(secret, "[redacted]")
    return {"type": type(exc).__name__, "message": message[:2000]}


def generator_usage(cases):
    executions = [row.get("execution", {}) for row in cases]
    physical = sum(row.get("model_requests", 0) for row in executions)
    usages = [row.get("generator_usage", {}) for row in executions]
    reported = sum(row.get("reported_requests", 0) for row in usages)
    totals = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
    ):
        values = [row[key] for row in usages if isinstance(row.get(key), (int, float))]
        totals[key] = sum(values) if values else None
    return {
        "physical_requests": physical,
        "reported_requests": reported,
        "status": "reported"
        if physical and physical == reported
        else "partial"
        if reported
        else "unavailable",
        **totals,
    }


def run_experiment(args):
    started = time.monotonic()
    timeout = args.timeout or 1800
    deadline = started + timeout
    from .corpus import load_cases, materialize_case
    from .evidence import audit_bundle, build_bundle
    from .runtime import EvaluationDeadline, _hard_deadline, execute_case

    if args.mode == "live" and (args.max_requests is None or args.timeout is None):
        raise ValueError("live requires --max-requests and --timeout (total run wall seconds)")
    cases = load_cases(args.suite)
    from .presentation_diversity import VERSION as presentation_policy

    for case in cases:
        case["presentation_diversity_policy"] = presentation_policy
        case["export_consistency_policy"] = "source-anchor-format-agreement-1"
    if args.case:
        requested = set(args.case)
        cases = [case for case in cases if case["id"] in requested]
        if {case["id"] for case in cases} != requested:
            raise ValueError("Unknown case in selected suite")
    directory = (
        args.output.resolve()
        if args.output
        else RUNS / (now().replace(":", "").replace("+", "-") + "-" + uuid4().hex[:8])
    )
    settings = None
    if args.mode == "live":
        from studio.config import Settings
        from studio.providers.gateway import validate_model_policy

        settings = Settings.from_env()
        validate_model_policy(settings)
        if settings.mode != "api":
            raise ValueError("live requires STUDIO_MODEL_MODE=api; extractive is not a fallback")
        settings = replace(settings, execution_kind="live", download_fonts=False)
    directory.mkdir(parents=True, exist_ok=False)
    before = source_snapshot()
    document = {
        "schema_version": 1,
        "id": directory.name,
        "started_at": now(),
        "mode": args.mode,
        "suite": args.suite,
        "selected_cases": [c["id"] for c in cases],
        "scope": "generation_only" if args.generation_only else "end_to_end",
        "status": "inconclusive",
        "quality_status": "not_evaluated",
        "source_snapshot": before,
        "environment": environment(timeout=deadline - time.monotonic()),
        "limits": {"max_model_requests": args.max_requests, "timeout_seconds": timeout},
        "cases": [],
        "generator_usage": {"physical_requests": 0},
        "judge_usage": {},
    }
    document["generator_config"] = (
        {
            key: value
            for key, value in asdict(settings).items()
            if key not in {"api_key", "data_dir"}
        }
        if settings
        else {"profile": "strict_http_replay"}
    )
    write_json(directory / "run.json", document)
    used_requests = 0
    try:
        # Resolve and hash the whole suite before the first paid request.
        prepared = []
        for case in cases:
            case_dir = directory / case["id"]
            with _hard_deadline(deadline - time.monotonic()):
                materialized = materialize_case(
                    case,
                    case_dir / "input",
                    synthetic=args.mode == "replay",
                    corpus_root=args.corpus_root,
                )
            if args.mode == "replay":
                cassette = AUDIT_ROOT / "fixtures/cassettes" / (case["id"] + ".json")
                if not cassette.is_file():
                    raise ValueError(
                        f"Missing reviewed replay cassette: {case['id']}; no model fallback"
                    )
                fixture = json.loads(cassette.read_text())
                if (
                    not isinstance(fixture.get("fixture_revision"), int)
                    or fixture["fixture_revision"] < 1
                ):
                    raise ValueError(f"Replay cassette has no explicit revision: {case['id']}")
                materialized["cassette"] = cassette
            prepared.append((materialized, case_dir))
        for case, case_dir in prepared:
            row = {
                "case_id": case["id"],
                "status": "inconclusive",
                "execution_status": "inconclusive",
                "interface": case.get("interface", "http"),
            }
            document["cases"].append(row)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (
                args.max_requests is not None and used_requests >= args.max_requests
            ):
                row["error"] = {
                    "type": "BudgetExhausted",
                    "message": "Run limits exhausted before this case",
                }
                continue
            try:
                result = execute_case(
                    case,
                    case_dir / "result",
                    mode=args.mode,
                    settings=settings,
                    max_requests=None
                    if args.max_requests is None
                    else args.max_requests - used_requests,
                    timeout=remaining,
                )
                row["execution"] = result
                row["execution_status"] = result.get("status", "inconclusive")
                count = result.get("model_requests", 0)
                used_requests += count
                document["generator_usage"] = generator_usage(document["cases"])
                if row["execution_status"] == "passed":
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Run limit exhausted before independent export checks")
                    bundle_dir = case_dir / "bundle"
                    with _hard_deadline(remaining):
                        bundle = build_bundle(case, case_dir / "result", bundle_dir)
                        hashes = seal_bundle(bundle_dir)
                        audit = audit_bundle(bundle, bundle_dir)
                    row["bundle"] = str((bundle_dir / "bundle.json").relative_to(directory))
                    row["artifact_sha256"] = hashes
                    row["deterministic"] = audit
                    row["status"] = row["deterministic"]["status"]
                else:
                    row["status"] = row["execution_status"]
            except (Exception, EvaluationDeadline) as exc:
                row["error"] = _safe_error(exc, settings.api_key if settings else "")
            write_json(directory / "run.json", document)
        after = source_snapshot()
        document["source_unchanged"] = before["tree_sha256"] == after["tree_sha256"]
        document["status"] = aggregate_status(row["status"] for row in document["cases"])
        if not document["source_unchanged"]:
            document["execution_error"] = "Source tree changed during experiment"
            document["status"] = aggregate_status([document["status"], "inconclusive"])
        if (
            args.mode == "live"
            and not args.generation_only
            and any(row.get("bundle") for row in document["cases"])
        ):
            judge_timeout = deadline - time.monotonic()
            if judge_timeout > 0:
                _judge(directory, document, timeout=judge_timeout, calibration=args.calibration)
            else:
                document["quality_status"] = "inconclusive"
                if document["status"] != "failed":
                    document["status"] = "inconclusive"
    except (Exception, EvaluationDeadline) as exc:
        document["error"] = _safe_error(exc, settings.api_key if settings else "")
        document["status"] = aggregate_status(
            ["inconclusive", *(row["status"] for row in document["cases"])]
        )
    finally:
        final_snapshot = source_snapshot()
        document["source_unchanged"] = before["tree_sha256"] == final_snapshot["tree_sha256"]
        if not document["source_unchanged"]:
            document["execution_error"] = "Source tree changed during experiment"
            document["changed_source_files"] = sorted(
                key
                for key in before["files"].keys() | final_snapshot["files"].keys()
                if before["files"].get(key) != final_snapshot["files"].get(key)
            )
            document["status"] = aggregate_status([document["status"], "inconclusive"])
        document["completed_at"] = now()
        document["elapsed_seconds"] = round(time.monotonic() - started, 3)
        document["deadline_exhausted"] = time.monotonic() >= deadline
        if document["deadline_exhausted"]:
            document["status"] = aggregate_status([document["status"], "inconclusive"])
            document["execution_error"] = "Run time limit exhausted"
        write_json(directory / "run.json", document)
        render_report(directory, document)
    return directory, document


def _judge(directory, document, *, timeout, calibration):
    from .judge import judge_cases

    paths = bundle_paths(directory, document)
    output = directory / ("judgment-" + uuid4().hex[:8])
    document["evaluation_snapshot"] = source_snapshot()
    certificate = json.loads(Path(calibration).read_text()) if calibration else None
    result = judge_cases(paths, output, timeout=timeout, calibration=certificate)
    document["judgment"] = str((output / "report.json").relative_to(directory))
    write_json(output / "report.json", result)
    document["judge_usage"] = result.get("usage", {})
    document["quality_status"] = result.get("status", "inconclusive")
    document["evaluation_source_unchanged"] = (
        document["evaluation_snapshot"]["tree_sha256"] == source_snapshot()["tree_sha256"]
    )
    if not document["evaluation_source_unchanged"]:
        document["quality_status"] = "inconclusive"
    by_id = {case["case_id"]: case for case in result.get("cases", [])}
    for row in document["cases"]:
        row["judgment"] = by_id.get(
            row["case_id"], {"status": "inconclusive", "reason": "not_judged"}
        )
        row["status"] = aggregate_status(
            [
                row.get("deterministic", {}).get(
                    "status", row.get("execution_status", "inconclusive")
                ),
                row["judgment"].get("status", "inconclusive"),
            ]
        )
    # A fresh judge may resolve an earlier inconclusive evaluation, but never an
    # execution failure, changed source tree or failed deterministic assertion.
    execution = aggregate_status(
        row.get("deterministic", {}).get("status", row.get("execution_status", "inconclusive"))
        for row in document["cases"]
    )
    if document.get("source_unchanged") is False:
        execution = aggregate_status([execution, "inconclusive"])
    document["status"] = aggregate_status(
        [
            execution,
            document["quality_status"],
            aggregate_status(row["status"] for row in document["cases"]),
        ]
    )
    return result


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="One bounded replay or live experiment")
    run.add_argument("--mode", choices=("replay", "live"), required=True)
    run.add_argument("--suite", choices=("core", "extended", "development"), default="core")
    run.add_argument(
        "--case", action="append", help="Explicit subset; never reported as the full suite"
    )
    run.add_argument("--output", type=Path)
    run.add_argument("--corpus-root", type=Path)
    run.add_argument("--max-requests", type=positive)
    run.add_argument("--timeout", type=positive)
    run.add_argument("--calibration", type=Path)
    run.add_argument(
        "--generation-only", action="store_true", help="Save exports without a model quality claim"
    )
    judge = commands.add_parser("judge", help="Re-evaluate sealed exports without regenerating")
    judge.add_argument("--run", required=True)
    judge.add_argument("--timeout", type=positive, required=True)
    judge.add_argument("--calibration", type=Path)
    compare = commands.add_parser("compare", help="Blind paired design review; advisory")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--timeout", type=positive, required=True)
    compare.add_argument("--output", type=Path, required=True)
    calibration = commands.add_parser(
        "calibrate", help="Three passes over independent clean and mutated controls"
    )
    calibration.add_argument("--output", type=Path, required=True)
    calibration.add_argument("--timeout", type=positive, required=True)
    calibration.add_argument(
        "--controls-only", action="store_true", help="Build fixtures with no LLM calls"
    )
    inventory = commands.add_parser("inventory", help="Inspect source and native template coverage")
    inventory.add_argument("--output", type=Path)
    defects = commands.add_parser(
        "build-defects", help="Build native bad-export controls and measure deterministic detection"
    )
    defects.add_argument("--output", type=Path, required=True)
    defects.add_argument("--category", action="append")
    defects.add_argument("--timeout", type=positive, required=True)
    challenge = commands.add_parser(
        "challenge-status", help="Validate a sealed private corpus; aggregate output only"
    )
    challenge.add_argument("--root", type=Path, required=True)
    challenge.add_argument("--identity", help="Previously recorded seal identity")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "challenge-status":
            from .challenge import aggregate_status as challenge_status

            document = challenge_status(args.root, expected_manifest_identity=args.identity)
            print(json.dumps(document, ensure_ascii=False))
            return 0 if document.get("status") == "ready" else 2
        elif args.command == "build-defects":
            from .defect_corpus import build_defect_corpus
            from .runtime import _hard_deadline

            build_source = source_snapshot()
            build_environment = environment()
            with _hard_deadline(args.timeout):
                document = build_defect_corpus(
                    args.output.resolve(), categories=args.category, validate=True
                )
            outcomes = document["matrix"].get("counts", {})
            detector_status = (
                "failed"
                if any(
                    outcomes.get(key, 0) for key in ("missed", "false_positive", "false_success")
                )
                else "inconclusive"
                if not outcomes or outcomes.get("inconclusive", 0)
                else "passed"
            )
            identity = {
                "scope": "native_control_build_no_model_calls",
                "source_snapshot": build_source,
                "source_unchanged": build_source["tree_sha256"] == source_snapshot()["tree_sha256"],
                "environment": build_environment,
                "manifest_path": document["manifest_path"],
                "counts": document["counts"],
                "detector_status": detector_status,
            }
            if not identity["source_unchanged"]:
                detector_status = aggregate_status([detector_status, "inconclusive"])
            identity["status"] = detector_status
            write_json(args.output / "build.json", identity)
            print(
                json.dumps(
                    {
                        "build_status": "completed",
                        "detector_status": detector_status,
                        **{
                            key: document[key]
                            for key in ("manifest_path", "labels_path", "validation_path", "counts")
                        },
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            return EXIT_CODES[detector_status]
        elif args.command == "inventory":
            from .development_inventory import inventory_report

            document = inventory_report()
            if args.output:
                write_json(args.output, document)
            print(json.dumps(document, ensure_ascii=False, indent=2))
            return 0
        elif args.command == "run":
            directory, document = run_experiment(args)
        elif args.command == "judge":
            directory, original = read_run(args.run)
            # Preserve historical verdict; each reevaluation is a distinct report.
            document = json.loads(json.dumps(original))
            _judge(directory, document, timeout=args.timeout, calibration=args.calibration)
            output = directory / ("reevaluation-" + uuid4().hex[:8] + ".json")
            write_json(output, document)
            render_report(directory, document, filename=output.with_suffix(".html").name)
        elif args.command == "compare":
            from .judge import compare_cases

            baseline_dir, baseline = read_run(args.baseline)
            candidate_dir, candidate = read_run(args.candidate)
            directory = args.output.resolve()
            directory.mkdir(parents=True, exist_ok=False)
            baseline_paths = bundle_paths(baseline_dir, baseline)
            candidate_paths = bundle_paths(candidate_dir, candidate)
            evaluation_snapshot = source_snapshot()
            document = compare_cases(
                baseline_paths,
                candidate_paths,
                directory,
                timeout=args.timeout,
            )
            document["evaluation_snapshot"] = evaluation_snapshot
            document["evaluation_source_unchanged"] = (
                evaluation_snapshot["tree_sha256"] == source_snapshot()["tree_sha256"]
            )
            document["environment"] = environment()
            document["input_runs"] = {
                side: {
                    key: original.get(key)
                    for key in (
                        "id",
                        "mode",
                        "scope",
                        "source_snapshot",
                        "source_unchanged",
                        "environment",
                    )
                }
                for side, original in (("baseline", baseline), ("candidate", candidate))
            }
            if not document["evaluation_source_unchanged"]:
                document["execution_error"] = "Source tree changed during comparison"
                document["status"] = aggregate_status([document["status"], "inconclusive"])
            rows = {row["case_id"]: row for row in document.get("cases", [])}
            for side, paths in (("baseline", baseline_paths), ("candidate", candidate_paths)):
                for path in paths:
                    case_id = json.loads(path.read_text())["case_id"]
                    if case_id in rows:
                        destination = directory / "evidence" / side / case_id
                        shutil.copytree(path.parent, destination)
                        rows[case_id].setdefault("comparison_bundles", {})[side] = str(
                            (destination / "bundle.json").relative_to(directory)
                        )
            write_json(directory / "comparison.json", document)
            render_report(directory, document)
        else:
            from .controls import build_controls
            from .judge import calibrate

            directory = args.output.resolve()
            directory.mkdir(parents=True, exist_ok=False)
            calibration_source = source_snapshot()
            calibration_environment = environment()
            controls = build_controls(directory / "controls")
            write_json(directory / "controls.json", controls)
            document = (
                {"status": "inconclusive", "reason": "controls_only_no_judge", "controls": controls}
                if args.controls_only
                else calibrate(controls, directory / "judge", repeats=3, timeout=args.timeout)
            )
            outcomes = {row["id"]: row for row in document.get("control_results", [])}
            document["cases"] = [
                {
                    "case_id": control["id"],
                    "status": outcomes.get(control["id"], {}).get("status", "inconclusive"),
                    "category": control["category"],
                    "expected": control["expected"],
                    "bundle": str(Path(control["bundle"]).relative_to(directory)),
                }
                for control in controls
            ]
            document["environment"] = calibration_environment
            document["source_snapshot"] = calibration_source
            document["source_unchanged"] = (
                calibration_source["tree_sha256"] == source_snapshot()["tree_sha256"]
            )
            if not document["source_unchanged"]:
                document["status"] = aggregate_status([document["status"], "inconclusive"])
            write_json(directory / "calibration.json", document)
            render_report(directory, document)
        print(
            json.dumps(
                {"status": document.get("status", "inconclusive"), "artifacts": str(directory)},
                ensure_ascii=False,
            )
        )
        return EXIT_CODES.get(document.get("status"), 2)
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "inconclusive", "error": _safe_error(exc)}, ensure_ascii=False))
        return 2
