import shutil
from dataclasses import replace

from studio.preparation.contracts import PreparationRequest, PreparationServices
from studio.preparation.orchestrator import run_preparation, run_template_preanalysis
from studio.jobs.store import Store
from studio.templates.template_cache import TemplateCache
from studio.models import Plans, SlidePlan, VariantPlan


def test_template_only_and_full_preparation_share_cache_and_close_gateway_once(prepared, content):
    settings, store, prior = prepared
    source = store.directory(prior.id)
    assert TemplateCache(settings).save(prior.template, source, prior.analysis)
    gateways = []

    class Gateway:
        def __init__(self, settings):
            self.settings = settings
            self.calls = []
            self.usage = {}
            self.closed = 0
            gateways.append(self)

        async def aclose(self):
            self.closed += 1

    async def intelligence(package, path, gateway, progress, template_result):
        assert template_result.analysis["template_cache"]["hit"]
        package.analysis = template_result.analysis
        return package

    def no_analysis(*args, **kwargs):
        raise AssertionError("Cached template must not be analyzed again")

    services = PreparationServices(no_analysis, intelligence, Gateway, lambda: {}, lambda: "test")

    template_job = store.create("template_preanalysis")
    template_dir = store.directory(template_job["id"])
    shutil.copyfile(source / "input.pptx", template_dir / "input.pptx")
    run_template_preanalysis(store, template_job["id"], settings, services)
    assert store.get(template_job["id"])["state"] == "ready"
    assert not (template_dir / "package.json").exists()
    assert not (template_dir / "analysis-input.json").exists()

    full_job = store.create("preparation", {"template_name": "same.pptx"})
    shutil.copyfile(source / "input.pptx", store.directory(full_job["id"]) / "input.pptx")
    run_preparation(
        store,
        full_job["id"],
        PreparationRequest(content, "Команда", "", 5),
        settings,
        services,
    )
    assert store.get(full_job["id"])["state"] == "ready", store.get(full_job["id"])
    assert [gateway.closed for gateway in gateways] == [1, 1]


def test_invalid_content_stops_before_template_model_and_cache(prepared, tmp_path):
    original_settings, original_store, prior = prepared
    settings = replace(original_settings, data_dir=tmp_path / "new-data")
    store = Store(settings.data_dir)
    job = store.create("preparation")
    shutil.copyfile(
        original_store.directory(prior.id) / "input.pptx",
        store.directory(job["id"]) / "input.pptx",
    )

    def gateway_factory(settings):
        raise AssertionError("Invalid content must stop before model gateway creation")

    services = PreparationServices(
        lambda *args, **kwargs: prior.template.model_copy(deep=True),
        lambda *args, **kwargs: None,
        gateway_factory,
        lambda: {},
        lambda: "test",
    )
    run_preparation(store, job["id"], PreparationRequest("# Проект", "", "", 5), settings, services)
    assert store.get(job["id"])["state"] == "failed"
    assert not TemplateCache(settings).location(prior.template.sha256).exists()


def test_explicit_brief_can_prepare_short_input_without_auto_generation(prepared):
    settings, store, prior = prepared
    job = store.create("preparation", {"template_name": "same.pptx"})
    shutil.copyfile(
        store.directory(prior.id) / "input.pptx", store.directory(job["id"]) / "input.pptx"
    )

    class Gateway:
        def __init__(self, settings):
            self.settings = settings
            self.calls = []
            self.usage = {}

        async def aclose(self):
            pass

    async def intelligence(package, path, gateway, progress, template_result):
        package.analysis = template_result.analysis
        package.brief_evidence = package.content.model_copy(deep=True)
        package.analysis["editorial"] = {
            "plan": {"slides": [{"source_table_id": None}]},
            "provenance": [{"fact_id": "f1", "evidence": [{"fact_id": "f1"}]}],
        }
        package.prepared_plans = Plans(
            variants=[
                VariantPlan(
                    key=key,
                    title=key,
                    slides=[SlidePlan(title="Пилот", purpose="content", fact_ids=["f1"])],
                )
                for key in ("executive", "analytical", "story")
            ]
        )
        from studio.contents.brief import build_draft

        package.draft = build_draft(package)
        assert package.original_content.facts[0].source == "user_text"
        return package

    services = PreparationServices(
        lambda *args, **kwargs: prior.template.model_copy(deep=True),
        intelligence,
        Gateway,
        lambda: {},
        lambda: "test",
    )
    run_preparation(
        store,
        job["id"],
        PreparationRequest("Идея сервиса для команд.", "", "", 10, input_mode="brief"),
        settings,
        services,
    )
    saved = store.get(job["id"])
    assert saved["state"] == "ready", saved.get("error") or saved
    assert saved["auto_generation"] == "needs_confirmation"
    assert saved["draft_hash"]


