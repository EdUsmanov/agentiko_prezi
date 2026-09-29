from copy import deepcopy
from types import SimpleNamespace

from studio.contents.archetypes import reviewed_editorial_report
from studio.templates.archetype_catalog import VERSION


def test_editorial_report_keeps_slide_roles_separate_from_slot_classifier():
    groups = [
        {"title": "Обложка", "purpose": "cover", "fact_ids": ["summary-1-1"]},
        {
            "title": "Порядок работы",
            "purpose": "process",
            "fact_ids": ["summary-2-1", "summary-2-2"],
        },
        {"title": "Данные", "purpose": "metrics", "fact_ids": ["summary-3-1", "table-3"]},
    ]
    package = SimpleNamespace(analysis={"narrative": {"groups": deepcopy(groups)}})
    report = reviewed_editorial_report(package)
    assert report["status"] == "completed"
    assert report["catalog_version"] == VERSION
    assert report["reviewed_groups"] == groups
    assert report["units"] == []  # No fabricated evidence slots or rendering contracts.
    report["reviewed_groups"][0]["fact_ids"].clear()
    assert package.analysis["narrative"]["groups"] == groups


def test_empty_editorial_report_does_not_claim_completed_checks():
    report = reviewed_editorial_report(SimpleNamespace(analysis={}))
    assert report["status"] == "not_run"
    assert report["reviewed_groups"] == report["units"] == []
