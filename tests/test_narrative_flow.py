import asyncio
from types import SimpleNamespace
import pytest
from studio.contents.parsing import insufficient_material, parse_content, parse_constraints
from studio.contents.narrative import validate_narrative, choose_visualization, prepare_narrative
from studio.models import PreparationControl, SlideBudget, TableData


def response(content, quotes=None, rows=None, relationship="none"):
    return {
        "slides": [
            {
                "title": "Результаты",
                "excerpts": [
                    {"fact_id": f.id, "quotes": quotes or [f.text]} for f in content.facts
                ],
                "rows": rows or [],
                "relationship": relationship,
            }
        ]
    }


def test_ranges_and_explicit_override():
    for preset, count in [("mini", 5), ("standard", 10), ("large", 20)]:
        c = parse_constraints(None, "", "", preset)
        assert c.slides == count and c.summarize and c.confirm_plan
    assert parse_constraints(None, "", "ровно 7 слайдов", "mini").slides == 7


def test_short_brief_reports_missing_material_before_model_analysis():
    brief = parse_content("О создании автодизайнера командой из трёх человек для хакатона.")
    assert "Добавьте отдельные факты" in insufficient_material(
        brief, parse_constraints(None, "жюри", "", "large")
    )
    detailed = parse_content(
        "\n".join(f"Факт {i}: этап проекта описан отдельно." for i in range(12))
    )
    assert insufficient_material(detailed, parse_constraints(None, "жюри", "", "large")) is None


def test_continuous_prose_and_lowercase_clauses_keep_numeric_tokens():
    c = parse_content("Запуск в 2025 году; выручка 12,5 млн. далее масштабирование.")
    assert len(c.facts) == 3
    assert "12,5" in c.facts[1].text


def test_summary_cannot_omit_facts_numbers_or_negations():
    c = parse_content("Компания не достигла 25% роста.")
    for text in ["Компания достигла 25% роста.", "25% роста.", "Компания не достигла роста."]:
        with pytest.raises(ValueError):
            validate_narrative(response(c, [text]), c)
    assert validate_narrative(response(c), c)
    with pytest.raises(ValueError):
        validate_narrative({"slides": []}, c)


def test_data_association_must_quote_same_fact():
    c = parse_content("Север — 25%. Юг — 75%.")
    rows = [
        {"fact_id": "f1", "label": "Север", "value": "25%"},
        {"fact_id": "f2", "label": "Юг", "value": "75%"},
    ]
    assert validate_narrative(response(c, rows=rows, relationship="share"), c)
    rows[0]["value"] = "75%"
    with pytest.raises(ValueError):
        validate_narrative(response(c, rows=rows, relationship="share"), c)


@pytest.mark.parametrize(
    "rows,relation,expected",
    [
        ([["2024", "12 млн"], ["2025", "15 млн"]], "time", "line"),
        ([["A", "25%"], ["B", "75%"]], "share", "pie"),
        ([["A", "25%"], ["B", "35%"]], "share", "column"),
        ([["A", "25%"], ["B", "35 млн"]], "comparison", "table"),
        ([["A", "25"], ["B", "35"]], "time", "table"),
    ],
)
def test_chart_selection(rows, relation, expected):
    t = TableData(id="t1", headers=["Категория", "Значение"], rows=rows)
    assert choose_visualization(t, relation) == expected


