import hashlib
import json
import os
import zipfile
from pathlib import Path
from io import BytesIO

from reportlab.pdfgen.canvas import Canvas

from audit_e2e.challenge import aggregate_status, record_disclosure, retire_case


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _write_sealed(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o400)
    return {
        "path": path.relative_to(path.parents[1]).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _template_bytes(identity, *, ole=False, smartart=False):
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        shape = '<p:oleObj r:id="rId1"/>' if ole else ""
        archive.writestr(
            "ppt/slides/slide1.xml",
            f'<p:sld xmlns:p="urn:p" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">{shape}</p:sld>',
        )
        if ole:
            archive.writestr("ppt/embeddings/object.bin", b"mock-embedded-object")
            archive.writestr(
                "ppt/slides/_rels/slide1.xml.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="../embeddings/object.bin"/></Relationships>',
            )
        if smartart:
            diagram = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
            package_rels = "http://schemas.openxmlformats.org/package/2006/relationships"
            archive.writestr(
                "ppt/slides/slide2.xml",
                f'<p:sld xmlns:p="urn:p" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:dgm="{diagram}"><dgm:relIds r:dm="rId1" r:lo="rId2" r:qs="rId3" r:cs="rId4"/></p:sld>',
            )
            archive.writestr(
                "ppt/slides/_rels/slide2.xml.rels",
                f'<Relationships xmlns="{package_rels}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData" Target="../diagrams/data1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramLayout" Target="../diagrams/layout1.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramQuickStyle" Target="../diagrams/quickStyle1.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramColors" Target="../diagrams/colors1.xml"/></Relationships>',
            )
            archive.writestr("ppt/diagrams/data1.xml", f'<dgm:dataModel xmlns:dgm="{diagram}"/>')
            archive.writestr(
                "ppt/diagrams/_rels/data1.xml.rels",
                f'<Relationships xmlns="{package_rels}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramLayout" Target="layout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramQuickStyle" Target="quickStyle1.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramColors" Target="colors1.xml"/></Relationships>',
            )
            archive.writestr("ppt/diagrams/layout1.xml", f'<dgm:layoutDef xmlns:dgm="{diagram}"/>')
            archive.writestr(
                "ppt/diagrams/quickStyle1.xml", f'<dgm:styleDef xmlns:dgm="{diagram}"/>'
            )
            archive.writestr("ppt/diagrams/colors1.xml", f'<dgm:colorsDef xmlns:dgm="{diagram}"/>')
        archive.writestr("ppt/mock/identity.txt", str(identity))
    return buffer.getvalue()


def _pdf_bytes(identity):
    buffer = BytesIO()
    canvas = Canvas(buffer)
    canvas.drawString(72, 720, f"Public mock render {identity}")
    canvas.showPage()
    canvas.drawString(72, 720, f"Public mock render {identity} 2")
    canvas.showPage()
    canvas.drawString(72, 720, f"Public mock render {identity} 3")
    canvas.save()
    return buffer.getvalue()


def _make_challenge(root: Path):
    root.mkdir(mode=0o700)
    assets = root / "assets"
    assets.mkdir(mode=0o700)
    cases = []
    rendered_outputs = []
    for index in range(13):
        token = f"public-mock-{index:02d}"
        source_text = f"Public mock fact {index}: value {index + 10}.\n"
        ref_data = json.dumps({"points": [{"quote": source_text.strip()}]}).encode()
        source = _write_sealed(assets / f"source-{index:02d}.txt", source_text.encode())
        reference = _write_sealed(assets / f"reference-{index:02d}.json", ref_data)
        ole = index in {0, 12}
        smartart = index in {1, 12}
        template = _write_sealed(
            assets / f"template-{index:02d}.pptx",
            _template_bytes(index, ole=ole, smartart=smartart),
        )
        rendered = _write_sealed(root / "rendered" / f"mock-{index:02d}.pdf", _pdf_bytes(index))
        rendered_outputs.append(
            {
                "template_sha256": template["sha256"],
                "path": rendered["path"],
                "sha256": rendered["sha256"],
                "pages": 3,
            }
        )
        cases.append(
            {
                "token": token,
                "role": "replacement_reserve" if index == 12 else "holdout",
                "revision": 1,
                "template_family": f"public-family-{index % 6}",
                "source_material": f"public-material-{index:02d}",
                "reference_status": "silver",
                "review_status": "unreviewed",
                "trial_status": "not_started",
                "languages": ["en", "fr"] if index == 0 else ["en"],
                "fonts": [f"Public Font {index % 7}"],
                "native_features": {"ole": ole, "smartart": smartart},
                "assets": {"source": source, "reference": reference, "template": template},
            }
        )
    manifest = {
        "schema_version": 1,
        "cases": cases,
        "product_trials": "not_started",
        "native_validation": {
            "render": "passed",
            "renderer": "Public Mock Renderer",
            "rendered_outputs": rendered_outputs,
            "compatibility": "unverified",
        },
    }
    manifest["manifest_identity"] = hashlib.sha256(_canonical(manifest)).hexdigest()
    manifest_path = root / "manifest.json"
    manifest_path.write_bytes(_canonical(manifest))
    manifest_path.chmod(0o400)
    return manifest["manifest_identity"], cases


def test_aggregate_status_is_ready_and_contains_only_aggregate_data(tmp_path):
    root = tmp_path / "mock-challenge"
    identity, _ = _make_challenge(root)

    status = aggregate_status(root, expected_manifest_identity=identity)

    assert status["status"] == "ready"
    assert status["counts"]["eligible_holdout"] == 12
    assert status["counts"]["template_families"] == 6
    assert status["counts"]["source_materials"] == 12
    assert status["counts"]["silver"] == status["counts"]["unreviewed"] == 13
    assert status["counts"]["gold"] == 0
    assert status["product_trials"] == "not_started"
    assert status["native_validation"] == {
        "structural_ooxml": "passed",
        "native_render": "passed",
        "office_compatibility": "unverified",
    }
    assert "public-mock-00" not in repr(status)
    assert "assets" not in repr(status)


def test_digest_or_expected_identity_mismatch_is_invalid(tmp_path):
    root = tmp_path / "mock-challenge"
    identity, cases = _make_challenge(root)
    source_path = root / cases[0]["assets"]["source"]["path"]
    os.chmod(source_path, 0o600)
    source_path.write_text("changed", encoding="utf-8")

    assert aggregate_status(root)["status"] == "invalid"
    assert aggregate_status(root, expected_manifest_identity="0" * 64)["status"] == "invalid"


def test_gold_or_anchoring_errors_are_invalid(tmp_path):
    root = tmp_path / "mock-challenge"
    _, _ = _make_challenge(root)
    manifest_path = root / "manifest.json"
    os.chmod(manifest_path, 0o600)
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["reference_status"] = "gold"
    manifest["cases"][0]["assets"]["reference"]["sha256"] = "0" * 64
    manifest.pop("manifest_identity")
    manifest["manifest_identity"] = hashlib.sha256(_canonical(manifest)).hexdigest()
    manifest_path.write_bytes(_canonical(manifest))
    manifest_path.chmod(0o400)

    status = aggregate_status(root)
    assert status["status"] == "invalid"
    assert "reference_status_invalid" in status["issue_codes"]


def test_disclosure_removes_case_from_holdout(tmp_path):
    root = tmp_path / "mock-challenge"
    identity, cases = _make_challenge(root)

    status = record_disclosure(root, cases[0]["token"], expected_manifest_identity=identity)

    assert status["status"] == "not_ready"
    assert status["counts"]["disclosed"] == 1
    assert status["counts"]["eligible_holdout"] == 11
    assert cases[0]["token"] not in repr(status)


def test_disclosure_recomputes_active_diversity_and_feature_coverage(tmp_path):
    root = tmp_path / "mock-challenge"
    identity, cases = _make_challenge(root)
    record_disclosure(root, cases[0]["token"], expected_manifest_identity=identity)
    status = record_disclosure(root, cases[6]["token"], expected_manifest_identity=identity)

    assert status["status"] == "not_ready"
    assert status["counts"]["template_families"] == 5
    assert status["counts"]["eligible_holdout"] == 10
    assert status["counts"]["ole_cases"] == 0
    assert status["counts"]["smartart_cases"] == 1


def test_rejected_duplicate_disclosure_does_not_corrupt_journal(tmp_path):
    root = tmp_path / "mock-challenge"
    identity, cases = _make_challenge(root)
    record_disclosure(root, cases[0]["token"], expected_manifest_identity=identity)
    before = (root / "events.jsonl").read_bytes()

    import pytest

    with pytest.raises(ValueError):
        record_disclosure(root, cases[0]["token"], expected_manifest_identity=identity)

    assert (root / "events.jsonl").read_bytes() == before
    assert aggregate_status(root)["status"] == "not_ready"


def test_retirement_requires_regression_and_activates_new_identity(tmp_path):
    root = tmp_path / "mock-challenge"
    identity, cases = _make_challenge(root)
    record = root / "regressions" / "record.json"
    record.parent.mkdir(mode=0o700)
    record.write_text('{"schema":"mock"}', encoding="utf-8")
    record.chmod(0o400)

    status = retire_case(
        root,
        cases[0]["token"],
        cases[12]["token"],
        "regressions/record.json",
        expected_manifest_identity=identity,
    )

    assert status["status"] == "ready"
    assert status["counts"]["retired"] == 1
    assert status["counts"]["eligible_holdout"] == 12
    assert cases[0]["token"] not in repr(status)


def test_disclosed_case_can_be_retired_with_record_and_new_replacement(tmp_path):
    root = tmp_path / "mock-challenge"
    identity, cases = _make_challenge(root)
    record_disclosure(root, cases[0]["token"], expected_manifest_identity=identity)
    record = root / "regressions" / "record.json"
    record.parent.mkdir(mode=0o700)
    record.write_text('{"schema":"mock"}', encoding="utf-8")
    record.chmod(0o400)

    status = retire_case(
        root,
        cases[0]["token"],
        cases[12]["token"],
        "regressions/record.json",
        expected_manifest_identity=identity,
    )

    assert status["status"] == "ready"
    assert status["counts"]["disclosed"] == status["counts"]["retired"] == 1
    assert status["counts"]["eligible_holdout"] == 12


def test_event_chain_tampering_is_invalid(tmp_path):
    root = tmp_path / "mock-challenge"
    identity, cases = _make_challenge(root)
    record_disclosure(root, cases[0]["token"], expected_manifest_identity=identity)
    events = root / "events.jsonl"
    os.chmod(events, 0o600)
    events.write_text(events.read_text().replace('"type": "disclosure"', '"type": "retirement"'))

    assert aggregate_status(root)["status"] == "invalid"


def test_product_trial_status_change_is_invalid(tmp_path):
    root = tmp_path / "mock-challenge"
    _make_challenge(root)
    manifest_path = root / "manifest.json"
    os.chmod(manifest_path, 0o600)
    manifest = json.loads(manifest_path.read_text())
    manifest["product_trials"] = "complete"
    manifest.pop("manifest_identity")
    manifest["manifest_identity"] = hashlib.sha256(_canonical(manifest)).hexdigest()
    manifest_path.write_bytes(_canonical(manifest))
    manifest_path.chmod(0o400)

    status = aggregate_status(root)
    assert status["status"] == "invalid"
    assert "product_trial_status_invalid" in status["issue_codes"]
