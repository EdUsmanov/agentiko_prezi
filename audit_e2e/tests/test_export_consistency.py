import pytest

from audit_e2e.export_consistency import VERSION, _norm, assess_export_consistency, html_text


def _bundle(
    pdf="The sample includes 73 requests.",
    html="The sample includes 73 requests.",
    *,
    text_contract=False,
):
    return {
        "reference": {
            "points": [{"id": "sample", "quote": "73 requests", "required": True}],
            "requirements": [{"kind": "text_export_agreement", "status": "gold"}]
            if text_contract
            else [],
        },
        "variants": {
            "executive": {
                "html": "deck.html",
                "html_text": html,
                "slides": [
                    {
                        "number": 1,
                        "text": "The sample includes 73 requests.",
                        "pdf_visible_text": pdf,
                    }
                ],
            }
        },
    }


def test_hidden_html_or_script_literals_are_not_visible_fact_coverage():
    assert (
        html_text(
            "<html><head><title>secret</title></head><body><script>73 requests</script><div hidden>73 requests</div><p>71 requests</p></body></html>"
        ).strip()
        == "71 requests"
    )


def test_independent_text_contract_detects_corrupt_pdf_and_html():
    assert assess_export_consistency(_bundle(text_contract=True))["status"] == "passed"
    for fmt in ("pdf", "html"):
        report = assess_export_consistency(
            _bundle(**{fmt: "The sample includes 99 requests."}, text_contract=True)
        )
        assert report["status"] == "failed"
        assert report["findings"][0]["slide_numbers"] == [1]
        assert report["findings"][0]["point_id"] == "sample"


def test_unextractable_raster_or_paraphrase_is_inconclusive_not_a_false_visual_failure():
    result = assess_export_consistency(_bundle(pdf=""))
    assert result["status"] == "inconclusive"
    assert result["scope"] == "literal_source_anchor_consistency_not_visual_equivalence"


def _anchor_bundle(quote, pdf, *, text_contract=True):
    bundle = _bundle(pdf=pdf, html=quote, text_contract=text_contract)
    bundle["reference"]["points"] = [{"id": "anchor", "quote": quote, "required": True}]
    bundle["variants"]["executive"]["slides"][0]["text"] = quote
    return bundle


def test_pdf_bullet_after_sentence_boundary_preserves_anchor_and_special_characters():
    quote = (
        "Bookings and cancellations are event counts. Attendee visits count people entering a room. "
        "The reading is −5 °C; it is not a forecast."
    )
    pdf = quote.replace(". Attendee", ".• Attendee")

    result = assess_export_consistency(_anchor_bundle(quote, pdf))

    assert result["status"] == "passed"
    assert result["coverage"][0]["literal_presence"] == {
        "pptx": True,
        "pdf": True,
        "html": True,
    }
    normalized = _norm("The reading is −5 °C; it is not a forecast.")
    assert "the reading is −5 °c; it is not a forecast." == normalized
    assert _norm("A•B") == "a•b"


def test_pdf_bullet_normalization_does_not_hide_a_real_omission():
    quote = (
        "Bookings and cancellations are event counts. Attendee visits count people entering a room."
    )
    result = assess_export_consistency(
        _anchor_bundle(quote, "Bookings and cancellations are event counts.•", text_contract=True)
    )

    assert result["status"] == "failed"
    assert result["version"] == VERSION == "source-anchor-format-agreement-3"
    assert result["findings"][0]["literal_presence"] == {
        "pptx": True,
        "pdf": False,
        "html": True,
    }


@pytest.mark.parametrize("punctuation", [";", ":"])
def test_pdf_clause_bullet_keeps_number_negation_and_condition(punctuation):
    quote = f"The pilot lasts 12 weeks{punctuation} expand only if reviewed, not automatically."
    pdf = quote.replace(f"{punctuation} ", f"{punctuation}• ")
    assert assess_export_consistency(_anchor_bundle(quote, pdf))["status"] == "passed"
    for before, after in [
        ("12 weeks", "21 weeks"),
        ("not automatically", "automatically"),
        ("only if reviewed", "without review"),
    ]:
        damaged = pdf.replace(before, after)
        assert assess_export_consistency(_anchor_bundle(quote, damaged))["status"] == "failed"
