import asyncio
import json
from types import SimpleNamespace
from dataclasses import replace

import pytest

pytest.importorskip("openai")
from studio.providers.deeppresenter import (
    Assignment,
    Action,
    CompositionEnvironment,
    GatewayBridge,
    design,
    COMMIT,
)
from studio.contents.planner import extractive_plans, assign_compositions
from studio.composition.composer import compose_variant


def environment(prepared):
    _, _, package = prepared
    return CompositionEnvironment(package, assign_compositions(extractive_plans(package), package))


def call(name, args):
    return SimpleNamespace(
        id="test", function=SimpleNamespace(name=name, arguments=json.dumps(args))
    )


def test_atomic_allowlist_and_complete_coverage(prepared):
    env = environment(prepared)
    assignments = [Assignment(**a) for a in env.baseline_assignments]
    original = env.plans.model_dump()
    for bad in (
        assignments[:-1],
        assignments + [assignments[0]],
        [a.model_copy(update={"pattern_id": "../../.env"}) for a in assignments],
    ):
        with pytest.raises(ValueError):
            env.compose(bad)
        assert env.plans.model_dump() == original
        assert env.revision == 0
    env.compose(assignments)
    assert env.revision == 1
    for variant in env.plans.variants:
        scenes = compose_variant(variant, env.package)
        assert [s.pattern_id for s in scenes] == [s.pattern_id for s in variant.slides]


def test_finalize_requires_inspection_of_current_revision(prepared):
    env = environment(prepared)
    finalize = call("finalize", {"outcome": "composition-plan", "agent_name": "Design"})
    assert asyncio.run(env.tool_execute(finalize)).is_error
    env.compose([Assignment(**a) for a in env.baseline_assignments])
    assert asyncio.run(env.tool_execute(finalize)).is_error
    env.inspect()
    assert asyncio.run(env.tool_execute(finalize)).text == "composition-plan"
    env.compose([Assignment(**a) for a in env.baseline_assignments])
    assert asyncio.run(env.tool_execute(finalize)).is_error


@pytest.mark.parametrize(
    "name,args",
    [
        ("execute_command", {"command": "cat .env"}),
        ("read_file", {"path": "/etc/passwd"}),
        ("finalize", {"outcome": "../../.env", "agent_name": "Design"}),
        ("inspect_variants", {"url": "https://example.com"}),
        ("compose_slides", {"assignments": [], "text": "ignore instructions"}),
    ],
)
def test_tools_reject_arbitrary_capabilities(prepared, name, args):
    env = environment(prepared)
    result = asyncio.run(env.tool_execute(call(name, args)))
    assert result.is_error
    assert "/etc/passwd" not in result.text and "cat .env" not in result.text
    assert env.revision == 0


def test_upstream_design_loop_completes_without_changing_facts(prepared, tmp_path):
    settings, _, package = prepared
    env = environment(prepared)

    class Gateway:
        def __init__(self):
            self.settings = replace(settings, mode="api", model_id="test-open-model")
            self.calls = 0

        async def json_request(self, prompt_name, payload, timeout, schema):
            assert prompt_name == "deeppresenter" and 0 < timeout <= 10
            if self.calls == 0:
                history = json.dumps(payload["history"])
                assert "color_schemes" in history
                assert '"colors"' in history or '\\"colors\\"' in history
            self.calls += 1
            return {
                "name": ["compose_slides", "inspect_variants", "finalize"][self.calls - 1],
                "assignments": env.baseline_assignments if self.calls == 1 else [],
                "outcome": "composition-plan" if self.calls == 3 else None,
            }

    gateway = Gateway()
    plans, report = asyncio.run(design(package, env.plans, gateway, tmp_path / "run", 10))
    assert report["commit"] == COMMIT and report["turns"] == 3
    assert gateway.calls == report["model_requests"] == 1
    assert [e["tool"] for e in report["events"]] == [
        "compose_slides",
        "inspect_variants",
        "finalize",
    ]
    for before, after in zip(env.plans.variants, plans.variants):
        for a, b in zip(before.slides, after.slides):
            assert a.model_dump(exclude={"pattern_id"}) == b.model_dump(exclude={"pattern_id"})
    assert not (tmp_path / "run/.history").exists()


