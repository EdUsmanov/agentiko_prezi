import hashlib
import json
import threading
import time

import pytest

from audit_e2e import judge


def _png():
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (1).to_bytes(4, "big")
        + (1).to_bytes(4, "big")
        + bytes((8, 2, 0, 0, 0))
    )


def _bundle(
    root, case_id="case-01", *, status="gold", proposals=None, variant_names=("executive",)
):
    root.mkdir(parents=True, exist_ok=True)
    (root / "slide.png").write_bytes(_png())
    (root / "deck.pptx").write_bytes(b"pptx fixture")
    text = "The pilot runs for 12 weeks."
    document = {
        "schema_version": 1,
        "case_id": case_id,
        "source": {
            "text": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            **({"approved_proposals": proposals} if proposals is not None else {}),
        },
        "reference": {
            "version": "ref-v1",
            "status": status,
            "provenance": {"review_status": "approved"} if status == "gold" else {},
            "points": [
                {
                    "id": "point-01",
                    "statement": "The pilot lasts twelve weeks.",
                    "quote": "The pilot runs for 12 weeks.",
                    "required": True,
                }
            ],
            "requirements": [],
        },
        "variants": {
            name: {
                "pptx": "deck.pptx",
                "slides": [{"number": 1, "image": "slide.png", "text": text, "objects": []}],
            }
            for name in variant_names
        },
    }
    path = root / "bundle.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _variant_result(
    *, design_status="passed", design_score=4, status="passed", point_status="preserved"
):
    return {
        "status": status,
        "categories": {
            "semantic": {"status": "passed", "score": 4},
            "visible": {"status": "passed", "score": 4},
            "design": {"status": design_status, "score": design_score},
        },
        "coverage": {"slide_numbers": [1], "point_ids": ["point-01"]},
        "points": [
            {
                "point_id": "point-01",
                "status": point_status,
                "evidence": "The pilot runs for 12 weeks." if point_status != "absent" else "",
                "slide_number": 1 if point_status != "absent" else None,
            }
        ],
        "findings": [],
        "summary": "Checked all evidence.",
    }


def _validated_case(case_id="case-01", *, reference_status="gold", variant=None):
    return {
        "case_id": case_id,
        "reference_status": reference_status,
        "status": "passed",
        "variants": {"executive": variant or _variant_result()},
    }


def _schema_objects(schema, path="$", violations=None):
    violations = violations if violations is not None else []
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            properties = set(schema.get("properties", {}))
            required = set(schema.get("required", []))
            if properties != required or schema.get("additionalProperties") is not False:
                violations.append(path)
        if any(key in schema for key in ("uniqueItems", "minimum", "maximum", "minLength")):
            violations.append(path + ":unsupported_constraint")
        for key, value in schema.items():
            _schema_objects(value, f"{path}.{key}", violations)
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            _schema_objects(value, f"{path}[{index}]", violations)
    return violations


def test_output_schemas_are_strict_and_codex_compatible():
    assert not _schema_objects(judge.JUDGE_SCHEMA)
    assert not _schema_objects(judge.COMPARE_SCHEMA)
    assert not _schema_objects(judge.JUDGE_CASE_SCHEMA)
    assert not _schema_objects(judge.COMPARE_CASE_SCHEMA)


def test_finding_evidence_accepts_only_exact_quoted_slide_excerpts():
    slide_text = "The pilot runs 12 weeks.\nNorth | 40 | 34 | 18"
    evidence = '"The pilot runs 12 weeks." and "North | 40 | 34 | 18"'

    assert judge._exact_slide_quote(evidence, slide_text) == evidence
    assert (
        judge._exact_slide_quote('"North | 40 | 34 | 18\\nSouth | 28"', slide_text + "\nSouth | 28")
        == "North | 40 | 34 | 18\nSouth | 28"
    )
    assert (
        judge._exact_slide_quote("“The pilot runs 12 weeks.”", slide_text)
        == "The pilot runs 12 weeks."
    )
    assert judge._exact_slide_quote('"invented finding"', slide_text) is None


def test_russian_quote_wrappers_preserve_exact_grounding():
    text = "Команда готовит единый\nсервис для обработки заявок. Пилот длится 12 недель."
    assert judge._exact_slide_quote("«Команда готовит единый\nсервис для обработки заявок.»", text)
    assert judge._exact_slide_quote(
        "На слайде: «Команда готовит единый сервис для обработки заявок.» и «12 недель».", text
    )
    assert judge._exact_slide_quote("«Пилот длится 14 недель.»", text) is None
    assert (
        judge._exact_slide_quote("«Команда не готовит единый сервис для обработки заявок.»", text)
        is None
    )


def test_pdf_visible_text_is_quote_source_with_formatting_only_normalization(tmp_path):
    path = _bundle(tmp_path / "pdf-visible")
    document = json.loads(path.read_text())
    slide = document["variants"]["executive"]["slides"][0]
    slide["text"] = ""
    slide["pdf_visible_text"] = (
        "Pilot duration is 12 weeks.\nNorth 28 24 21\nSouth 40 34 18\n"
        "The finding does not prove causation."
    )
    slide["pdf_text"] = "HIDDEN NOTES SECRET unsupported off-page text"
    path.write_text(json.dumps(document), encoding="utf-8")

    bundle = judge._read_bundle(path)
    normalized_slide = bundle["data"]["variants"]["executive"]["slides"][0]
    assert normalized_slide["pdf_visible_text"] == slide["pdf_visible_text"]
    assert "pdf_text" not in normalized_slide
    assert (
        judge._exact_slide_quote("“Pilot duration is 12 weeks.”", "", slide["pdf_visible_text"])
        == "Pilot duration is 12 weeks."
    )
    candidate = {"variant_id": "V01", **_variant_result()}
    candidate["points"][0]["evidence"] = "“Pilot duration is 12 weeks.”"
    checked = judge._validate_variant(
        candidate,
        alias="V01",
        slide_texts={1: ""},
        pdf_visible_texts={1: slide["pdf_visible_text"]},
        hidden_texts={1: ""},
        reference_points=[{"id": "point-01", "required": True, "statement": "Pilot lasts."}],
    )
    assert checked["points"][0]["status"] == "preserved"
    assert (
        judge._exact_slide_quote(
            "“North 28 24 21” and “South 40 34 18”", "", slide["pdf_visible_text"]
        )
        == "“North 28 24 21” and “South 40 34 18”"
    )
    for escaped in (r'"Pilot duration is 12\nweeks."', r'"Pilot duration is 12\\nweeks."'):
        assert judge._exact_slide_quote(escaped, "", "Pilot duration is 12\nweeks.") is not None

    assert judge._exact_slide_quote('"North 28 24 22"', "", slide["pdf_visible_text"]) is None
    assert (
        judge._exact_slide_quote(
            '"The finding does prove causation."', "", slide["pdf_visible_text"]
        )
        is None
    )
    assert judge._exact_slide_quote('"HIDDEN NOTES SECRET"', "", "") is None

    scratch, _meta, _images = judge._prepare_judge_pass([bundle], pass_number=0)
    try:
        packet = json.loads((scratch / "cases" / "case-001.json").read_text())
        packet_text = json.dumps(packet)
        assert packet["variants"][0]["slides"][0]["pdf_visible_text"] == slide["pdf_visible_text"]
        assert "pdf_text" not in packet_text
        assert "HIDDEN NOTES SECRET" not in packet_text
    finally:
        judge.shutil.rmtree(scratch, ignore_errors=True)


