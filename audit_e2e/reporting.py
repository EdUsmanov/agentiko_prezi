"""Run identity and a portable report, independent of the application's quality verdict."""

from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess

AUDIT_ROOT = Path(__file__).resolve().parent
ROOT = AUDIT_ROOT.parent
RESULTS = AUDIT_ROOT / "results"


def digest(value):
    return sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    temporary.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def source_snapshot(root=ROOT):
    root = Path(root)
    files = {}
    for name in (
        "studio",
        "prompts",
        "config",
        "web",
        "fonts",
        "test_support",
        "audit_e2e",
        "e2e",
        "scripts",
        "tests/fixtures",
    ):
        for path in sorted((root / name).rglob("*")):
            if (
                path.is_file()
                and not path.is_symlink()
                and not path.is_relative_to(root / "audit_e2e" / "results")
                and not any(
                    part.startswith(".") or part == "__pycache__"
                    for part in path.relative_to(root).parts
                )
            ):
                files[str(path.relative_to(root))] = sha256(path.read_bytes()).hexdigest()
    for pattern in (
        "requirements*.txt",
        "requirements.lock",
        "pyproject.toml",
        "Dockerfile*",
        ".github/workflows/*.yml",
    ):
        for path in sorted(root.glob(pattern)):
            files[str(path.relative_to(root))] = sha256(path.read_bytes()).hexdigest()
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True)
    return {"git_head": git.stdout.strip() or None, "tree_sha256": digest(files), "files": files}


def environment(*, timeout=15):
    from studio.composition.office import executable

    renderer = executable()
    version = "unavailable"
    if renderer:
        try:
            result = subprocess.run(
                [renderer, "--version"],
                capture_output=True,
                text=True,
                timeout=max(0.001, min(timeout, 15)),
            )
            version = result.stdout.strip()[:500]
        except (OSError, subprocess.TimeoutExpired):
            version = "unverified"
    packages = {}
    for name in ("python-pptx", "pypdfium2", "Pillow", "playwright", "pydantic"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "unavailable"
    codex = shutil.which("codex")
    codex_version = "unavailable"
    if codex:
        try:
            codex_version = subprocess.run(
                [codex, "--version"],
                capture_output=True,
                text=True,
                timeout=max(0.001, min(timeout, 5)),
            ).stdout.strip()[:300]
        except (OSError, subprocess.TimeoutExpired):
            codex_version = "unverified"
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "renderer": version,
        "codex_cli": codex_version,
        "packages": packages,
    }


