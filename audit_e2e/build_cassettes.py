"""Explicit, offline authoring of SYNTHETIC HTTP fixtures; never imported by replay.

Run only when intentionally reviewing a changed request/response contract. This
does not record a provider, establish model quality or update independent facts.
"""

import argparse
from collections import OrderedDict
from contextlib import contextmanager
import json
from threading import Lock
from uuid import uuid4

from test_support.replay import completion, request_contract

from .reporting import AUDIT_ROOT, RESULTS, write_json


PROPOSALS = [
    "Предлагается собрать обратную связь участников пилота и обсудить её с командой сервиса.",
    "Для проверки подхода предлагается заранее согласовать критерии оценки результата с руководителем.",
    "Возможный подход — сравнить наблюдения участников и зафиксировать ограничения перед следующим этапом.",
    "Предлагается распределить ответственность за подготовку итогового обсуждения внутри команды.",
]


def authored_response(stage, data, *, selected_repair=False, capture_failure=False):
    """Deliberately mechanical canned answers for test plumbing, not an oracle."""
    if stage == "template_analyst":
        return {
            "patterns": [
                {
                    "pattern_id": p["id"],
                    "roles": ["context", "insight"],
                    "density": "medium",
                    "purpose": p.get("technical_role")
                    if p.get("technical_role") in ("cover", "divider")
                    else "content",
                    "reusable": True,
                }
                for p in data["patterns"]
            ]
        }
    if stage == "author":
        count = data["requested_count"]
        if count > len(PROPOSALS):
            raise ValueError("Author fixture has no approved proposal for this count")
        return {"proposals": PROPOSALS[:count]}
    if stage == "editorial":
        source = data["source"]
        facts = source["facts"]
        groups = OrderedDict()
        for fact in facts:
            # Each proposed idea is a separate slide; original sections keep
            # their table, caveat and literal source claims together.
            key = (
                fact["id"]
                if fact.get("source") == "model_proposal"
                else fact.get("section") or fact["id"]
            )
            groups.setdefault(key, []).append(fact)
        slides = []
        for index, (section, rows) in enumerate(groups.items()):
            tables = [
                t for t in source.get("tables", []) if any(f.get("source") == t["id"] for f in rows)
            ]
            claims = [f for f in rows if not any(f.get("source") == t["id"] for t in tables)]
            if not claims:
                claims = rows[:1]
                if tables and len(claims[0]["text"]) > 300:
                    # The native table carries every cell. Use its source heading
                    # as a caption instead of duplicating all rows in a bullet.
                    caption = tables[0].get("section") or section
                    if not caption or len(caption) > 300:
                        raise ValueError("Author a grounded caption for the source table")
                    claims = [{**claims[0], "text": caption}]
            title = source["title"] if index == 0 else section
            if len(title) > 140:
                raise ValueError(
                    "Source title exceeds the fixture contract; author a grounded title explicitly"
                )
            if section.startswith("f") and section[1:].isdigit() and index != 0:
                title = "Оценка результата пилота"
            if section.startswith("draft"):
                title = {
                    "draft1": "Предлагаемый сбор обратной связи",
                    "draft2": "Предлагаемые критерии оценки",
                    "draft3": "Предлагаемое сравнение результатов",
                    "draft4": "Предлагаемое распределение ролей",
                }[section]
            slide = {
                "title": title[:140],
                "purpose": "cover" if index == 0 else "content",
                "bullets": [
                    {"text": f["text"], "evidence": [{"fact_id": f["id"]}]} for f in claims
                ],
            }
            if index == 0 and len(slide["bullets"]) > 1:
                subtitle = " ".join(f["text"] for f in claims)
                if len(subtitle) > 140:
                    raise ValueError("Review synthetic cover: grounded subtitle exceeds contract")
                slide["bullets"] = [
                    {
                        "text": subtitle,
                        "evidence": [{"fact_id": f["id"]} for f in claims],
                    }
                ]
            if tables:
                slide.update(
                    source_table_id=tables[0]["id"], source_columns=[], chart_type="table", rows=[]
                )
            slides.append(slide)
        low, high = data["slide_range"]
        if not low <= len(slides) <= high:
            raise ValueError(f"Review fixture grouping: {len(slides)} slides outside {low}..{high}")
        return {"slides": slides, "omitted": []}
    if stage == "editorial_review":
        return {
            "claims": [
                {
                    "claim_id": c["claim_id"],
                    "supported": True,
                    "meaning_preserved": True,
                    "issue": "",
                }
                for c in data["claims"]
            ],
            "missing_essential_fact_ids": [],
            "repair_slide_indices": [],
            "narrative_coherent": True,
            "explanation": "Synthetic fixture only; independent evidence checks evaluate exports.",
        }
    if stage == "editorial_repair" and capture_failure:
        # A deliberately ineffective model repair preserves the observed layout
        # failure. It exercises the product's bounded failure path, not recovery.
        return {
            "replacements": [
                {"slide": index, "content": data["previous_plan"]["slides"][index - 1]}
                for index in data["allowed_slide_indices"]
            ]
        }
    if stage == "deeppresenter":
        for message in data["history"]:
            if message["role"] == "user":
                payload = json.loads(message["text"])
                return {
                    "name": "compose_slides",
                    "assignments": payload["baseline_assignment"],
                    "outcome": None,
                }
        raise ValueError("No supplied composition fixture")
    if stage == "critic":
        return {"findings": []}
    if stage == "visual_critic":
        numbers = [s["slide"] for s in data["slides"]]
        findings = []
        if selected_repair and 2 in numbers:
            findings.append(
                {
                    "slide": 2,
                    "code": "hierarchy",
                    "severity": "warning",
                    "message": "Synthetic persistent hierarchy warning for selected repair workflow.",
                }
            )
        return {"checked_slides": numbers, "findings": findings}
    if stage == "repair" and selected_repair:
        return {"edits": []}
    if stage == "text_zone":
        return {"cells": []}
    if stage == "background_raster":
        return {"regions": []}
    if stage == "template_resources":
        return {
            "choices": [
                {
                    "id": c["id"],
                    "kind": "skip",
                    "description": "Synthetic fixture",
                    "confidence": 1,
                    "screen_box": None,
                    "tags": [],
                }
                for c in data["candidates"]
            ]
        }
    raise ValueError("Unreviewed synthetic stage: " + stage)