def test_worker_schema_binds_all_opaque_result_ids_to_known_case_values(tmp_path):
    bundle = judge._read_bundle(_bundle(tmp_path / "bound-schema"))
    scratch, meta, _images = judge._prepare_judge_pass([bundle], pass_number=0)
    try:
        case_schema = judge._bound_judge_case_schema(meta["cases"][0])
        case = case_schema["properties"]["case"]
        assert case["properties"]["case_id"]["enum"] == ["case-001"]
        variant = case["properties"]["variants"]["items"]
        assert variant["properties"]["variant_id"]["enum"] == ["V01"]
        point_ids = ["point-01"]
        assert (
            variant["properties"]["coverage"]["properties"]["point_ids"]["items"]["enum"]
            == point_ids
        )
        assert (
            variant["properties"]["points"]["items"]["properties"]["point_id"]["enum"] == point_ids
        )
        assert (
            variant["properties"]["findings"]["items"]["properties"]["point_ids"]["items"]["enum"]
            == point_ids
        )
        assert not _schema_objects(case_schema)

        pair = {"case_id": "case-01", "baseline": bundle, "candidate": bundle}
        compare_scratch, compare_meta, _images, _mapping = judge._prepare_compare_case(
            pair, blind_seed="schema-test", pass_number=0, case_number=1
        )
        try:
            compare_schema = judge._bound_compare_case_schema(compare_meta)
            comparison = compare_schema["properties"]["case"]["properties"]["comparisons"]["items"]
            assert comparison["properties"]["pair_id"]["enum"] == ["P01"]
            for side in ("A", "B"):
                side_schema = comparison["properties"]["sides"]["properties"][side]
                assert "variant_id" not in side_schema["properties"]
                assert (
                    side_schema["properties"]["points"]["items"]["properties"]["point_id"]["enum"]
                    == point_ids
                )
                assert (
                    side_schema["properties"]["findings"]["items"]["properties"]["point_ids"][
                        "items"
                    ]["enum"]
                    == point_ids
                )
            assert not _schema_objects(compare_schema)
        finally:
            judge.shutil.rmtree(compare_scratch, ignore_errors=True)
    finally:
        judge.shutil.rmtree(scratch, ignore_errors=True)


def test_bundle_checks_source_anchor_and_asset_containment(tmp_path):
    valid = _bundle(tmp_path / "valid")
    assert judge._read_bundle(valid)["data"]["case_id"] == "case-01"

    invalid = _bundle(tmp_path / "bad")
    doc = json.loads(invalid.read_text())
    doc["reference"]["points"][0]["quote"] = "invented quote"
    invalid.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="exact source anchor"):
        judge._read_bundle(invalid)

    traversal = _bundle(tmp_path / "traversal")
    doc = json.loads(traversal.read_text())
    doc["variants"]["executive"]["slides"][0]["image"] = "../outside.png"
    traversal.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="stay inside"):
        judge._read_bundle(traversal)


def test_response_requires_every_slide_and_every_reference_point(tmp_path):
    bundle = judge._read_bundle(_bundle(tmp_path / "case"))
    _, meta, _ = judge._prepare_judge_pass([bundle], pass_number=0)
    alias = meta["cases"][0]["variants"][0]["alias"]
    result = _variant_result()
    raw = {
        "schema_version": 1,
        "cases": [{"case_id": "case-001", "variants": [{"variant_id": alias, **result}]}],
    }
    raw["cases"][0]["variants"][0]["points"][0]["evidence"] = (
        'Slide text: "The pilot runs for 12 weeks."'
    )
    validated = judge._validate_judge_response(raw, meta)["case-01"]
    assert validated["status"] == "passed"
    assert (
        validated["variants"]["executive"]["points"][0]["evidence"]
        == "The pilot runs for 12 weeks."
    )

    raw["cases"][0]["variants"][0]["coverage"]["slide_numbers"] = []
    with pytest.raises(ValueError, match="omitted or duplicated slide/key-point coverage"):
        judge._validate_judge_response(raw, meta)


def test_image_only_key_point_and_numeric_finding_need_located_visual_evidence():
    candidate = _variant_result()
    candidate["variant_id"] = "V01"
    candidate["points"][0]["evidence"] = (
        "visual: Slide 1, central chart: “The pilot lasts twelve weeks.”"
    )
    candidate["categories"]["semantic"] = {"status": "failed", "score": 2}
    candidate["findings"] = [
        {
            "category": "number",
            "severity": "major",
            "slide_number": 1,
            "region": "central chart",
            "description": "The chart says thirteen weeks.",
            "evidence": "visual: Slide 1, central chart: “13 weeks”",
            "point_ids": ["point-01"],
        }
    ]

    checked = judge._validate_variant(
        candidate,
        alias="V01",
        slide_texts={1: ""},
        hidden_texts={1: ""},
        reference_points=[
            {"id": "point-01", "required": True, "statement": "The pilot lasts twelve weeks."}
        ],
    )

    assert checked["points"][0]["status"] == "preserved"
    assert checked["points"][0]["evidence"].startswith("visual: Slide 1, central chart:")
    assert checked["findings"][0]["category"] == "number"