def test_brief_preparation_builds_approvable_draft_from_actual_plan(prepared, monkeypatch):
    from studio.contents import narrative
    from studio.preparation import orchestrator
    from studio.pipeline import load_package

    base_settings, store, prior = prepared
    settings = replace(base_settings, mode="api")
    source = store.directory(prior.id)
    assert TemplateCache(settings).save(prior.template, source, prior.analysis)
    job = store.create("preparation", {"template_name": "same.pptx"})
    shutil.copyfile(source / "input.pptx", store.directory(job["id"]) / "input.pptx")

    class Gateway:
        def __init__(self, settings):
            self.settings = settings
            self.calls = []
            self.usage = {}

        async def json_request(self, name, payload, **kwargs):
            assert name == "author"
            names = "пилот дизайн аудит экспорт шаблон команда продукт процесс сценарий".split()
            return {
                "proposals": [
                    f"Предлагается обсудить {name} с командой и определить проверяемый следующий шаг."
                    for name in names[: payload["requested_count"]]
                ]
            }

        async def aclose(self):
            pass

    async def editorial(package, gateway, progress=None):
        facts = package.content.facts
        package.analysis["editorial"] = {
            "plan": {
                "slides": [
                    {
                        "title": fact.text[:45],
                        "purpose": "content",
                        "bullets": [{"text": fact.text, "evidence": [{"fact_id": fact.id}]}],
                        "source_table_id": None,
                    }
                    for i, fact in enumerate(facts, 1)
                ]
            },
            "provenance": [
                {"fact_id": fact.id, "evidence": [{"fact_id": fact.id}]} for fact in facts
            ],
        }
        package.analysis["narrative"] = {"status": "completed"}
        return True

    def storyboard(package):
        package.analysis["storyboard"] = [
            SlidePlan(title=fact.text[:45], purpose="content", fact_ids=[fact.id]).model_dump()
            for i, fact in enumerate(package.content.facts, 1)
        ]
        from studio.models import SlideBudget

        package.control.slide_budget = SlideBudget.model_validate(
            {
                "status": "adjusted",
                "requested": package.constraints.slides,
                "planned": 10,
                "message": "Предложено десять слайдов",
            }
        )

    monkeypatch.setattr(narrative, "prepare_narrative", editorial)
    monkeypatch.setattr(narrative, "narrative_storyboard", storyboard)

    def fail(*args):
        raise args[-1]

    monkeypatch.setattr(orchestrator, "record_preparation_failure", fail)
    services = PreparationServices(
        lambda *args, **kwargs: prior.template.model_copy(deep=True),
        __import__(
            "studio.preparation.intelligence", fromlist=["prepare_intelligence"]
        ).prepare_intelligence,
        Gateway,
        lambda: {},
        lambda: "test",
    )
    run_preparation(
        store,
        job["id"],
        PreparationRequest(
            "Сервис помогает команде готовить презентации.", "", "", 10, input_mode="brief"
        ),
        settings,
        services,
    )
    saved = store.get(job["id"])
    assert saved["state"] == "ready", saved.get("error") or saved
    package = load_package(store, job["id"])
    assert (
        not {"model_mode", "planning_status", "planning_source", "planned_slides", "slide_budget"}
        & package.analysis.keys()
    )
    assert saved["control"]["planning_status"] == saved["analysis"]["planning_status"]
    assert saved["control"]["slide_budget"] == saved["analysis"]["slide_budget"]
    assert package.manifest["execution_kind"] == settings.execution_kind
    assert len(package.original_content.facts) == 1
    assert len(package.brief_evidence.facts) == 10
    assert package.draft.slides[1].bullets[0].proposed
    assert saved["draft_hash"] and saved["auto_generation"] == "needs_confirmation"