class AuthoringRecorder:
    def __init__(self, *, revision=1, selected_repair=False, capture_failure=False):
        self.selected_repair = selected_repair
        self.capture_failure = capture_failure
        self.document = {
            "schema_version": 1,
            "fixture_revision": revision,
            "provenance": "Explicitly authored synthetic model replies at the real HTTP boundary. Not a provider recording or a quality oracle. Independent reference ledger is authored separately.",
            "image_matching": "dimensions",
            "exchanges": [],
            "ineffective_editorial_repair_allowed": capture_failure,
        }
        self.calls, self.mismatches = [], []
        self.lock = Lock()

    def respond(self, body):
        stage = body["response_format"]["json_schema"]["name"]
        content = body["messages"][-1]["content"]
        text = content[0]["text"] if isinstance(content, list) else content
        data = json.loads(text)["untrusted_input"]
        with self.lock:
            try:
                value = completion(
                    authored_response(
                        stage,
                        data,
                        selected_repair=self.selected_repair,
                        capture_failure=self.capture_failure,
                    )
                )
            except (ValueError, KeyError, TypeError) as exc:
                self.mismatches.append({"stage": stage, "error": str(exc), "data": data})
                return 401, {"error": {"message": "Unreviewed synthetic fixture stage"}}, {}
            self.document["exchanges"].append(
                {
                    "request": request_contract(body, image_matching="dimensions"),
                    "status": 200,
                    "response": value,
                }
            )
            self.calls.append({"stage": stage, "status": 200})
            return 200, value, {}

    def assert_consumed(self):
        assert self.calls and not self.mismatches, self.mismatches


@contextmanager
def recorder_provider(recorder):
    import test_support.app_server as server

    previous = server.Replay
    server.Replay = lambda path: recorder
    try:
        yield
    finally:
        server.Replay = previous


def main():
    from audit_e2e.corpus import load_cases, materialize_case
    from audit_e2e.runtime import execute_case

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--selected-repair", action="store_true")
    parser.add_argument("--suite", choices=("core", "extended", "development"), default="extended")
    parser.add_argument(
        "--capture-failure",
        action="store_true",
        help="Explicitly preserve a finite failed execution for regression; never makes it a passing oracle",
    )
    args = parser.parse_args()
    case = next(c for c in load_cases(args.suite) if c["id"] == args.case)
    fixture_id = "browser-selected-repair" if args.selected_repair else case["id"]
    destination = AUDIT_ROOT / "fixtures/cassettes" / (fixture_id + ".json")
    if destination.exists() and not args.replace:
        parser.error(
            "Fixture already exists; use --replace only for an intentional reviewed change"
        )
    previous = (
        json.loads(destination.read_text()).get("fixture_revision", 0)
        if destination.exists()
        else 0
    )
    if args.revision <= previous:
        parser.error("Fixture changes require a strictly higher --revision")
    directory = RESULTS / "cassette-authoring" / (case["id"] + "-" + uuid4().hex[:8])
    materialized = materialize_case(case, directory / "input", synthetic=True)
    materialized["cassette"] = destination
    if args.selected_repair:
        materialized.update(interface="browser", selected_repair=True)
    recorder = AuthoringRecorder(
        revision=args.revision,
        selected_repair=args.selected_repair,
        capture_failure=args.capture_failure,
    )
    with recorder_provider(recorder):
        result = execute_case(materialized, directory / "result", mode="replay", timeout=600)
    write_json(directory / "candidate.json", recorder.document)
    write_json(directory / "authoring-errors.json", recorder.mismatches)
    if (
        recorder.mismatches
        or not recorder.calls
        or (result["status"] != "passed" and not args.capture_failure)
    ):
        print(
            json.dumps(
                {"status": "failed", "artifacts": str(directory), "execution": result}, default=str
            )
        )
        return 1
    recorder.document["authoring_execution_status"] = result["status"]
    recorder.document["quality_oracle"] = False
    write_json(destination, recorder.document)
    print(
        json.dumps(
            {
                "status": "authored_requires_diff_review",
                "fixture": str(destination),
                "exchanges": len(recorder.calls),
                "artifacts": str(directory),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