@pytest.mark.parametrize(
    "evidence",
    [
        "visual: the point is shown in the image",
        "visual: Slide 1, central chart: the point is shown without readable text",
    ],
)
def test_image_only_key_point_rejects_unlocated_or_unquoted_evidence(evidence):
    candidate = _variant_result()
    candidate["variant_id"] = "V01"
    candidate["points"][0]["evidence"] = evidence

    with pytest.raises(ValueError, match="located visual evidence"):
        judge._validate_variant(
            candidate,
            alias="V01",
            slide_texts={1: ""},
            hidden_texts={1: ""},
            reference_points=[
                {"id": "point-01", "required": True, "statement": "The pilot lasts twelve weeks."}
            ],
        )


def test_visual_finding_can_use_a_located_observation_with_a_distinct_region_label():
    candidate = {"variant_id": "V02", **_variant_result()}
    candidate["categories"]["design"] = {"status": "failed", "score": 2}
    candidate["findings"] = [
        {
            "category": "template_fidelity",
            "severity": "major",
            "slide_number": 1,
            "region": "Overall composition: teal accent bar and Pilot sample panel",
            "description": "The panel and divider are on the wrong sides of the slide.",
            "evidence": (
                "visual: Slide 1, concrete region: the teal bar is near the center, "
                "immediately left of the Pilot sample panel on the right."
            ),
            "point_ids": [],
        }
    ]

    checked = judge._validate_variant(
        candidate,
        alias="V02",
        slide_texts={1: "The pilot runs for 12 weeks."},
        hidden_texts={1: ""},
        reference_points=[
            {"id": "point-01", "required": True, "statement": "The pilot lasts twelve weeks."}
        ],
    )

    assert checked["findings"][0]["region"].startswith("Overall composition")
    assert checked["categories"]["semantic"]["status"] == "passed"
    assert checked["categories"]["design"]["status"] == "failed"


def test_visual_finding_rejects_a_different_region_without_grounding_overlap():
    candidate = {"variant_id": "V02", **_variant_result()}
    candidate["categories"]["design"] = {"status": "failed", "score": 2}
    candidate["findings"] = [
        {
            "category": "template_fidelity",
            "severity": "major",
            "slide_number": 1,
            "region": "Overall composition: teal accent bar and Pilot sample panel",
            "description": "A visual defect is present.",
            "evidence": "visual: Slide 1, concrete region: footer logo at the bottom right.",
            "point_ids": [],
        }
    ]

    with pytest.raises(ValueError, match="located visual evidence"):
        judge._validate_variant(
            candidate,
            alias="V02",
            slide_texts={1: "The pilot runs for 12 weeks."},
            hidden_texts={1: ""},
            reference_points=[
                {"id": "point-01", "required": True, "statement": "The pilot lasts twelve weeks."}
            ],
        )


def test_omission_finding_can_link_to_a_distorted_point_and_must_be_grounded():
    candidate = {"variant_id": "V02", **_variant_result(point_status="distorted")}
    candidate["points"][0]["evidence"] = "Privacy safeguards are planned."
    candidate["categories"]["semantic"] = {"status": "failed", "score": 2}
    candidate["findings"] = [
        {
            "category": "omission",
            "severity": "major",
            "slide_number": 1,
            "region": "Closing sentence",
            "description": "The mandatory pre-launch review is omitted.",
            "evidence": "“Privacy safeguards are planned.”",
            "point_ids": ["point-01"],
        }
    ]
    reference = [
        {"id": "point-01", "required": True, "statement": "A privacy review is mandatory."}
    ]

    checked = judge._validate_variant(
        candidate,
        alias="V02",
        slide_texts={1: "Privacy safeguards are planned."},
        hidden_texts={1: ""},
        reference_points=reference,
    )
    assert checked["findings"][0]["evidence"] == "Privacy safeguards are planned."

    candidate["findings"][0]["evidence"] = "“Privacy review will happen before launch.”"
    with pytest.raises(ValueError, match="exact slide text or located visual evidence"):
        judge._validate_variant(
            candidate,
            alias="V02",
            slide_texts={1: "Privacy safeguards are planned."},
            hidden_texts={1: ""},
            reference_points=reference,
        )


def test_duplicate_variants_are_visible_blockers_and_design_advisories():
    candidate = {"variant_id": "V01", **_variant_result()}
    candidate["categories"]["visible"] = {"status": "failed", "score": 2}
    candidate["categories"]["design"] = {"status": "failed", "score": 2}
    candidate["findings"] = [
        {
            "category": "duplicates",
            "severity": "major",
            "slide_number": 1,
            "region": "complete deck",
            "description": "The output variant duplicates another exported variant.",
            "evidence": "visual: Slide 1, complete deck: the corresponding slide in another variant is pixel-identical.",
            "point_ids": [],
        }
    ]
    checked = judge._validate_variant(
        candidate,
        alias="V01",
        slide_texts={1: "The pilot runs for 12 weeks."},
        hidden_texts={1: ""},
        reference_points=[
            {"id": "point-01", "required": True, "statement": "The pilot lasts twelve weeks."}
        ],
    )
    assert checked["status"] == "failed"
    assert checked["categories"]["semantic"]["status"] == "passed"
    assert checked["categories"]["visible"]["status"] == "failed"
    assert checked["categories"]["design"]["status"] == "failed"


