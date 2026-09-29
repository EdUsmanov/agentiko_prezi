from audit_e2e.export_consistency import assess_export_consistency, html_text


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