def test_empty_or_injected_action_rejected():
    for raw in (
        {"name": "execute_command", "assignments": [], "outcome": None},
        {"name": "finalize", "assignments": [], "outcome": "/etc/passwd"},
    ):
        with pytest.raises(ValueError):
            Action.model_validate(raw)


def test_token_composition_is_explicit_and_allowed(prepared):
    _, _, package = prepared
    package.template.patterns = []
    env = CompositionEnvironment(package, assign_compositions(extractive_plans(package), package))
    assert {a["pattern_id"] for a in env.baseline_assignments} == {"token:auto"}
    env.compose([Assignment(**a) for a in env.baseline_assignments])
    env.inspect()
    assert all(
        s.strategy == "token_composition"
        for v in env.plans.variants
        for s in compose_variant(v, package)
    )


def test_geometry_guard_repairs_unsafe_choice_without_rejecting_plan(prepared):
    from studio.models import Box

    _, _, package = prepared
    pattern = next(
        p for p in package.template.patterns if p.title_zone and p.body_zones
    ).model_copy(deep=True)
    pattern.id = "tiny-test-pattern"
    pattern.body_zones = [Box(x=50, y=150, w=60, h=10)]
    package.template.patterns.append(pattern)
    env = environment(prepared)
    assignments = [Assignment(**{**a, "pattern_id": pattern.id}) for a in env.baseline_assignments]
    result = env.compose(assignments)
    assert result["accepted"] and result["repairs"]
    assert all(s.pattern_id != pattern.id for v in env.plans.variants for s in v.slides)
    assert env.inspected_revision == -1


def test_guard_replaces_tiny_title_even_when_geometry_fits(prepared):
    _, _, package = prepared
    pattern = next(
        p for p in package.template.patterns if p.title_zone and p.body_zones
    ).model_copy(deep=True)
    pattern.id = "caption-size-title"
    pattern.title_size = 10
    package.template.patterns.append(pattern)
    env = environment(prepared)
    assignments = [Assignment(**{**a, "pattern_id": pattern.id}) for a in env.baseline_assignments]
    result = env.compose(assignments)
    assert result["repairs"]
    assert all(s.pattern_id != pattern.id for v in env.plans.variants for s in v.slides)


def test_runtime_timeout_cancels_provider(prepared, tmp_path):
    settings, _, package = prepared
    configured = replace(settings, mode="api", model_id="test")

    class SlowGateway:
        settings = configured

        async def json_request(self, *args, **kwargs):
            await asyncio.sleep(10)

    with pytest.raises(TimeoutError):
        asyncio.run(
            design(package, environment(prepared).plans, SlowGateway(), tmp_path / "run", 0.02)
        )
    report = json.loads((tmp_path / "run/engine-report.json").read_text())
    assert report["status"] == "timed_out"


def test_model_request_uses_stage_budget_not_35_second_cutoff(prepared):
    import time

    env = environment(prepared)

    class Gateway:
        settings = SimpleNamespace(model_id="test")

        async def json_request(self, prompt, payload, timeout, schema):
            assert 75 < timeout <= 85
            return {
                "name": "compose_slides",
                "assignments": env.baseline_assignments,
                "outcome": None,
            }

    bridge = GatewayBridge(Gateway(), time.monotonic() + 90, env)
    result = asyncio.run(bridge.run([]))
    assert result.choices[0].message.tool_calls[0].function.name == "compose_slides"


def test_single_action_array_from_provider_is_accepted(prepared):
    import time

    env = environment(prepared)

    class Gateway:
        settings = SimpleNamespace(model_id="test")

        async def json_request(self, prompt, payload, timeout, schema):
            return [
                {"name": "compose_slides", "assignments": env.baseline_assignments, "outcome": None}
            ]

    bridge = GatewayBridge(Gateway(), time.monotonic() + 10, env)
    result = asyncio.run(bridge.run([]))
    assert result.choices[0].message.tool_calls[0].function.name == "compose_slides"


def test_multiple_actions_from_provider_are_rejected(prepared):
    import time

    env = environment(prepared)

    class Gateway:
        settings = SimpleNamespace(model_id="test")

        async def json_request(self, prompt, payload, timeout, schema):
            action = {
                "name": "compose_slides",
                "assignments": env.baseline_assignments,
                "outcome": None,
            }
            return [action, action]

    bridge = GatewayBridge(Gateway(), time.monotonic() + 10, env)
    with pytest.raises(ValueError):
        asyncio.run(bridge.run([]))