def test_design_failure_is_advisory_but_silver_reference_cannot_pass(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path / "case")
    monkeypatch.setattr(
        judge, "_calibration_validation", lambda _: {"status": "passed", "controls_hash": "a" * 64}
    )

    def run(cases, **_kwargs):
        case = cases[0]
        return (
            {
                "case-01": _validated_case(
                    reference_status=case["data"]["reference"]["status"],
                    variant=_variant_result(design_status="failed", design_score=2),
                )
            },
            {"status": "unavailable"},
            {},
        )

    monkeypatch.setattr(judge, "_run_judge_pass", run)
    report = judge.judge_cases([bundle], tmp_path / "report")
    assert report["status"] == "passed"
    assert report["cases"][0]["variants"]["executive"]["categories"]["design"]["status"] == "failed"

    silver = _bundle(tmp_path / "silver", status="silver")
    report = judge.judge_cases([silver], tmp_path / "silver-report")
    assert report["cases"][0]["status"] == "inconclusive"
    assert report["cases"][0]["reference_status"] == "silver"


def test_uncalibrated_failures_are_diagnostic_not_effective_gate_failures(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path / "case")
    calibration_status = {"status": "passed", "controls_hash": "a" * 64}
    monkeypatch.setattr(judge, "_calibration_validation", lambda _: calibration_status)
    variant = _variant_result(status="failed", point_status="distorted")
    variant["categories"]["semantic"] = {"status": "failed", "score": 2}
    variant["findings"] = [
        {
            "category": "number",
            "severity": "major",
            "slide_number": 1,
            "region": "center",
            "description": "A number is changed.",
            "evidence": "The pilot runs for 13 weeks.",
            "point_ids": ["point-01"],
        }
    ]
    result = _validated_case(variant=variant)
    result["status"] = "failed"
    monkeypatch.setattr(
        judge,
        "_run_judge_pass",
        lambda *_args, **_kwargs: ({"case-01": result}, {"status": "unavailable"}, {}),
    )

    calibrated = judge.judge_cases([bundle], tmp_path / "calibrated")
    assert calibrated["cases"][0]["status"] == "failed"
    assert calibrated["cases"][0]["observed_status"] == "failed"

    calibration_status = {"status": "inconclusive", "reason": "stale"}
    diagnostic = judge.judge_cases([bundle], tmp_path / "diagnostic")
    assert diagnostic["cases"][0]["status"] == "inconclusive"
    assert diagnostic["cases"][0]["observed_status"] == "failed"
    assert diagnostic["status"] == "inconclusive"


def test_judge_recheck_disagreement_is_inconclusive(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path / "case")
    monkeypatch.setattr(
        judge, "_calibration_validation", lambda _: {"status": "passed", "controls_hash": "b" * 64}
    )
    calls = 0

    def run(cases, **_kwargs):
        nonlocal calls
        calls += 1
        variant = _variant_result()
        if calls == 2:
            variant["points"][0].update(status="distorted", evidence="The pilot runs for 12 weeks.")
            variant["categories"]["semantic"] = {"status": "failed", "score": 2}
            variant["findings"] = [
                {
                    "category": "number",
                    "severity": "major",
                    "slide_number": 1,
                    "region": "center",
                    "description": "The duration is distorted.",
                    "evidence": "The pilot runs for 12 weeks.",
                    "point_ids": ["point-01"],
                }
            ]
            variant["status"] = "failed"
        return {"case-01": _validated_case(variant=variant)}, {"status": "unavailable"}, {}

    monkeypatch.setattr(judge, "_run_judge_pass", run)
    report = judge.judge_cases([bundle], tmp_path / "report")
    assert calls == 2
    assert report["cases"][0]["recheck"]["agreement"] == "disagreed"
    assert report["cases"][0]["status"] == "inconclusive"


def test_compare_allows_distinct_approved_proposals_but_blinds_and_records_them(
    tmp_path, monkeypatch
):
    baseline = _bundle(
        tmp_path / "baseline",
        proposals=[
            {"slide_number": 1, "text": "Proposal A", "origin": "model_proposal", "fact_ids": []}
        ],
    )
    candidate = _bundle(
        tmp_path / "candidate",
        proposals=[
            {"slide_number": 1, "text": "Proposal B", "origin": "model_proposal", "fact_ids": []}
        ],
    )
    calls = []

    def run(cases, *, blind_seed, pass_number, **_kwargs):
        calls.append((blind_seed, pass_number))
        row = {
            "case_id": "case-01",
            "reference_status": "gold",
            "status": "passed",
            "comparisons": [
                {
                    "variant": "executive",
                    "sides": {
                        side: {
                            "status": "passed",
                            "categories": {
                                "semantic": {"status": "passed", "score": 4},
                                "visible": {"status": "passed", "score": 4},
                            },
                            "points": [],
                            "findings": [],
                        }
                        for side in ("A", "B")
                    },
                    "winners": {"semantic": "tie", "visible": "tie", "design": "A"},
                    "rationale": "The evidence is equivalent.",
                }
            ],
        }
        mapping = judge._comparison_mapping(cases[0], blind_seed)
        return {"case-01": row}, {"status": "unavailable"}, {"case-01": mapping}

    monkeypatch.setattr(judge, "_run_compare_pass", run)
    monkeypatch.setattr(
        judge, "_calibration_validation", lambda _: {"status": "passed", "controls_hash": "c" * 64}
    )
    report = judge.compare_cases([baseline], [candidate], tmp_path / "compare")
    assert calls[0][0] == calls[1][0]
    expected_pair = {
        "case_id": "case-01",
        "baseline": judge._read_bundle(baseline),
        "candidate": judge._read_bundle(candidate),
    }
    assert report["blind_mapping"]["case-01"] == judge._comparison_mapping(
        expected_pair, calls[0][0]
    )
    assert report["cases"][0]["approved_proposals"]["baseline"][0]["text"] == "Proposal A"
    assert report["cases"][0]["approved_proposals"]["candidate"][0]["text"] == "Proposal B"
    assert report["status"] == "passed"