def seal_bundle(directory):
    directory = Path(directory)
    if any(p.is_symlink() for p in directory.rglob("*")):
        raise ValueError("Evidence bundles must contain copied files, not symlinks")
    return {
        str(p.relative_to(directory)): sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def verify_bundle(directory, expected):
    if not expected or seal_bundle(directory) != expected:
        raise ValueError("Evidence bundle changed since generation; create a new run instead")


def aggregate_status(statuses):
    statuses = list(statuses)
    if "failed" in statuses:
        return "failed"
    return "passed" if statuses and all(s == "passed" for s in statuses) else "inconclusive"


def _anchor(*parts):
    return re.sub(r"[^a-zA-Z0-9_-]", "-", "-".join(map(str, parts)))


def _judgment_html(case, blind_mapping=None):
    judgment = case.get("judgment", case)
    blocks = []
    variants = [(name, name, result) for name, result in judgment.get("variants", {}).items()]
    for comparison in judgment.get("comparisons", []):
        variant = comparison["variant"]
        mapping = (blind_mapping or {}).get(variant)
        names = (
            {"A": "baseline", "B": "candidate"}
            if mapping == "baseline_A"
            else {"A": "candidate", "B": "baseline"}
            if mapping == "baseline_B"
            else {"A": "A", "B": "B"}
        )
        winners = {
            category: names.get(winner, winner)
            for category, winner in comparison.get("winners", {}).items()
        }
        blocks.append(
            f"<h3>{escape(variant)} · comparison</h3><p>{escape(json.dumps(winners))}</p>"
            f"<p>{escape(comparison.get('rationale', ''))}</p>"
        )
        variants.extend(
            (f"{names[side]} · {variant}", f"{names[side]}-{variant}", result)
            for side, result in comparison.get("sides", {}).items()
        )
    for variant, anchor_variant, result in variants:
        rows = []
        for finding in result.get("findings", []):
            number = finding.get("slide", finding.get("slide_number"))
            label = escape(str(number or "whole deck"))
            if number:
                label = (
                    f'<a href="#{_anchor(case.get("case_id"), anchor_variant, number)}">{label}</a>'
                )
            rows.append(
                f"<tr><td>{label}</td><td>{escape(str(finding.get('category', '')))}</td><td>{escape(json.dumps(finding, ensure_ascii=False))}</td></tr>"
            )
        points = escape(json.dumps(result.get("points", []), ensure_ascii=False, indent=2))
        blocks.append(
            f"<h3>{escape(variant)} · {escape(result.get('status', 'inconclusive'))}</h3><table><thead><tr><th>Slide</th><th>Category</th><th>Evidence</th></tr></thead><tbody>{''.join(rows)}</tbody></table><details><summary>Key point coverage</summary><pre>{points}</pre></details>"
        )
    return "".join(blocks)


def render_report(directory, document, *, filename="report.html"):
    directory = Path(directory)
    cards = []
    for case in document.get("cases", []):
        case_id = escape(str(case.get("case_id", "unknown")))
        details = escape(json.dumps(case, ensure_ascii=False, indent=2, default=str))
        images = []
        bundles = case.get("comparison_bundles", {}) or {"": case.get("bundle", "missing")}
        for side, relative_bundle in bundles.items():
            bundle_file = (directory / relative_bundle).resolve()
            if not bundle_file.is_relative_to(directory.resolve()):
                raise ValueError("Report evidence must stay within the report directory")
            if not bundle_file.is_file():
                continue
            bundle = json.loads(bundle_file.read_text())
            for variant, deck in bundle.get("variants", {}).items():
                label = (side + " · " if side else "") + variant
                anchor_variant = side + "-" + variant if side else variant
                for slide in deck.get("slides", []):
                    path = (bundle_file.parent / slide["image"]).resolve()
                    try:
                        relative = path.relative_to(directory.resolve()).as_posix()
                    except ValueError:
                        continue
                    images.append(
                        f'<figure id="{_anchor(case.get("case_id"), anchor_variant, slide["number"])}"><a href="{escape(relative, quote=True)}"><img loading="lazy" src="{escape(relative, quote=True)}" alt="{escape(label)} {slide["number"]}"></a><figcaption>{escape(label)} · {slide["number"]}</figcaption></figure>'
                    )
        judgment = _judgment_html(
            case, document.get("blind_mapping", {}).get(case.get("case_id"), {})
        )
        cards.append(
            f'<section><h2>{case_id}: {escape(case.get("status", "inconclusive"))}</h2>{judgment}<details><summary>Execution and independent findings</summary><pre>{details}</pre></details><div class="slides">{"".join(images)}</div></section>'
        )
    metadata = {k: v for k, v in document.items() if k != "cases"}
    body = escape(json.dumps(metadata, ensure_ascii=False, indent=2, default=str))
    notice = (
        "Calibration control status describes whether the expected behavior was detected: "
        "a passing damaged control means its defect was caught. It does not mean the "
        "presentation is correct or the product is certified. Design remains advisory."
        if "calibration_version" in document
        else "Execution, independent factual checks and model judgments are reported separately. "
        "Design is advisory. Replay does not establish live model quality."
    )
    html = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Presentation evaluation</title><style>body{{font:16px/1.5 system-ui;max-width:1300px;margin:30px auto;padding:0 20px;color:#182233;background:#f5f7fa}}section{{background:white;padding:20px;margin:20px 0;border-radius:10px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}.slides{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}figure{{margin:0}}img{{width:100%;height:auto}}figcaption{{font-size:13px}}</style><h1>Presentation evaluation: {escape(document.get("status", "inconclusive"))}</h1><p>{notice}</p><details><summary>Provenance and limits</summary><pre>{body}</pre></details>{"".join(cards)}</html>"""
    (directory / filename).write_text(html)
