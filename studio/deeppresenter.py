"""Bounded integration of the pinned upstream Design agent, without code tools."""

import asyncio
import importlib.util
import json
import time
from collections import Counter
from types import SimpleNamespace
from typing import Literal

from pydantic import ConfigDict, Field

from .audit import audit_scenes, repair_scenes
from .composer import CompositionSession
from .config import ROOT
from .models import StrictModel
from .planner import validate_plans

COMMIT = "2e68c095a86bdbb91635dc4d91dad4662aba163c"
MAX_TURNS = 6


class Assignment(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    variant: Literal["executive", "analytical", "story"]
    slide: int = Field(ge=1, le=30)
    pattern_id: str = Field(min_length=1, max_length=120)


class Action(StrictModel):
    name: Literal["compose_slides", "inspect_variants", "finalize"]
    assignments: list[Assignment] = Field(max_length=90)
    outcome: Literal["composition-plan"] | None


def readiness():
    missing = [
        name
        for name in ("openai", "yaml", "jinja2", "jsonlines")
        if importlib.util.find_spec(name) is None
    ]
    return {
        "ready": not missing,
        "missing": missing,
        "commit": COMMIT,
        "runtime": "DeepPresenter Design (restricted)",
        "visual_reflection": False,
    }


def errors(findings):
    # Multiset detects extra errors of the same kind on an already-bad slide.
    return Counter((f.code, f.slide) for f in findings if f.severity == "error")


class CompositionEnvironment:
    """No arbitrary paths/text/code accepted. Every write is a validated plan copy."""

    def __init__(self, package, plans):
        self.package = package
        self.composition_cache = CompositionSession(package)
        self.plans = plans.model_copy(deep=True)
        self.revision = 0
        self.inspected_revision = -1
        self.events = []
        self.repairs = []
        self.baseline = {}
        self.baseline_scenes = {}
        self.title_floors = {}
        self.baseline_assignments = []
        self.allowed = {
            p.id
            for p in package.template.patterns
            if p.title_zone and (p.body_zones or p.role in ("divider", "cover"))
        } | {"token:auto"}
        self.expected = {(v.key, i) for v in plans.variants for i in range(1, len(v.slides) + 1)}
        self._server_tools = {
            "studio_composition": ["compose_slides", "inspect_variants", "finalize"]
        }
        self._tools_dict = {
            name: {
                "type": "function",
                "function": {"name": name, "description": name, "parameters": {"type": "object"}},
            }
            for name in self._server_tools["studio_composition"]
        }
        for variant in self.plans.variants:
            scenes = self.composition_cache.variant(variant)
            repair_scenes(scenes, package)
            self.baseline[variant.key] = errors(audit_scenes(scenes, package))
            self.baseline_scenes[variant.key] = [s.model_copy(deep=True) for s in scenes]
            for i, scene in enumerate(scenes, 1):
                self.title_floors[variant.key, i] = min(
                    16, min((e.size for e in scene.elements if e.role == "title"), default=16)
                )
                pid = scene.pattern_id if scene.strategy == "native_template" else "token:auto"
                self.baseline_assignments.append(
                    {"variant": variant.key, "slide": i, "pattern_id": pid}
                )

    def compose(self, assignments):
        keys = [(a.variant, a.slide) for a in assignments]
        if len(keys) != len(set(keys)) or set(keys) != self.expected:
            raise ValueError("Complete unique assignments for all slides are required")
        if any(a.pattern_id not in self.allowed for a in assignments):
            raise ValueError("Unknown or unusable pattern ID")
        selected = {(a.variant, a.slide): a.pattern_id for a in assignments}
        baseline = {(a["variant"], a["slide"]): a["pattern_id"] for a in self.baseline_assignments}
        candidate = self.plans.model_copy(deep=True)
        repairs = []
        for variant in candidate.variants:
            readability_bad = set()
            fidelity_bad = set()
            for i, slide in enumerate(variant.slides, 1):
                slide.pattern_id = selected[variant.key, i]
                original = next(
                    (p for p in self.package.template.patterns if p.id == baseline[variant.key, i]),
                    None,
                )
                requested = next(
                    (p for p in self.package.template.patterns if p.id == slide.pattern_id), None
                )
                from .contracts import compatible

                if (
                    requested is not None
                    and not compatible(requested, slide, i - 1)
                    or requested is None
                    and slide.purpose in ("cover", "divider")
                ):
                    repairs.append(
                        {
                            "variant": variant.key,
                            "slide": i,
                            "requested_pattern": slide.pattern_id,
                            "applied_pattern": baseline[variant.key, i],
                            "reason": "semantic_contract_guard",
                        }
                    )
                    slide.pattern_id = baseline[variant.key, i]
                    requested = next(
                        (p for p in self.package.template.patterns if p.id == slide.pattern_id),
                        None,
                    )
                if (
                    original
                    and original.source_slide
                    and (requested is None or not requested.source_slide)
                ):
                    fidelity_bad.add(i)
                observed = {
                    p.master_index for p in self.package.template.patterns if p.source_slide
                }
                if (
                    original
                    and original.master_index in observed
                    and (requested is None or requested.master_index not in observed)
                ):
                    fidelity_bad.add(i)
            try:
                scenes = self.composition_cache.variant(variant)
                repair_scenes(scenes, self.package)
                worse = errors(audit_scenes(scenes, self.package)) - self.baseline[variant.key]
                bad = {slide for _, slide in worse}
                from .quality import candidate_regressions

                bad.update(
                    r["slide"]
                    for r in candidate_regressions(
                        self.baseline_scenes[variant.key], scenes, self.package
                    )
                )
                # A title can fit geometrically only because it was shrunk to
                # caption size. Do not accept that regression from model choice.
                readability_bad = {
                    i
                    for i, scene in enumerate(scenes, 1)
                    if any(
                        e.role == "title" and e.size < self.title_floors[variant.key, i] - 0.1
                        for e in scene.elements
                    )
                }
                bad.update(readability_bad)
                bad.update(fidelity_bad)
            except ValueError:
                bad = {0}
            if bad:
                # Restore only unsafe choices, not the whole content plan or engine.
                # The baseline is generated by our template-aware fit checks.
                for i, slide in enumerate(variant.slides, 1):
                    if 0 in bad or i in bad:
                        previous = slide.pattern_id
                        slide.pattern_id = baseline[variant.key, i]
                        repairs.append(
                            {
                                "variant": variant.key,
                                "slide": i,
                                "requested_pattern": previous,
                                "applied_pattern": slide.pattern_id,
                                "reason": "template_fidelity_guard"
                                if i in fidelity_bad
                                else "readability_guard"
                                if i in readability_bad
                                else "geometry_guard",
                            }
                        )
                scenes = self.composition_cache.variant(variant)
                repair_scenes(scenes, self.package)
                if errors(audit_scenes(scenes, self.package)) - self.baseline[variant.key]:
                    raise ValueError("No safe composition after bounded repair")
        validate_plans(candidate, self.package)
        self.plans = candidate
        self.revision += 1
        self.inspected_revision = -1
        self.repairs.extend(repairs)
        return {"accepted": True, "revision": self.revision, "repairs": repairs}

    def inspect(self):
        if not self.revision:
            raise ValueError("Compose all variants before inspection")
        result = {}
        for variant in self.plans.variants:
            scenes = self.composition_cache.variant(variant)
            repair_scenes(scenes, self.package)
            result[variant.key] = [f.model_dump() for f in audit_scenes(scenes, self.package)][:60]
        self.inspected_revision = self.revision
        return {
            "revision": self.revision,
            "findings": result,
            "check": "geometry_and_template_tokens_not_visual_review",
        }

    async def tool_execute(self, call):
        from ._vendor.deeppresenter.utils.typings import ChatMessage, Role

        name = call.function.name
        try:
            args = json.loads(call.function.arguments)
            if not isinstance(args, dict):
                raise ValueError("Arguments must be an object")
            if name == "compose_slides" and set(args) == {"assignments"}:
                result = self.compose([Assignment.model_validate(a) for a in args["assignments"]])
            elif name == "inspect_variants" and not args:
                result = self.inspect()
            elif name == "finalize" and args == {
                "outcome": "composition-plan",
                "agent_name": "Design",
            }:
                if self.revision < 1 or self.inspected_revision != self.revision:
                    raise ValueError("Inspection of the current revision is required")
                result = "composition-plan"
            else:
                raise ValueError("Tool or arguments are not allowed")
            self.events.append({"tool": name, "status": "completed", "revision": self.revision})
            return ChatMessage(
                role=Role.TOOL,
                content=result if isinstance(result, str) else json.dumps(result),
                tool_call_id=call.id,
            )
        except (ValueError, TypeError, KeyError):
            # Do not echo arbitrary rejected values, paths or model code into history/logs.
            self.events.append(
                {"tool": name if name in self._tools_dict else "unknown", "status": "rejected"}
            )
            return ChatMessage(
                role=Role.TOOL,
                content="Rejected: use complete allowed IDs, safe geometry and compose -> inspect -> finalize order. Baseline assignment is available.",
                tool_call_id=call.id,
                is_error=True,
            )


class GatewayBridge:
    def __init__(self, gateway, deadline, environment):
        self.gateway = gateway
        self.deadline = deadline
        self.model_name = gateway.settings.model_id
        self.turns = 0
        self.model_requests = 0
        self.environment = environment

    async def run(self, messages, tools=None, **kwargs):
        from openai.types.chat import ChatCompletionMessage
        from openai.types.chat.chat_completion_message_function_tool_call import (
            ChatCompletionMessageFunctionToolCall,
        )

        self.turns += 1
        # A reasoning response for 30–90 assignments can exceed two minutes.
        # This bounds a stalled HTTP request, not the whole generation phase.
        remaining = self.deadline - time.monotonic() if self.deadline is not None else 600
        if remaining <= 0 or self.turns > MAX_TURNS:
            raise TimeoutError("Бюджет Design исчерпан")
        history = [{"role": str(m.role), "text": m.text, "is_error": m.is_error} for m in messages]
        env = self.environment
        next_action = (
            "compose_slides"
            if env.revision == 0
            else "inspect_variants"
            if env.inspected_revision != env.revision
            else "finalize"
        )
        schema = Action.model_json_schema()
        schema["properties"]["name"] = {"type": "string", "enum": [next_action]}
        if next_action == "compose_slides":
            # Use the stage budget, not a hidden 35-second per-request cutoff.
            # Inspection/finalization are deterministic tool transitions and need no LLM.
            self.model_requests += 1
            raw = await self.gateway.json_request(
                "deeppresenter",
                {"history": history, "required_next_action": next_action},
                timeout=max(0.001, remaining - min(5, remaining * 0.1)),
                schema=schema,
            )
        else:
            raw = {
                "name": next_action,
                "assignments": [],
                "outcome": "composition-plan" if next_action == "finalize" else None,
            }
        action = Action.model_validate(raw)
        if action.name != next_action:
            raise ValueError("Design violated the bounded workflow")
        if action.name == "compose_slides":
            if not action.assignments or action.outcome is not None:
                raise ValueError("Invalid composition action")
            arguments = {"assignments": [a.model_dump() for a in action.assignments]}
        else:
            if action.assignments or (
                action.name == "inspect_variants" and action.outcome is not None
            ):
                raise ValueError("Invalid inspection/finalize action")
            if action.name == "finalize" and action.outcome != "composition-plan":
                raise ValueError("Invalid outcome")
            arguments = {"outcome": action.outcome} if action.name == "finalize" else {}
        call = ChatCompletionMessageFunctionToolCall(
            id=f"studio_{self.turns}",
            type="function",
            function={"name": action.name, "arguments": json.dumps(arguments)},
        )
        message = ChatCompletionMessage(role="assistant", content=None, tool_calls=[call])
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=message)])


