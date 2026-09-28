"""Preparation-only grounded content units. Never generate facts or reorder them."""

from copy import deepcopy
from typing import Literal
from pydantic import Field

from .archetype_catalog import Archetype, SlotRole, VERSION, CATALOG, catalog_payload
from .models import StrictModel


class EvidenceSlot(StrictModel):
    role: SlotRole
    fact_id: str
    quote: str = Field(min_length=1, max_length=1200)
    source: Literal["text", "section", "table_header"] = "text"


class SemanticUnit(StrictModel):
    fact_ids: list[str] = Field(min_length=1, max_length=24)
    purpose: Archetype
    confidence: Literal["low", "medium", "high"]
    slots: list[EvidenceSlot] = Field(max_length=80)


class ContentArchetypes(StrictModel):
    units: list[SemanticUnit] = Field(min_length=1, max_length=24)


def validate_units(raw, facts, tables=()):
    parsed = ContentArchetypes.model_validate(raw)
    expected = [fact.id for fact in facts]
    if [fid for unit in parsed.units for fid in unit.fact_ids] != expected:
        raise ValueError("Units must partition every input fact once, in source order")
    by_id = {fact.id: fact for fact in facts}
    table_by_id = {table.id: table for table in tables}
    for unit in parsed.units:
        if unit.purpose in ("cover", "divider"):
            raise ValueError(
                "Structural slides are reserved by the storyboard, not generated from body facts"
            )
        values = {}
        positions = []
        for slot in unit.slots:
            if slot.fact_id not in unit.fact_ids:
                raise ValueError("Slot references a fact outside its unit")
            fact = by_id[slot.fact_id]
            # Headers carry units/criteria often absent from the extracted row
            # text. Only the table linked to this fact may provide that evidence.
            table = table_by_id.get(fact.source)
            in_header = (
                table
                and slot.role in ("criterion", "metric", "date", "topic")
                and any(slot.quote in header for header in table.headers)
            )
            in_section = (
                slot.source == "section"
                and slot.role in ("person", "topic", "entity", "metric", "whole")
                and slot.quote in fact.section
            )
            if not (
                in_section
                or slot.source == "text"
                and slot.quote in fact.text
                or slot.source in ("text", "table_header")
                and in_header
            ):
                raise ValueError("Slot must quote its source fact verbatim")
            values.setdefault(slot.role, set()).add(" ".join(slot.quote.casefold().split()))
            if slot.role == "step":
                positions.append((expected.index(slot.fact_id), fact.text.index(slot.quote)))
        for role, count in CATALOG[unit.purpose][2].items():
            if len(values.get(role, set())) < count:
                raise ValueError("Required semantic slots are missing or duplicated")
        if unit.purpose == "process" and positions != sorted(set(positions)):
            raise ValueError("Process steps must preserve source order")
        if unit.confidence == "low" and unit.purpose != "content":
            # Conservative admission, not false confidence in a special layout.
            unit.purpose = "content"
    return parsed.model_dump()


def _batches(facts):
    batch = []
    characters = 0
    for fact in facts:
        if batch and (len(batch) >= 24 or characters + len(fact.text) > 16000):
            yield batch
            batch = []
            characters = 0
        batch.append(fact)
        characters += len(fact.text)
    if batch:
        yield batch


def reviewed_editorial_report(package):
    """Report editorial slide roles separately from evidence-slot classifier units."""
    groups = deepcopy(package.analysis.get("narrative", {}).get("groups", []))
    return {
        "catalog_version": VERSION,
        "status": "completed" if groups else "not_run",
        "method": "reviewed_editorial_groups",
        "reviewed_groups": groups,
        # Consumers use units as evidence-slot contracts. Editorial groups are
        # not classifier units and must not change rendering or source binding.
        "units": [],
    }