def test_narrative_keeps_original_and_builds_provenance(monkeypatch):
    from studio.contents import narrative_layout as narrative

    monkeypatch.setattr(
        narrative,
        "narrative_storyboard",
        lambda p: setattr(p.control, "slide_budget", SlideBudget(status="adjusted", planned=1)),
    )
    c = parse_content("Север — 25%. Юг — 75%.")
    source = c.model_copy(deep=True)
    p = SimpleNamespace(
        content=c,
        original_content=source,
        constraints=parse_constraints(1, "", "", "mini"),
        template=SimpleNamespace(patterns=[]),
        analysis={},
        control=PreparationControl(),
    )

    class Gateway:
        settings = SimpleNamespace(mode="api")

        async def json_request(self, stage, payload, **kwargs):
            if stage == "editorial_review":
                return {
                    "claims": [{"claim_id": "s1b1", "supported": True, "meaning_preserved": True}],
                    "narrative_coherent": True,
                }
            return {
                "slides": [
                    {
                        "title": "Доли регионов",
                        "purpose": "composition",
                        "bullets": [
                            {
                                "text": "Север — 25%, Юг — 75%.",
                                "evidence": [{"fact_id": f.id, "quote": f.text} for f in c.facts],
                            }
                        ],
                        "rows": [
                            {"fact_id": "f1", "label": "Север", "value": "25%"},
                            {"fact_id": "f2", "label": "Юг", "value": "75%"},
                        ],
                        "relationship": "share",
                    }
                ],
                "omitted": [],
            }

    assert asyncio.run(prepare_narrative(p, Gateway()))
    assert p.content.tables[0].visualization == "pie"
    assert len(p.original_content.facts) == 2 and not p.original_content.tables
    assert p.analysis["editorial"]["provenance"][0]["evidence"][1]["fact_id"] == "f2"


def test_revision_reads_original_instead_of_previous_summary(
    prepared, monkeypatch, preparation_worker
):
    import time
    from fastapi.testclient import TestClient
    from studio import app as module

    settings, store, package = prepared
    original = package.content.model_copy(deep=True)
    package.original_content = original
    package.content.facts[0].text = "Старое сокращение"
    package.constraints.summarize = True
    captured = []

    def fake_prepare(
        store, jid, text, audience, instructions, slides, settings, content_model, base
    ):
        captured.append((content_model.model_dump(), slides, base.model_dump()))
        store.update(jid, "ready", analysis={}, auto_generation="cancelled")

    preparation_worker(fake_prepare)
    monkeypatch.setattr("studio.presentation_service.load_package", lambda *_: package)
    with TestClient(module.create_app(settings)) as client:
        reply = client.post(
            f"/api/packages/{package.id}/revise",
            json={"slides": 3, "instructions": "Выделить главное"},
        )
        assert reply.status_code == 202, reply.text
        for _ in range(100):
            if captured:
                break
            time.sleep(0.01)
    assert captured[0][0] == original.model_dump()
    assert captured[0][1] == 3 and captured[0][2]["summarize"]


def test_due_timer_accepts_proposal_once_and_records_choice(tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from studio import app as module
    from studio.config import Settings

    application = module.create_app(Settings(data_dir=tmp_path))
    store = application.state.store
    prep = store.create("preparation")
    pid = prep["id"]
    store.update(
        pid,
        "ready",
        constraints={"confirm_plan": True},
        auto_generation="scheduled",
        auto_generate_at=time.time() - 1,
    )
    package = SimpleNamespace(
        input_mode="content",
        constraints=SimpleNamespace(confirm_plan=True, slides=10),
        analysis={},
        control=PreparationControl(
            planned_slides=7,
            slide_budget=SlideBudget(status="adjusted", message="Предложено 7"),
        ),
    )
    monkeypatch.setattr(
        application.state.presentation_service, "load_operation", lambda *_: package
    )
    calls = []

    class Pipe:
        async def read(self, n):
            return b""

    class Process:
        pid = 999999
        returncode = 0
        stdout = Pipe()

        async def wait(self):
            return 0

    async def spawn(*args, **kwargs):
        calls.append(args)
        store.update(args[3], "completed")
        return Process()

    monkeypatch.setattr("studio.jobs.runtime.asyncio.create_subprocess_exec", spawn)
    with TestClient(application) as client:
        for _ in range(100):
            job = store.get(pid)
            if job.get("generation_id"):
                break
            time.sleep(0.01)
        assert job["auto_generation"] == "started"
        generated = store.get(job["generation_id"])
        assert generated["slide_count_decision"]["mode"] == "automatic"
        assert generated["slide_count_decision"]["count"] == 7
        reply = client.post(
            "/api/generate", json={"package_id": pid, "accept_adjusted_slide_count": True}
        )
        assert reply.json()["id"] == generated["id"]
        assert len(calls) == 1