async def design(package, plans, gateway, workspace, timeout):
    """Run the actual pinned Design loop. Failure is explicit, never native success."""
    if not readiness()["ready"]:
        raise ValueError("Установите зависимости: pip install -e '.[deeppresenter]'")
    if gateway.settings.mode != "api":
        raise ValueError(
            "DeepPresenter требует настроенную LLM; для автономной работы выберите native"
        )
    from ._vendor.deeppresenter.agents.design import Design

    started = time.monotonic()
    env = CompositionEnvironment(package, plans)
    bridge = GatewayBridge(gateway, started + timeout if timeout is not None else None, env)

    class Config:
        context_window = 200000
        max_context_folds = 0
        context_folding = False
        offline_mode = False  # Do not misrepresent the provider call as physically offline.

        def __getitem__(self, name):
            if name != "design_agent":
                raise KeyError(name)
            return bridge

    # No paths, font bytes, environment or original file bodies enter the agent.
    catalog = [
        {
            "id": p.id,
            "body_zones": [z.model_dump() for z in p.body_zones],
            "role": p.role,
            "heading_zones": len([z for z in p.heading_zones if z]),
            "graphic_count": p.graphic_count,
            "source_slide": p.source_slide,
            "graphic_kind": p.graphic_kind,
            "graphic_edges": p.graphic_edges,
        }
        for p in package.template.patterns
        if p.id in env.allowed
    ]
    catalog.append(
        {
            "id": "token:auto",
            "description": "Deterministic composition from the uploaded template tokens. Use only when native patterns do not fit; template fidelity requires review.",
        }
    )
    payload = {
        "plans": plans.model_dump(),
        "facts": [{"id": f.id, "text": f.text} for f in package.content.facts],
        "catalog": catalog,
        "semantics": package.analysis.get("template_semantics", {}),
        "baseline_assignment": env.baseline_assignments,
        "size": {"width": package.template.width, "height": package.template.height},
    }
    agent = Design(
        Config(),
        env,
        workspace,
        "en",
        config_file=ROOT / "config/deeppresenter.yaml",
        keep_reasoning=False,
        max_turns=MAX_TURNS,
    )
    status = "failed"
    try:
        async with asyncio.timeout(timeout):
            request = SimpleNamespace(designagent_prompt=json.dumps(payload, ensure_ascii=False))
            async for outcome in agent.loop(request, ""):
                pass
        status = (
            "completed"
            if outcome == "composition-plan" and env.inspected_revision == env.revision
            else "failed"
        )
    except TimeoutError:
        status = "timed_out"
        raise
    finally:
        # Metadata survives failures; never serialize payloads, credentials or reasoning.
        report = {
            "engine": "deeppresenter",
            "status": status,
            "commit": COMMIT,
            "mode": "restricted_template_composition",
            "turns": bridge.turns,
            "model_requests": bridge.model_requests,
            "budget_seconds": timeout,
            "events": env.events,
            "repairs": env.repairs,
            "seconds": round(time.monotonic() - started, 3),
            "visual_reflection": False,
        }
        (workspace / "engine-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
    if outcome != "composition-plan" or env.inspected_revision != env.revision:
        raise ValueError("Design не завершил проверенную композицию")
    return env.plans, report