def test_compare_rechecks_and_retains_secondary_when_primary_worker_is_invalid(
    tmp_path, monkeypatch
):
    baseline = _bundle(tmp_path / "baseline")
    candidate = _bundle(tmp_path / "candidate")
    calls = 0

    def run(cases, *, blind_seed, pass_number, **_kwargs):
        nonlocal calls
        calls += 1
        if pass_number == 0:
            raise judge.WorkerBatchError(
                "invalid primary reply",
                usage={"status": "partial", "totals": {"input_tokens": 3}},
                workers=[],
                case_errors={"case-01": {"type": "ValueError", "message": "bad schema"}},
            )
        pair = cases[0]
        variant = {
            "status": "passed",
            "categories": {
                "semantic": {"status": "passed", "score": 4},
                "visible": {"status": "passed", "score": 4},
            },
            "points": [],
            "findings": [],
        }
        row = {
            "case_id": "case-01",
            "reference_status": "gold",
            "status": "passed",
            "comparisons": [
                {
                    "variant": "executive",
                    "sides": {"A": variant, "B": variant},
                    "winners": {"semantic": "tie", "visible": "tie", "design": "tie"},
                    "rationale": "The sides have matching evidence.",
                }
            ],
        }
        return (
            {"case-01": row},
            {"status": "reported", "totals": {"input_tokens": 5}},
            {"case-01": judge._comparison_mapping(pair, blind_seed)},
        )

    monkeypatch.setattr(judge, "_run_compare_pass", run)
    monkeypatch.setattr(judge, "_calibration_validation", lambda _: {"status": "passed"})
    report = judge.compare_cases([baseline], [candidate], tmp_path / "compare-invalid-primary")

    assert calls == 2
    row = report["cases"][0]
    assert row["status"] == "inconclusive"
    assert row["observed_status"] == "invalid_primary"
    assert row["primary"] is None
    assert row["secondary"]["case_id"] == "case-01"
    assert row["recheck"]["selected"] is True
    assert row["recheck"]["agreement"] == "inconclusive"


def test_compare_failure_is_diagnostic_until_calibration_passes(tmp_path, monkeypatch):
    baseline = _bundle(tmp_path / "baseline")
    candidate = _bundle(tmp_path / "candidate")
    calibration_status = {"status": "inconclusive", "reason": "no_certificate"}

    def run(cases, *, blind_seed, **_kwargs):
        pair = cases[0]
        row = {
            "case_id": "case-01",
            "reference_status": "gold",
            "status": "failed",
            "comparisons": [
                {
                    "variant": "executive",
                    "sides": {
                        side: {
                            "status": "failed",
                            "categories": {
                                "semantic": {"status": "failed", "score": 2},
                                "visible": {"status": "passed", "score": 4},
                            },
                            "points": [],
                            "findings": [],
                        }
                        for side in ("A", "B")
                    },
                    "winners": {
                        "semantic": "tie",
                        "visible": "tie",
                        "design": "tie",
                    },
                    "rationale": "Both sides fail the semantic gate.",
                }
            ],
        }
        return (
            {"case-01": row},
            {"status": "unavailable"},
            {"case-01": judge._comparison_mapping(pair, blind_seed)},
        )

    monkeypatch.setattr(judge, "_run_compare_pass", run)
    monkeypatch.setattr(judge, "_calibration_validation", lambda _: calibration_status)
    report = judge.compare_cases([baseline], [candidate], tmp_path / "compare-uncalibrated")

    assert report["cases"][0]["status"] == "inconclusive"
    assert report["cases"][0]["observed_status"] == "failed"
    assert report["status"] == "inconclusive"


@pytest.mark.parametrize("cross_experiment_duplicate", [False, True])
def test_compare_pass_uses_one_isolated_worker_per_complete_case(
    tmp_path, monkeypatch, cross_experiment_duplicate
):
    baseline_path = _bundle(tmp_path / "baseline")
    candidate_path = _bundle(tmp_path / "candidate")
    pair = {
        "case_id": "case-01",
        "baseline": judge._read_bundle(baseline_path),
        "candidate": judge._read_bundle(candidate_path),
    }
    observed = {}

    def run_codex(workdir, _prompt, schema, image_paths, *, worker_alias, **_kwargs):
        index = json.loads((workdir / "packets.json").read_text())
        assert len(index["cases"]) == 1
        assert len(image_paths) == 2
        assert (workdir / "image-manifest.json").is_file()
        packet = json.loads((workdir / index["cases"][0]["packet"]).read_text())
        assert packet["case_id"] == worker_alias
        case_schema = schema["properties"]["case"]["properties"]
        assert case_schema["case_id"]["enum"] == [worker_alias]
        comparisons_schema = case_schema["comparisons"]["items"]
        finding_categories = comparisons_schema["properties"]["sides"]["properties"]["A"][
            "properties"
        ]["findings"]["items"]["properties"]["category"]["enum"]
        assert "duplicates" not in finding_categories
        assert comparisons_schema["properties"]["pair_id"]["enum"] == [
            item["pair_id"] for item in packet["comparisons"]
        ]
        assert not _schema_objects(schema)
        observed["cwd"] = workdir.resolve()
        comparisons = []
        for item in packet["comparisons"]:
            sides = {}
            for side, side_packet in item["sides"].items():
                slides = side_packet["slides"]
                variant = _variant_result()
                sides[side] = {
                    key: variant[key]
                    for key in ("categories", "coverage", "points", "findings", "summary")
                }
                sides[side]["coverage"]["slide_numbers"] = [slide["number"] for slide in slides]
                sides[side]["points"][0]["evidence"] = slides[0]["text"]
                if cross_experiment_duplicate:
                    sides[side]["findings"] = [{"category": "duplicates"}]
            comparisons.append(
                {
                    "pair_id": item["pair_id"],
                    "sides": sides,
                    "winners": {key: "tie" for key in ("semantic", "visible", "design")},
                    "rationale": "Both sides have the same evidence and presentation quality.",
                }
            )
        return (
            {"schema_version": 1, "case": {"case_id": worker_alias, "comparisons": comparisons}},
            {
                "status": "reported",
                "totals": {"input_tokens": 3, "output_tokens": 4},
                "worker_execution": {"status": "cli_completed"},
            },
        )

    monkeypatch.setattr(judge, "_run_codex", run_codex)
    if cross_experiment_duplicate:
        with pytest.raises(judge.WorkerBatchError) as caught:
            judge._run_compare_pass(
                [pair],
                timeout=10,
                blind_seed="fixed-seed",
                pass_number=0,
                artifact_dir=tmp_path / "comparison-pass",
            )
        assert "Paired A/B equality" in caught.value.case_errors["case-01"]["message"]
        return
    results, usage, mapping = judge._run_compare_pass(
        [pair],
        timeout=10,
        blind_seed="fixed-seed",
        pass_number=0,
        artifact_dir=tmp_path / "comparison-pass",
    )

    assert results["case-01"]["status"] == "passed"
    assert usage["execution_mode"] == "isolated_codex_process"
    assert usage["worker_count"] == 1
    assert set(mapping["case-01"]) == {"executive"}
    assert (tmp_path / "comparison-pass" / "worker-001" / "blind-map.json").is_file()


