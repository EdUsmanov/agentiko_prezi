"""Shared semantic intent types and a versioned, domain-independent catalog."""

import json
from typing import Literal, get_args
from .config import ROOT

Archetype = Literal[
    "cover",
    "speaker",
    "agenda",
    "divider",
    "context",
    "problem",
    "solution",
    "claim_evidence",
    "comparison",
    "process",
    "timeline",
    "metrics",
    "trend",
    "composition",
    "structure",
    "recommendations",
    "summary",
    "closing",
    "content",
]
SlotRole = Literal[
    "topic",
    "person",
    "problem",
    "solution",
    "claim",
    "evidence",
    "entity",
    "criterion",
    "step",
    "event",
    "date",
    "metric",
    "value",
    "whole",
    "part",
    "component",
    "relationship",
    "action",
    "takeaway",
    "message",
]

_DATA = json.loads((ROOT / "config/archetypes.json").read_text())
VERSION = _DATA["version"]
CATALOG = {
    item["id"]: (item["name"], item["description"], item["required_slots"])
    for item in _DATA["archetypes"]
}
if len(CATALOG) != len(_DATA["archetypes"]) or set(CATALOG) != set(get_args(Archetype)):
    raise ValueError("Archetype catalog and schema must contain the same unique IDs")
if any(
    role not in get_args(SlotRole) or type(count) is not int or count < 1
    for _, _, slots in CATALOG.values()
    for role, count in slots.items()
):
    raise ValueError("Invalid archetype slot contract")


def catalog_payload():
    # Return fresh objects: validators and provider schema builders cannot mutate
    # the catalog used by subsequent requests in this process.
    return {
        "version": VERSION,
        "archetypes": [
            {"id": key, "name": value[0], "description": value[1], "required_slots": dict(value[2])}
            for key, value in CATALOG.items()
        ],
        "appendix": _DATA["appendix"],
    }