async def analyze_content_archetypes(package, gateway, progress=None):
    from .induction import validated_request, InductionFailure
    from .sections import source_sections
    from .content import slide_heading

    report = {"catalog_version": VERSION, "status": "not_run", "units": [], "failed_batches": 0}
    package.analysis["archetypes"] = report
    if gateway.settings.mode != "api":
        return report
    by_id = {f.id: f for f in package.content.facts}
    explicit = source_sections(package)
    table_ids = {t.id for t in package.content.tables}
    if (
        explicit
        and all(slide_heading(g["section"]) is not None for g in explicit)
        and package.content.directives
        and all(
            i == 0 or any(by_id[f].source in table_ids for f in g["fact_ids"])
            for i, g in enumerate(explicit)
        )
    ):
        # An authored slide sequence with explicit data visualizations is already
        # a structural contract. Reclassifying every sentence does not add a
        # decision and can fragment related evidence into incompatible units.
        report.update(
            status="completed",
            method="explicit_data_storyboard",
            units=[
                {
                    "fact_ids": [f.id for f in batch],
                    "purpose": "content",
                    "confidence": "high",
                    "slots": [],
                }
                for group in explicit
                for batch in _batches([by_id[f] for f in group["fact_ids"]])
            ],
        )
        return report
    groups = package.analysis.get("section_groups", [])
    if not groups and any(slide_heading(f.section) is not None for f in package.content.facts):
        groups = source_sections(package)
    if not groups or [fid for g in groups for fid in g["fact_ids"]] != list(by_id):
        groups = [{"fact_ids": list(by_id)}]

    async def classify(batch):
        if progress:
            progress("Определяем смысловые блоки: сравнения, процессы и другие архетипы")
        schema = ContentArchetypes.model_json_schema()
        ids = [f.id for f in batch]
        schema["$defs"]["SemanticUnit"]["properties"]["fact_ids"]["items"] = {
            "type": "string",
            "enum": ids,
        }
        schema["$defs"]["EvidenceSlot"]["properties"]["fact_id"] = {"type": "string", "enum": ids}
        tables = {table.id: table for table in package.content.tables}
        payload = {
            "catalog": catalog_payload(),
            "facts": [
                {
                    **f.model_dump(),
                    "table": tables[f.source].model_dump() if f.source in tables else None,
                }
                for f in batch
            ],
        }
        try:
            result = await validated_request(
                gateway,
                "content_archetypes",
                payload,
                schema,
                lambda raw: validate_units(raw, batch, package.content.tables),
                timeout=180,
                progress=progress,
            )
            return result["units"], False
        except InductionFailure:
            # Keep source sections so failure is no more destructive than the
            # old storyboard. No guessed special types; no lost source facts.
            units = []
            for fact in batch:
                previous = units[-1] if units else None
                if (
                    previous
                    and previous.get("fallback")
                    and by_id[previous["fact_ids"][-1]].section == fact.section
                ):
                    previous["fact_ids"].append(fact.id)
                else:
                    units.append(
                        {
                            "fact_ids": [fact.id],
                            "purpose": "content",
                            "confidence": "low",
                            "slots": [],
                            "fallback": True,
                        }
                    )
            return units, True

    import asyncio

    batches = [
        batch for group in groups for batch in _batches([by_id[fid] for fid in group["fact_ids"]])
    ]
    # Calls are independent. The shared provider limiter still enforces the
    # configured account concurrency. gather preserves source order.
    classified = await asyncio.gather(*(classify(batch) for batch in batches))
    report["units"] = [unit for units, _ in classified for unit in units]
    report["failed_batches"] = sum(failed for _, failed in classified)
    report["status"] = "degraded" if report["failed_batches"] else "completed"
    if report["failed_batches"]:
        package.analysis.setdefault("warnings", []).append(
            "Для части материала архетипы не подтверждены моделью. Эти блоки сохранены как обычное содержание; результат требует проверки."
        )
    return report


def unit_chunks(package, groups):
    """Validated semantic units become atomic allocation units for specific types."""
    units = package.analysis.get("archetypes", {}).get("units", [])
    if not units:
        return None
    facts = {f.id: f for f in package.content.facts}
    if [fid for unit in units for fid in unit["fact_ids"]] != list(facts):
        raise ValueError("Архетипы не покрывают исходный материал по порядку; повторите анализ")
    group_of = {fid: index for index, group in enumerate(groups) for fid in group["fact_ids"]}
    tables = {t.id for t in package.content.tables}
    result = []
    for unit in units:
        ids = unit["fact_ids"]
        # Chapters can have been coalesced, not reordered, by prepare_storyboard.
        if len({group_of[fid] for fid in ids}) != 1:
            if unit["purpose"] != "content":
                raise ValueError("Смысловой блок пересекает границу раздела; повторите анализ")
            for fid in ids:
                result.append(
                    {
                        "group": group_of[fid],
                        "section": facts[fid].section,
                        "ids": [fid],
                        "purpose": "content",
                    }
                )
            continue
        if sum(facts[fid].source in tables for fid in ids) > 1:
            # Keep one full table per slide; never split its cells or fabricate a
            # comparison layout with only half of a semantic comparison.
            for fid in ids:
                result.append(
                    {
                        "group": group_of[fid],
                        "section": facts[fid].section,
                        "ids": [fid],
                        "purpose": "content",
                    }
                )
        else:
            result.append(
                {
                    "group": group_of[ids[0]],
                    "section": facts[ids[0]].section,
                    "ids": ids[:],
                    "purpose": unit["purpose"],
                }
            )
    return result


def align_inferred_groups(groups, package):
    """Keep real induced chapters; merge weak heading boundaries crossed by a
    grounded specialized unit (e.g. headings naming two compared products).
    """
    if package.analysis.get("section_grouping", {}).get("status") == "completed":
        return groups
    units = package.analysis.get("archetypes", {}).get("units", [])
    group_of = {fid: index for index, group in enumerate(groups) for fid in group["fact_ids"]}
    joined = set()
    for unit in units:
        if unit["purpose"] == "content":
            continue
        indices = [group_of[fid] for fid in unit["fact_ids"] if fid in group_of]
        if indices:
            joined.update(range(min(indices), max(indices)))
    result = []
    for index, group in enumerate(groups):
        if index and index - 1 in joined:
            result[-1]["fact_ids"].extend(group["fact_ids"])
        else:
            result.append({**group, "fact_ids": group["fact_ids"][:]})
    return result
