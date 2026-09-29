from copy import deepcopy
from types import SimpleNamespace

import pytest

from studio.checks.repair_errors import PlanValidationError
from studio.contents.editorial_domain import apply_plan, validate_plan
from studio.contents.parsing import parse_content


@pytest.mark.parametrize("label,value", [("Requests", "40"), ("North", "40")])
def test_table_fact_cannot_be_flattened_into_partial_chart_rows(label, value):
    content = parse_content(
        "# Data\n| Team | Requests | Hours |\n|---|---:|---:|\n"
        "| North | 40 | 18 |\n| South | 28 | 21 |"
    )
    table = content.tables[0]
    fid = next(f.id for f in content.facts if f.source == table.id)
    raw = {
        "slides": [
            {
                "title": "Data",
                "bullets": [{"text": "Team data", "evidence": [{"fact_id": fid}]}],
                "chart_type": "column",
                "relationship": "comparison",
                "rows": [
                    {"fact_id": fid, "label": label, "value": value},
                    {"fact_id": fid, "label": "South", "value": "28"},
                ],
            }
        ]
    }
    with pytest.raises(PlanValidationError) as raised:
        validate_plan(raw, content, (1, 1))
    issue = raised.value.issues[0]
    assert issue.code == "table_binding_required"
    assert "source_table_id='t1'" in issue.message
    assert "rows=[]" in issue.message

    corrected = deepcopy(raw)
    corrected["slides"][0].update(
        source_table_id=table.id,
        source_columns=[],
        rows=[],
        chart_type="table",
        relationship="table",
    )
    accepted = validate_plan(corrected, content, (1, 1))
    package = SimpleNamespace(
        content=content.model_copy(deep=True),
        original_content=content.model_copy(deep=True),
        analysis={},
    )
    apply_plan(package, accepted, {}, (1, 1))
    assert package.content.tables[0].headers == table.headers
    assert package.content.tables[0].rows == table.rows