def test_calibration_requires_each_exact_category_on_all_three_repeats(tmp_path, monkeypatch):
    from audit_e2e.controls import _SPEC

    controls_dir = tmp_path / "controls"
    rows = []
    category_by_id = {}
    for index, (category, expected) in enumerate(_SPEC, 1):
        control_id = f"control-{index:02d}"
        bundle = _bundle(controls_dir / control_id, case_id=control_id)
        rows.append(
            {"id": control_id, "category": category, "expected": expected, "bundle": bundle}
        )
        category_by_id[control_id] = category

    missed_category = None
    false_alarm_category = None
    design_false_alarm_category = None
    missing_repeat_at = None
    partial_worker_at = None
    invocations = 0

    def run(cases, **_kwargs):
        nonlocal invocations
        invocations += 1
        if invocations == missing_repeat_at:
            raise TimeoutError("fake missing repeat")
        judged = {}
        for case in cases:
            case_id = case["data"]["case_id"]
            category = category_by_id[case_id]
            positive = category in judge.POSITIVE_CONTROL_CATEGORIES
            detected_category = category
            if category == missed_category:
                detected_category = "omission"
            semantic_defect = category in {
                "omission",
                "number",
                "negation",
                "condition",
                "table_binding",
                "units",
                "provenance",
            }
            visible_defect = category in {"hidden_text", "duplicates", "readability"}
            categories = {
                "semantic": {
                    "status": "failed" if semantic_defect else "passed",
                    "score": 2 if semantic_defect else 4,
                },
                "visible": {
                    "status": "failed" if visible_defect else "passed",
                    "score": 2 if visible_defect else 4,
                },
                "design": {
                    "status": "inconclusive"
                    if positive
                    else "failed"
                    if category in {"template_fidelity", "readability", "duplicates"}
                    else "passed",
                    "score": 3
                    if positive
                    else 2
                    if category in {"template_fidelity", "readability", "duplicates"}
                    else 4,
                },
            }
            if positive and category == design_false_alarm_category:
                categories["design"] = {"status": "failed", "score": 2}
            findings = (
                []
                if positive
                else [
                    {
                        "category": detected_category,
                        "severity": "major",
                        "slide_number": 1,
                        "region": "center",
                        "description": "Controlled defect.",
                        "evidence": "The pilot runs for 12 weeks.",
                        "point_ids": ["point-01"],
                    }
                ]
            )
            if category == false_alarm_category:
                findings.append(
                    {
                        "category": "unsupported",
                        "severity": "major",
                        "slide_number": 1,
                        "region": "center",
                        "description": "A false positive was raised.",
                        "evidence": "The pilot runs for 12 weeks.",
                        "point_ids": ["point-01"],
                    }
                )
            if positive and category == design_false_alarm_category:
                findings.append(
                    {
                        "category": "template_fidelity",
                        "severity": "major",
                        "slide_number": 1,
                        "region": "center",
                        "description": "A design false positive was raised.",
                        "evidence": "The pilot runs for 12 weeks.",
                        "point_ids": [],
                    }
                )
            status = "failed" if semantic_defect or visible_defect else "passed"
            judged[case_id] = {
                "case_id": case_id,
                "reference_status": "gold",
                "status": status,
                "variants": {
                    "executive": {"status": status, "categories": categories, "findings": findings}
                },
            }
        if invocations == partial_worker_at:
            missing_id = "control-01"
            judged.pop(missing_id)
            raise judge.WorkerBatchError(
                "one controlled worker was invalid",
                usage={"status": "partial", "totals": {"input_tokens": 7}},
                workers=[],
                results=judged,
                case_errors={missing_id: {"type": "ValueError", "message": "invalid result"}},
            )
        return judged, {"status": "unavailable"}, {}

    monkeypatch.setattr(judge, "_run_judge_pass", run)
    report = judge.calibrate(rows, tmp_path / "calibration", repeats=3)
    assert report["status"] == "passed"
    assert all(len(row["repeats"][0]["verdicts"]) == 3 for row in report["categories"].values())
    assert report["categories"]["template_fidelity"]["status"] == "passed"

    design_false_alarm_category = "clean"
    invocations = 0
    report = judge.calibrate(rows, tmp_path / "calibration-design-false-alarm", repeats=3)
    assert report["status"] == "passed"
    assert report["categories"]["clean"]["status"] == "passed"
    assert report["categories"]["clean"]["design_status"] == "failed"
    assert report["gates"]["semantic"] == "passed"
    assert report["gates"]["visible"] == "passed"
    assert report["gates"]["design"] == "failed"

    design_false_alarm_category = None
    missed_category = "number"
    invocations = 0
    report = judge.calibrate(rows, tmp_path / "calibration-missed", repeats=3)
    assert report["status"] == "failed"
    assert report["categories"]["number"]["status"] == "failed"

    missed_category = None
    false_alarm_category = "clean"
    invocations = 0
    report = judge.calibrate(rows, tmp_path / "calibration-false-alarm", repeats=3)
    assert report["status"] == "failed"
    assert report["categories"]["clean"]["status"] == "failed"

    false_alarm_category = None
    partial_worker_at = 1
    invocations = 0
    report = judge.calibrate(rows, tmp_path / "calibration-partial-worker", repeats=3)
    assert invocations == 3
    assert report["status"] == "inconclusive"
    assert report["categories"]["clean"]["status"] == "inconclusive"
    assert report["categories"]["number"]["status"] == "passed"
    assert report["control_results"][0]["status"] == "inconclusive"
    assert len(report["usage"]["passes_detail"]) == 3

    partial_worker_at = None
    missing_repeat_at = 2
    invocations = 0
    report = judge.calibrate(rows, tmp_path / "calibration-incomplete", repeats=3)
    assert report["status"] == "inconclusive"
    assert report["repeats"] == 3
    assert len(report["usage"]["passes_detail"]) == 3


