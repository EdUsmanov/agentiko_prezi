from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

from PIL import Image

from audit_e2e.corpus import AUDIT_ROOT, load_cases, materialize_case
from audit_e2e.development_builder import _SPECS, _build_screenshot, _build_template
from audit_e2e.development_inventory import DEVELOPMENT_ROOT, _presentation, inventory_report


def _digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def test_suite_sizes_and_baseline_case_ids_are_preserved():
    core = load_cases("core")
    extended = load_cases("extended")
    development = load_cases("development")

    assert len(core) == 4
    assert len(extended) == 9
    assert len(development) == 24
    assert [case["id"] for case in core] == [case["id"] for case in extended[:4]]
    assert [case["id"] for case in extended] == [case["id"] for case in development[:9]]
    assert len({case["id"] for case in development}) == 24


def test_development_inputs_are_source_anchored_and_fully_native(tmp_path):
    cases = load_cases("development")[9:]
    expected_image_sizes = {
        "dev-screenshot-landscape.png": (1600, 900),
        "dev-screenshot-portrait.png": (540, 960),
        "dev-screenshot-square.png": (960, 960),
    }
    registered_templates = set()

    for case in cases:
        assert case["content_source"]["file"].startswith(
            "audit_e2e/fixtures/development/sources/"
        )
        source = AUDIT_ROOT.parent / case["content_source"]["file"]
        assert _digest(source) == case["content_source"]["sha256"]
        assert all(point["quote"] in case["content"] for point in case["reference"]["points"])
        assert case["reference"]["status"] == "silver"
        assert case["reference"]["provenance"]["review_status"] == "unreviewed"
        assert "challenge" not in case["id"].casefold()
        assert "private" not in case["id"].casefold()

        template = DEVELOPMENT_ROOT / case["synthetic_template"]["asset"]
        assert template.is_file()
        assert _digest(template) == case["template"]["sha256"]
        registered_templates.add(template.name)
        with ZipFile(template) as package:
            assert package.testzip() is None
        parsed = _presentation(template.read_bytes(), template.name)
        assert parsed.slide_width > 0 and parsed.slide_height > 0

        native_run = materialize_case(case, tmp_path / case["id"] / "native")
        replay_run = materialize_case(
            case, tmp_path / case["id"] / "replay", synthetic=True
        )
        assert _digest(native_run["template"]) == _digest(replay_run["template"])
        assert native_run["template_origin"] == "owned_native_synthetic"
        assert replay_run["template_origin"] == "owned_native_synthetic"
        assert native_run["template_fidelity_applicability"] == "owned_native_template"
        assert replay_run["template_fidelity_applicability"] == "owned_native_template"
        assert _digest(native_run["template"]) == case["template"]["sha256"]

        for image in case["images"]:
            image_path = DEVELOPMENT_ROOT / "images" / image["file"]
            assert _digest(image_path) == image["sha256"]
            with Image.open(image_path) as opened:
                assert (opened.width, opened.height) == expected_image_sizes[image["file"]]
            assert image["origin"] == "owned_synthetic_asset"

    assert len(registered_templates) == 15


def test_inventory_reports_actual_features_and_distinguishes_originals_from_analogs():
    report = inventory_report()
    owned = report["owned_development"]
    analogs = report["simplified_replay_analogs"]
    originals = report["original_external_templates"]

    assert report["case_counts"] == {"core": 4, "extended": 9, "development": 24}
    assert originals["case_count"] == 9
    assert originals["unique_registered_file_sha256_count"] == 8
    assert len(originals["identical_registered_byte_groups"]) == 1
    assert originals["identical_registered_byte_groups"][0]["case_ids"] == [
        "binghamton-brief",
        "binghamton-content",
    ]
    assert analogs["case_count"] == 9
    assert analogs["unique_file_sha256_count"] == 7
    assert analogs["broad_profile_count"] == 4
    assert any(
        group["case_ids"] == ["mcmurry-brand", "wpi-reference-pages"]
        for group in analogs["duplicate_byte_groups"]
    )
    assert all(not analog["source_reference_features_reproduced"] for analog in analogs["cases"])
    assert "reference-page" in " ".join(analogs["limitations"])

    assert owned["case_count"] == 15
    assert owned["independent_source_file_count"] == 15
    assert owned["unique_template_sha256_count"] == 15
    assert owned["unique_image_sha256_count"] == 3
    assert owned["declared_family_count"] == 15
    assert owned["measured_effective_structural_signature_count"] >= 12
    assert report["coverage_evidence"]["all_declared_features_present"]
    assert report["coverage_evidence"]["all_sources_and_quotes_verified"]
    assert not report["coverage_evidence"]["challenge_or_private_inputs_imported"]


def test_owned_fixture_builder_reproduces_the_frozen_native_bytes(tmp_path):
    for spec in _SPECS:
        expected = DEVELOPMENT_ROOT / "templates" / spec["file"]
        rebuilt = tmp_path / spec["file"]
        _build_template(spec, rebuilt)
        assert _digest(rebuilt) == _digest(expected), spec["id"]

        with ZipFile(rebuilt) as package:
            assert package.testzip() is None
        parsed = _presentation(rebuilt.read_bytes(), rebuilt.name)
        assert parsed.slide_width > 0 and parsed.slide_height > 0
        assert list(parsed.slides)
        for master in parsed.slide_masters:
            layouts = list(master.slide_layouts)
            assert layouts
            assert all(layout.part is not None for layout in layouts)

        if spec.get("image"):
            image_spec = spec["image"]
            rebuilt_image = tmp_path / image_spec["file"]
            _build_screenshot(image_spec, rebuilt_image)
            assert _digest(rebuilt_image) == _digest(
                DEVELOPMENT_ROOT / "images" / image_spec["file"]
            )
