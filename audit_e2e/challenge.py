"""Fail-closed aggregate validation for a private presentation challenge."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import posixpath
import re
import stat
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

SCHEMA_VERSION = 1
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_ASSETS = {"source", "reference", "template"}
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_file(root: Path, rel: str) -> Path | None:
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        return None
    parts = Path(rel).parts
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    path = root
    for part in parts:
        path = path / part
        try:
            mode = path.lstat().st_mode
        except OSError:
            return None
        if stat.S_ISLNK(mode):
            return None
    try:
        if not path.is_file() or path.resolve().parent != path.parent.resolve():
            return None
    except OSError:
        return None
    return path


def _pptx_features(path: Path) -> tuple[bool, bool] | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            xml_roots = {
                name: ElementTree.fromstring(archive.read(name))
                for name in names
                if name.endswith(".xml") or name.endswith(".rels")
            }
            package_rels = "http://schemas.openxmlformats.org/package/2006/relationships"
            diagram = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
            rel_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

            def relationship_map(part_name: str) -> dict[str, tuple[str, str]]:
                directory, base = posixpath.split(part_name)
                rels_name = posixpath.join(directory, "_rels", base + ".rels")
                rels_root = xml_roots.get(rels_name)
                if rels_root is None:
                    return {}
                return {
                    item.get("Id", ""): (item.get("Type", ""), item.get("Target", ""))
                    for item in rels_root.findall(f"{{{package_rels}}}Relationship")
                }

            def target_part(part_name: str, target: str) -> str:
                return posixpath.normpath(posixpath.join(posixpath.dirname(part_name), target))

            has_ole = False
            for slide_name, slide_root in xml_roots.items():
                if not slide_name.startswith("ppt/slides/slide") or not slide_name.endswith(".xml"):
                    continue
                rels = relationship_map(slide_name)
                for ole in slide_root.iter():
                    if ole.tag.rsplit("}", 1)[-1] != "oleObj":
                        continue
                    rel_id = ole.get(rel_attr + "id")
                    rel = rels.get(rel_id or "")
                    if rel and rel[0].endswith("/oleObject"):
                        part = target_part(slide_name, rel[1])
                        if part.startswith("ppt/embeddings/") and part in names:
                            has_ole = True

            diagram_data = [
                name
                for name, root in xml_roots.items()
                if name.startswith("ppt/diagrams/data") and root.tag.endswith("dataModel")
            ]
            slide_rel_ids = any(
                slide_name.startswith("ppt/slides/slide")
                and slide_name.endswith(".xml")
                and any(element.tag == f"{{{diagram}}}relIds" for element in slide_root.iter())
                for slide_name, slide_root in xml_roots.items()
            )
            slide_diagram_rels = any(
                name.startswith("ppt/slides/_rels/slide")
                and all(
                    part in archive.read(name)
                    for part in (
                        b"diagramData",
                        b"diagramLayout",
                        b"diagramQuickStyle",
                        b"diagramColors",
                    )
                )
                for name in names
                if name.endswith(".rels")
            )
            data_diagram_rels = any(
                name.startswith("ppt/diagrams/_rels/data")
                and all(
                    part in archive.read(name)
                    for part in (b"diagramLayout", b"diagramQuickStyle", b"diagramColors")
                )
                for name in names
                if name.endswith(".rels")
            )
            layout_parts = any(
                root.tag.endswith("layoutDef")
                for name, root in xml_roots.items()
                if name.startswith("ppt/diagrams/")
            )
            style_parts = any(
                root.tag.endswith("styleDef")
                for name, root in xml_roots.items()
                if name.startswith("ppt/diagrams/")
            )
            color_parts = any(
                root.tag.endswith("colorsDef")
                for name, root in xml_roots.items()
                if name.startswith("ppt/diagrams/")
            )
            has_smartart = bool(
                diagram_data
                and slide_rel_ids
                and slide_diagram_rels
                and data_diagram_rels
                and layout_parts
                and style_parts
                and color_parts
            )
            return has_ole, has_smartart
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, KeyError):
        return None


def _empty_counts() -> dict[str, int]:
    return {
        "cases": 0,
        "eligible_holdout": 0,
        "disclosed": 0,
        "retired": 0,
        "replacement_reserve": 0,
        "silver": 0,
        "gold": 0,
        "unreviewed": 0,
        "template_families": 0,
        "source_materials": 0,
        "ole_cases": 0,
        "smartart_cases": 0,
        "mixed_language_cases": 0,
        "font_families": 0,
    }


def challenge_status(
    root: str | Path, *, expected_manifest_identity: str | None = None
) -> dict[str, Any]:
    """Return counts and generic issues only; never return case tokens or paths."""
    counts = _empty_counts()
    issues: list[str] = []
    manifest_identity: str | None = None
    native_render = "unverified"
    candidate = Path(root)
    if not candidate.is_absolute():
        issues.append("root_not_absolute")
    try:
        root_path = candidate.resolve(strict=True)
        root_mode = candidate.lstat().st_mode
        if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
            issues.append("root_not_private_directory")
        if root_mode & 0o077:
            issues.append("root_permissions_open")
        if root_path == _REPO_ROOT or _REPO_ROOT in root_path.parents:
            issues.append("root_inside_repository")
    except OSError:
        root_path = candidate
        issues.append("root_unavailable")

    manifest_path = root_path / "manifest.json"
    manifest: dict[str, Any] | None = None
    try:
        mode = manifest_path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode) or mode & 0o222:
            issues.append("manifest_not_sealed")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        stored_identity = manifest.get("manifest_identity")
        unsigned = dict(manifest)
        unsigned.pop("manifest_identity", None)
        computed_identity = _sha(_canonical(unsigned))
        if not isinstance(stored_identity, str) or stored_identity != computed_identity:
            issues.append("manifest_identity_mismatch")
        else:
            manifest_identity = stored_identity
        if (
            expected_manifest_identity is not None
            and expected_manifest_identity != manifest_identity
        ):
            issues.append("expected_identity_mismatch")
        if manifest.get("schema_version") != SCHEMA_VERSION:
            issues.append("unsupported_manifest_schema")
        if manifest.get("product_trials") != "not_started":
            issues.append("product_trial_status_invalid")
    except (OSError, ValueError, TypeError, AttributeError):
        issues.append("manifest_unavailable_or_invalid")

    cases = manifest.get("cases") if isinstance(manifest, dict) else None
    if not isinstance(cases, list):
        cases = []
        issues.append("case_registry_invalid")
    tokens: dict[str, dict[str, Any]] = {}
    fonts: set[str] = set()
    family_by_token: dict[str, str] = {}
    material_by_token: dict[str, str] = {}
    feature_by_token: dict[str, tuple[bool, bool]] = {}
    for row in cases:
        if not isinstance(row, dict):
            issues.append("case_record_invalid")
            continue
        token = row.get("token")
        if not isinstance(token, str) or not _TOKEN.fullmatch(token) or token in tokens:
            issues.append("case_identity_invalid")
            continue
        tokens[token] = row
        counts["cases"] += 1
        role = row.get("role")
        if role == "replacement_reserve":
            counts["replacement_reserve"] += 1
        elif role != "holdout":
            issues.append("case_role_invalid")
        ref_status = row.get("reference_status")
        review_status = row.get("review_status")
        if ref_status == "silver":
            counts["silver"] += 1
        elif ref_status == "gold":
            counts["gold"] += 1
            issues.append("reference_status_invalid")
        else:
            issues.append("reference_status_invalid")
        if review_status == "unreviewed":
            counts["unreviewed"] += 1
        else:
            issues.append("review_status_invalid")
        if row.get("trial_status") != "not_started":
            issues.append("product_trial_status_invalid")
        if row.get("template_family"):
            family_by_token[token] = str(row["template_family"])
        if row.get("source_material"):
            material_by_token[token] = str(row["source_material"])
        row_fonts = row.get("fonts", [])
        if isinstance(row_fonts, list):
            fonts.update(str(font) for font in row_fonts if isinstance(font, str) and font)
        languages = row.get("languages", [])
        if isinstance(languages, list) and len({str(v) for v in languages}) >= 2:
            counts["mixed_language_cases"] += 1

        assets = row.get("assets")
        if not isinstance(assets, dict) or not _REQUIRED_ASSETS.issubset(assets):
            issues.append("required_assets_missing")
            continue
        resolved_assets: dict[str, Path] = {}
        for kind, asset in assets.items():
            if not isinstance(asset, dict):
                issues.append("asset_record_invalid")
                continue
            path = _safe_file(root_path, asset.get("path"))
            expected_hash = asset.get("sha256")
            if (
                path is None
                or not isinstance(expected_hash, str)
                or not _SHA256.fullmatch(expected_hash)
            ):
                issues.append("asset_path_or_hash_invalid")
                continue
            try:
                file_mode = path.lstat().st_mode
                if not stat.S_ISREG(file_mode) or file_mode & 0o222:
                    issues.append("asset_not_sealed")
                if _sha(path.read_bytes()) != expected_hash:
                    issues.append("asset_hash_mismatch")
                resolved_assets[str(kind)] = path
            except OSError:
                issues.append("asset_unavailable")
        source_path = resolved_assets.get("source")
        reference_path = resolved_assets.get("reference")
        if source_path and reference_path and reference_path.suffix.lower() == ".json":
            try:
                source_text = source_path.read_text(encoding="utf-8")
                reference = json.loads(reference_path.read_text(encoding="utf-8"))
                points = reference.get("points", []) if isinstance(reference, dict) else []
                if not isinstance(points, list):
                    raise ValueError
                for point in points:
                    quote = point.get("quote") if isinstance(point, dict) else None
                    if not isinstance(quote, str) or not quote or quote not in source_text:
                        issues.append("reference_anchor_invalid")
            except (OSError, ValueError, TypeError, UnicodeError):
                issues.append("reference_unavailable_or_invalid")
        template_path = resolved_assets.get("template")
        if template_path:
            features = _pptx_features(template_path)
            if features is None:
                issues.append("template_ooxml_invalid")
                continue
            ole, smartart = features
            declared = row.get("native_features", {})
            if (
                not isinstance(declared, dict)
                or declared.get("ole", False) != ole
                or declared.get("smartart", False) != smartart
            ):
                issues.append("native_feature_declaration_mismatch")
            feature_by_token[token] = features

    counts["font_families"] = len(fonts)
    events_path = root_path / "events.jsonl"
    disclosed: set[str] = set()
    retired: set[str] = set()
    activated: set[str] = set()
    try:
        if events_path.exists():
            event_mode = events_path.lstat().st_mode
            if stat.S_ISLNK(event_mode) or not stat.S_ISREG(event_mode) or event_mode & 0o077:
                issues.append("event_log_permissions_open")
            previous = "0" * 64
            sequence = 0
            for line in events_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                stored_hash = event.pop("event_hash", None)
                sequence += 1
                if (
                    event.get("sequence") != sequence
                    or event.get("previous_hash") != previous
                    or stored_hash != _sha(_canonical(event))
                ):
                    issues.append("event_log_chain_invalid")
                    break
                previous = stored_hash
                token = event.get("case_token")
                if token not in tokens:
                    issues.append("event_case_invalid")
                    continue
                if event.get("type") == "disclosure":
                    if (
                        token in disclosed
                        or token in retired
                        or (
                            tokens[token].get("role") == "replacement_reserve"
                            and token not in activated
                        )
                    ):
                        issues.append("event_transition_invalid")
                    disclosed.add(token)
                elif event.get("type") == "retirement":
                    replacement = event.get("replacement_token")
                    if (
                        token in retired
                        or replacement not in tokens
                        or replacement in activated
                        or replacement in disclosed
                    ):
                        issues.append("event_transition_invalid")
                    else:
                        retired.add(token)
                        activated.add(replacement)
                        if (
                            tokens[token].get("role") != "holdout" and token not in activated
                        ) or tokens[replacement].get("role") != "replacement_reserve":
                            issues.append("event_transition_invalid")
                        regression_path = _safe_file(root_path, event.get("regression_path"))
                        if regression_path is None or not isinstance(
                            event.get("regression_sha256"), str
                        ):
                            issues.append("regression_record_invalid")
                        elif _sha(regression_path.read_bytes()) != event["regression_sha256"]:
                            issues.append("regression_record_invalid")
                else:
                    issues.append("event_type_invalid")
    except (OSError, ValueError, TypeError):
        issues.append("event_log_invalid")

    counts["disclosed"] = len(disclosed)
    counts["retired"] = len(retired)
    holdouts = {token for token, row in tokens.items() if row.get("role") == "holdout"}
    eligible = ((holdouts - disclosed - retired) | activated) - disclosed - retired
    counts["eligible_holdout"] = len(eligible)
    families = {family_by_token[token] for token in eligible if token in family_by_token}
    materials = {material_by_token[token] for token in eligible if token in material_by_token}
    counts["template_families"] = len(families)
    counts["source_materials"] = len(materials)
    counts["ole_cases"] = sum(feature_by_token.get(token, (False, False))[0] for token in eligible)
    counts["smartart_cases"] = sum(
        feature_by_token.get(token, (False, False))[1] for token in eligible
    )
    native_validation = manifest.get("native_validation", {}) if isinstance(manifest, dict) else {}
    if not isinstance(native_validation, dict):
        native_validation = {}
        issues.append("native_validation_invalid")
    render_state = native_validation.get("render")
    if render_state == "passed":
        evidence = native_validation.get("rendered_outputs")
        expected_templates = {
            row.get("assets", {}).get("template", {}).get("sha256")
            for row in cases
            if isinstance(row, dict)
        }
        seen_templates: set[str] = set()
        valid_evidence = isinstance(native_validation.get("renderer"), str) and bool(
            native_validation.get("renderer")
        )
        if not isinstance(evidence, list) or len(evidence) != len(cases):
            valid_evidence = False
        else:
            for row in evidence:
                if not isinstance(row, dict):
                    valid_evidence = False
                    continue
                template_hash = row.get("template_sha256")
                output_hash = row.get("sha256")
                output_path = _safe_file(root_path, row.get("path"))
                if (
                    not isinstance(template_hash, str)
                    or template_hash not in expected_templates
                    or template_hash in seen_templates
                    or not isinstance(output_hash, str)
                    or not _SHA256.fullmatch(output_hash)
                    or output_path is None
                    or type(row.get("pages")) is not int
                    or row.get("pages", 0) < 1
                ):
                    valid_evidence = False
                    continue
                seen_templates.add(template_hash)
                try:
                    file_mode = output_path.lstat().st_mode
                    output_bytes = output_path.read_bytes()
                    if (
                        not stat.S_ISREG(file_mode)
                        or file_mode & 0o222
                        or not output_bytes.startswith(b"%PDF-")
                        or _sha(output_bytes) != output_hash
                        or len(re.findall(rb"/Type\s*/Page\b", output_bytes)) != row["pages"]
                    ):
                        valid_evidence = False
                except OSError:
                    valid_evidence = False
        if seen_templates != expected_templates:
            valid_evidence = False
        if valid_evidence:
            native_render = "passed"
        else:
            issues.append("native_render_evidence_invalid")
    elif render_state == "failed":
        native_render = "failed"
        issues.append("native_render_failed")
    else:
        issues.append("native_render_not_validated")

    if counts["eligible_holdout"] < 12:
        issues.append("holdout_count_below_minimum")
    if counts["template_families"] < 6:
        issues.append("template_diversity_below_minimum")
    if counts["source_materials"] < 6:
        issues.append("source_diversity_below_minimum")
    if counts["silver"] != counts["cases"] or counts["gold"]:
        issues.append("reference_not_silver_only")
    if counts["unreviewed"] != counts["cases"]:
        issues.append("review_status_not_unreviewed")
    if counts["ole_cases"] < 1 or counts["smartart_cases"] < 1:
        issues.append("structural_feature_coverage_missing")
    if native_validation.get("compatibility") != "unverified":
        issues.append("compatibility_claim_invalid")
    issues = sorted(set(issues))
    integrity_codes = {
        "root_not_absolute",
        "root_not_private_directory",
        "root_permissions_open",
        "root_inside_repository",
        "root_unavailable",
        "manifest_not_sealed",
        "manifest_identity_mismatch",
        "expected_identity_mismatch",
        "manifest_unavailable_or_invalid",
        "unsupported_manifest_schema",
        "case_registry_invalid",
        "case_record_invalid",
        "case_identity_invalid",
        "asset_record_invalid",
        "asset_path_or_hash_invalid",
        "asset_not_sealed",
        "asset_hash_mismatch",
        "asset_unavailable",
        "template_ooxml_invalid",
        "native_feature_declaration_mismatch",
        "event_log_permissions_open",
        "event_log_chain_invalid",
        "event_case_invalid",
        "event_transition_invalid",
        "regression_record_invalid",
        "event_type_invalid",
        "event_log_invalid",
        "reference_anchor_invalid",
        "reference_unavailable_or_invalid",
        "case_role_invalid",
        "reference_status_invalid",
        "review_status_invalid",
        "product_trial_status_invalid",
        "required_assets_missing",
        "native_validation_invalid",
        "compatibility_claim_invalid",
        "product_trial_status_invalid",
        "native_render_evidence_invalid",
        "manifest_identity_mismatch",
        "expected_identity_mismatch",
    }
    status = (
        "invalid"
        if any(code in integrity_codes for code in issues)
        else ("not_ready" if issues else "ready")
    )
    return {
        "status": status,
        "readiness": status == "ready",
        "manifest_identity": manifest_identity,
        "counts": counts,
        "reference_state": "silver_unreviewed_only"
        if counts["silver"] == counts["cases"] and counts["unreviewed"] == counts["cases"]
        else "invalid_or_mixed",
        "product_trials": "not_started"
        if manifest and manifest.get("product_trials") == "not_started"
        else "invalid_or_unknown",
        "native_validation": {
            "structural_ooxml": "passed"
            if counts["ole_cases"] >= 1
            and counts["smartart_cases"] >= 1
            and "template_ooxml_invalid" not in issues
            else "failed",
            "native_render": native_render,
            "office_compatibility": "unverified",
        },
        "issue_codes": issues,
    }


def _append_event(root: Path, fields: dict[str, Any]) -> None:
    path = root / "events.jsonl"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        lines = Path(path).read_text(encoding="utf-8").splitlines() if path.exists() else []
        previous = "0" * 64
        if lines:
            prior = json.loads(lines[-1])
            previous = prior.get("event_hash", previous)
        event = {"sequence": len(lines) + 1, "previous_hash": previous, **fields}
        event["event_hash"] = _sha(_canonical(event))
        stream.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _event_state(root: Path) -> tuple[set[str], set[str], set[str]]:
    disclosed: set[str] = set()
    retired: set[str] = set()
    activated: set[str] = set()
    path = root / "events.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("type") == "disclosure":
                disclosed.add(event["case_token"])
            elif event.get("type") == "retirement":
                retired.add(event["case_token"])
                activated.add(event["replacement_token"])
    return disclosed, retired, activated


def record_disclosure(
    root: str | Path, case_token: str, *, expected_manifest_identity: str
) -> dict[str, Any]:
    """Append a disclosure event; aggregate output contains no private token."""
    status = challenge_status(root, expected_manifest_identity=expected_manifest_identity)
    if status["status"] == "invalid":
        raise ValueError("challenge validation failed")
    root_path = Path(root).resolve(strict=True)
    manifest = json.loads((root_path / "manifest.json").read_text(encoding="utf-8"))
    cases = {row.get("token"): row for row in manifest["cases"]}
    disclosed, retired, activated = _event_state(root_path)
    active = {token for token, row in cases.items() if row.get("role") == "holdout"} | activated
    active -= disclosed | retired
    if case_token not in active or case_token not in cases:
        raise ValueError("challenge transition rejected")
    _append_event(root_path, {"type": "disclosure", "case_token": case_token})
    return challenge_status(root_path, expected_manifest_identity=expected_manifest_identity)


def retire_case(
    root: str | Path,
    case_token: str,
    replacement_token: str,
    regression_record_path: str,
    *,
    expected_manifest_identity: str,
) -> dict[str, Any]:
    """Retire a holdout only when a sealed regression record and reserve exist."""
    status = challenge_status(root, expected_manifest_identity=expected_manifest_identity)
    if status["status"] == "invalid":
        raise ValueError("challenge validation failed")
    root_path = Path(root).resolve(strict=True)
    manifest = json.loads((root_path / "manifest.json").read_text(encoding="utf-8"))
    tokens = {row.get("token"): row for row in manifest["cases"]}
    disclosed, retired, activated = _event_state(root_path)
    old_is_active_or_disclosed = (
        tokens.get(case_token, {}).get("role") == "holdout" or case_token in activated
    ) and case_token not in retired
    replacement_is_available = (
        tokens.get(replacement_token, {}).get("role") == "replacement_reserve"
        and replacement_token not in activated
        and replacement_token not in disclosed
        and replacement_token not in retired
    )
    if not old_is_active_or_disclosed or not replacement_is_available:
        raise ValueError("challenge transition rejected")
    record_path = _safe_file(root_path, regression_record_path)
    if record_path is None or record_path.stat().st_mode & 0o222:
        raise ValueError("challenge transition rejected")
    _append_event(
        root_path,
        {
            "type": "retirement",
            "case_token": case_token,
            "replacement_token": replacement_token,
            "regression_path": regression_record_path,
            "regression_sha256": _sha(record_path.read_bytes()),
        },
    )
    result = challenge_status(root_path, expected_manifest_identity=expected_manifest_identity)
    if result["status"] == "invalid":
        raise ValueError("challenge transition rejected")
    return result


# Parent CLI entry point; return values intentionally remain aggregate-only.
aggregate_status = challenge_status