def test_calibration_rejects_a_control_detected_in_only_one_of_three_variants(
    tmp_path, monkeypatch
):
    from audit_e2e.controls import _SPEC

    controls_dir = tmp_path / "partial-detection-controls"
    rows = []
    category_by_id = {}
    for index, (category, expected) in enumerate(_SPEC, 1):
        control_id = f"control-{index:02d}"
        bundle = _bundle(controls_dir / control_id, case_id=control_id)
        rows.append(
            {"id": control_id, "category": category, "expected": expected, "bundle": bundle}
        )
        category_by_id[control_id] = category

    semantic_categories = {
        "omission",
        "number",
        "negation",
        "condition",
        "table_binding",
        "units",
        "provenance",
    }
    visible_categories = {"hidden_text", "duplicates", "readability"}
    design_categories = {"template_fidelity", "readability"}
    variant_names = ("executive", "analytical", "story")

    def run(cases, **_kwargs):
        judged = {}
        for case in cases:
            case_id = case["data"]["case_id"]
            category = category_by_id[case_id]
            positive = category in judge.POSITIVE_CONTROL_CATEGORIES
            variants = {}
            for name in variant_names:
                detected = not positive and (category != "number" or name == "executive")
                semantic_failed = category in semantic_categories and detected
                visible_failed = category in visible_categories and detected
                design_failed = category in design_categories and detected
                statuses = {
                    "semantic": "failed" if semantic_failed else "passed",
                    "visible": "failed" if visible_failed else "passed",
                    "design": "failed" if design_failed else "passed",
                }
                findings = (
                    [
                        {
                            "category": category,
                            "severity": "major",
                            "slide_number": 1,
                            "region": "center",
                            "description": "Controlled defect.",
                            "evidence": "The pilot runs for 12 weeks.",
                            "point_ids": ["point-01"],
                        }
                    ]
                    if detected
                    else []
                )
                variant_status = judge._aggregate_status(
                    [statuses["semantic"], statuses["visible"]]
                )
                variants[name] = {
                    "status": variant_status,
                    "categories": {
                        key: {
                            "status": status,
                            "score": 2 if status == "failed" else 4,
                        }
                        for key, status in statuses.items()
                    },
                    "findings": findings,
                }
            judged[case_id] = {
                "case_id": case_id,
                "reference_status": "gold",
                "status": judge._aggregate_status(
                    [variant["status"] for variant in variants.values()]
                ),
                "variants": variants,
            }
        return judged, {"status": "unavailable"}, {}

    monkeypatch.setattr(judge, "_run_judge_pass", run)
    report = judge.calibrate(rows, tmp_path / "partial-detection", repeats=3)

    assert report["status"] == "failed"
    assert report["categories"]["number"]["status"] == "failed"
    verdict = report["categories"]["number"]["repeats"][0]["verdicts"][0]
    assert verdict["required_variants"] == ["analytical", "executive", "story"]
    assert verdict["detected_variants"] == ["executive"]
    assert verdict["gate_failed_variants"] == ["executive"]


def test_stale_calibration_definitions_never_gate_success(monkeypatch):
    definitions = judge._definition_snapshot()
    certificate = {
        "status": "passed",
        "model": judge.MODEL,
        "model_reasoning_effort": judge.MODEL_REASONING_EFFORT,
        **definitions,
        "controls_hash": "d" * 64,
        "repeats": 3,
        "categories": {
            category: {"status": "passed"} for category in judge._required_calibration_categories()
        },
        "gates": {"semantic": "passed", "visible": "passed"},
    }
    assert judge._calibration_validation(certificate)["status"] == "passed"

    original = judge._prompt_hashes
    monkeypatch.setattr(judge, "_prompt_hashes", lambda: {**original(), "judge": "e" * 64})
    assert judge._calibration_validation(certificate)["reason"] == "prompt_hash_mismatch"
    monkeypatch.setattr(judge, "_prompt_hashes", original)
    monkeypatch.setattr(judge, "_controls_builder_hash", lambda: "f" * 64)
    assert judge._calibration_validation(certificate)["reason"] == "controls_builder_changed"


def test_codex_timeout_kills_process_group_and_records_noninteractive_policy(tmp_path, monkeypatch):
    captured = {}

    class Process:
        pid = 12345
        returncode = -9

        def wait(self, timeout=None):
            if timeout is not None:
                raise judge.subprocess.TimeoutExpired("codex", timeout)

        def kill(self):
            captured["fallback_kill"] = True

    monkeypatch.setattr(judge.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(
        judge.subprocess,
        "Popen",
        lambda command, **_kwargs: captured.update(command=command) or Process(),
    )
    monkeypatch.setattr(judge.os, "killpg", lambda pid, signal: captured.update(killed_group=pid))
    with pytest.raises(TimeoutError):
        judge._run_codex(
            tmp_path,
            "blind judge prompt",
            judge.JUDGE_SCHEMA,
            [],
            timeout=0.01,
            artifact_dir=tmp_path / "artifacts",
            worker_alias="case-001",
        )
    command = captured["command"]
    assert captured["killed_group"] == 12345
    assert "--ignore-user-config" in command and "--ignore-rules" in command
    assert "--enable" not in command
    assert "agents.enabled=false" in command
    assert "read-only" in command and 'approval_policy="never"' in command


def test_judge_pass_uses_isolated_single_case_workers_with_bounded_parallelism(
    tmp_path, monkeypatch
):
    bundles = [
        judge._read_bundle(_bundle(tmp_path / f"case-{index:02d}", case_id=f"case-{index:02d}"))
        for index in range(1, 5)
    ]
    active = 0
    peak = 0
    calls = []
    lock = threading.Lock()

    def run_codex(workdir, prompt, schema, image_paths, *, worker_alias, **_kwargs):
        nonlocal active, peak
        index = json.loads((workdir / "packets.json").read_text())
        assert len(index["cases"]) == 1
        assert not _schema_objects(schema)
        assert image_paths and (workdir / "image-manifest.json").is_file()
        assert "subagent" not in prompt.lower() and "spawn_agent" not in prompt.lower()
        packet = json.loads((workdir / index["cases"][0]["packet"]).read_text())
        assert packet["case_id"] == worker_alias
        case_schema = schema["properties"]["case"]["properties"]
        assert case_schema["case_id"]["enum"] == [worker_alias]
        variant_schema = case_schema["variants"]["items"]
        assert variant_schema["properties"]["variant_id"]["enum"] == sorted(
            variant["variant_id"] for variant in packet["variants"]
        )
        expected_point_ids = [row["id"] for row in packet["reference"]["points"]]
        assert variant_schema["properties"]["points"]["items"]["properties"]["point_id"][
            "enum"
        ] == sorted(expected_point_ids)
        with lock:
            active += 1
            peak = max(peak, active)
            calls.append((worker_alias, workdir.resolve()))
        time.sleep(0.02)
        with lock:
            active -= 1
        result = {
            "schema_version": 1,
            "case": {
                "case_id": worker_alias,
                "variants": [
                    {"variant_id": variant["variant_id"], **_variant_result()}
                    for variant in packet["variants"]
                ],
            },
        }
        return result, {
            "status": "reported",
            "totals": {"input_tokens": 1, "output_tokens": 2},
            "worker_execution": {"status": "cli_completed"},
        }

    monkeypatch.setattr(judge, "_run_codex", run_codex)
    results, usage, metadata = judge._run_judge_pass(
        bundles, timeout=10, pass_number=0, artifact_dir=tmp_path / "passes"
    )

    assert set(results) == {f"case-{index:02d}" for index in range(1, 5)}
    assert peak == 3
    assert len({path for _, path in calls}) == 4
    assert usage["execution_mode"] == "isolated_codex_process"
    assert usage["worker_count"] == 4
    assert usage["totals"] == {"input_tokens": 4, "output_tokens": 8}
    assert [row["case_alias"] for row in usage["workers"]] == [
        f"case-{index:03d}" for index in range(1, 5)
    ]
    assert len(metadata["cases"]) == 4


def test_judge_pass_keeps_valid_cases_when_one_worker_result_is_invalid(tmp_path, monkeypatch):
    bundles = [
        judge._read_bundle(_bundle(tmp_path / f"case-{index:02d}", case_id=f"case-{index:02d}"))
        for index in range(1, 3)
    ]

    def run_codex(workdir, _prompt, _schema, _images, *, worker_alias, **_kwargs):
        packet_index = json.loads((workdir / "packets.json").read_text())
        packet = json.loads((workdir / packet_index["cases"][0]["packet"]).read_text())
        variants = [
            {"variant_id": variant["variant_id"], **_variant_result()}
            for variant in packet["variants"]
        ]
        if worker_alias == "case-002":
            variants = []
        return (
            {"schema_version": 1, "case": {"case_id": worker_alias, "variants": variants}},
            {
                "status": "reported",
                "totals": {"input_tokens": 3},
                "worker_execution": {"status": "cli_completed"},
            },
        )

    monkeypatch.setattr(judge, "_run_codex", run_codex)
    with pytest.raises(judge.WorkerBatchError) as caught:
        judge._run_judge_pass(bundles, timeout=10, pass_number=0)

    error = caught.value
    assert set(error.results) == {"case-01"}
    assert (
        error.case_errors["case-02"]["message"]
        == "Structured judge result omitted or added variants"
    )
    assert len(error.workers) == 2
    assert error.usage["totals"] == {"input_tokens": 6}


def test_partial_usage_keeps_known_subtotal_and_marks_started_unknown_workers():
    usage = judge._aggregate_worker_usage(
        [
            {
                "case_alias": "case-001",
                "started": True,
                "usage": {"status": "reported", "totals": {"input_tokens": 17}},
            },
            {
                "case_alias": "case-002",
                "started": True,
                "usage": {"status": "unavailable", "totals": {}},
            },
            {"case_alias": "case-003", "started": False, "usage": None},
        ]
    )

    assert usage["status"] == "partial"
    assert usage["totals"] == {"input_tokens": 17}
    assert usage["started_worker_count"] == 2
    assert usage["not_started_worker_count"] == 1
    assert usage["workers_missing_usage"] == ["case-002"]
    total = judge._sum_usage([{"pass": "repeat-1", "usage": usage}])
    assert total["status"] == "partial"
    assert total["totals"] == {"input_tokens": 17}


def test_judge_report_keeps_good_cases_when_another_primary_worker_fails(tmp_path, monkeypatch):
    bundles = [
        _bundle(tmp_path / f"case-{index:02d}", case_id=f"case-{index:02d}")
        for index in range(1, 3)
    ]
    monkeypatch.setattr(
        judge, "_calibration_validation", lambda _: {"status": "passed", "controls_hash": "a" * 64}
    )
    calls = 0

    def run(cases, **_kwargs):
        nonlocal calls
        calls += 1
        good = {"case-01": _validated_case("case-01")}
        if calls == 1:
            raise judge.WorkerBatchError(
                "one case worker failed",
                usage={"status": "partial", "totals": {"input_tokens": 5}},
                workers=[],
                results=good,
                case_errors={"case-02": {"type": "ValueError", "message": "invalid result"}},
            )
        assert {case["data"]["case_id"] for case in cases} == {"case-01", "case-02"}
        return (
            {
                **good,
                "case-02": _validated_case("case-02"),
            },
            {"status": "reported", "totals": {"input_tokens": 7}},
            {},
        )

    monkeypatch.setattr(judge, "_run_judge_pass", run)
    report = judge.judge_cases(bundles, tmp_path / "report")

    assert report["cases"][0]["case_id"] == "case-01"
    assert report["cases"][0]["status"] == "passed"
    assert report["cases"][1]["case_id"] == "case-02"
    assert report["cases"][1]["status"] == "inconclusive"
    assert report["cases"][1]["recheck"]["selected"] is True
    assert report["cases"][1]["secondary"]["case_id"] == "case-02"
    assert report["cases"][1]["error"]["message"] == "invalid result"
    assert report["usage"]["status"] == "partial"
