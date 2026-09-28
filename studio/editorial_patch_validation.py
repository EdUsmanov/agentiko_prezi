"""Keep a local text repair on its original subject and validate before caching."""

from copy import deepcopy
from .repair_errors import PlanValidationError, validation_issues


def claim_signature(claim):
    result = deepcopy(claim)
    # Quotes are server-resolved; immutable evidence is identified by fact IDs.
    result["evidence"] = [{"fact_id": row["fact_id"]} for row in result["evidence"]]
    return result


def shortening_contracts(previous, allowed, feedback, budget):
    """Lock valid structure only when the requested change is text shortening."""
    issues = feedback.get("repair_issues", [])
    contracts = []
    for index in allowed:
        local = [issue for issue in issues if issue.get("slide") == index]
        # Wording is for humans. Only explicit actions may lock a text repair.
        if not local or any(issue.get("action") != "shorten_text" for issue in local):
            continue
        slide = previous["slides"][index - 1]
        labels = sum(len(b["group"]) for b in slide["bullets"])
        fields = [
            field
            for row in feedback.get("fields", [])
            if row.get("slide") == index
            for field in row.get("fields", [])
        ]
        geometry_only = all(issue["code"] in ("text_overflow", "readability") for issue in local)
        affected = {
            fid
            for field in fields
            if field.get("role") != "title"
            for fid in field.get("fact_ids", [])
        }
        fixed = (
            [
                {"index": j, "content": claim_signature(bullet)}
                for j, bullet in enumerate(slide["bullets"], 1)
                if f"summary-{index}-{j}" not in affected
            ]
            if geometry_only and fields
            else []
        )
        field_targets = [
            {
                "role": field["role"],
                "fact_ids": field.get("fact_ids", []),
                "target_max_characters": field["target_max_characters"],
            }
            for field in fields
            if isinstance(field.get("target_max_characters"), int)
            and field["target_max_characters"] > 0
        ]
        per_bullet = max(1, (budget - labels) // max(1, len(slide["bullets"])))
        if field_targets:
            # A local geometry limit is not the whole-slide writing budget.
            # Keep separate field limits so fitting neighbours stay untouched.
            per_bullet = (
                min(per_bullet, field_targets[0]["target_max_characters"])
                if len(slide["bullets"]) == 1
                and len(field_targets) == 1
                and field_targets[0]["role"] != "title"
                else None
            )

        contracts.append(
            {
                "slide": index,
                "fixed_bullets": fixed,
                "bullet_count": len(slide["bullets"]) if fixed else None,
                "fixed_title": slide["title"]
                if geometry_only
                and fields
                and not any(field.get("role") == "title" for field in fields)
                else None,
                "purpose": slide["purpose"],
                "source_table_id": slide["source_table_id"],
                "source_columns": slide["source_columns"],
                "chart_type": slide["chart_type"],
                "relationship": slide["relationship"],
                "rows": slide["rows"],
                "source_fact_ids": sorted(
                    {e["fact_id"] for b in slide["bullets"] for e in b["evidence"]}
                ),
                "groups": [b["group"] for b in slide["bullets"]]
                if slide["purpose"] in ("timeline", "process", "hierarchy", "structure")
                else None,
                "target_characters_per_bullet": per_bullet,
                "field_character_targets": field_targets,
            }
        )
    return contracts


def validate_contracts(changed, contracts):
    for contract in contracts:
        slide = changed["slides"][contract["slide"] - 1]
        prefix = f"s{contract['slide']}: shortening-only repair"
        if contract.get("fixed_title") is not None and slide["title"] != contract["fixed_title"]:
            raise ValueError(f"{prefix} must preserve the unaffected title")
        if (
            contract.get("bullet_count") is not None
            and len(slide["bullets"]) != contract["bullet_count"]
        ):
            raise ValueError(f"{prefix} must preserve bullet positions")
        for fixed in contract.get("fixed_bullets", []):
            if claim_signature(slide["bullets"][fixed["index"] - 1]) != fixed["content"]:
                raise ValueError(f"{prefix} must preserve unaffected bullet {fixed['index']}")

        for key in (
            "purpose",
            "source_table_id",
            "source_columns",
            "chart_type",
            "relationship",
            "rows",
        ):
            if slide[key] != contract[key]:
                raise ValueError(
                    f"{prefix} must preserve {key}; shorten the existing text, do not replace its subject with another slide"
                )
        refs = {e["fact_id"] for b in slide["bullets"] for e in b["evidence"]}
        if not set(contract["source_fact_ids"]) <= refs:
            raise ValueError(
                f"{prefix} must retain evidence for source facts {contract['source_fact_ids']}"
            )
        if (
            contract["groups"] is not None
            and [b["group"] for b in slide["bullets"]] != contract["groups"]
        ):
            raise ValueError(f"{prefix} must preserve the ordered event/group labels")


def constrain_patch_schema(schema, contracts, allowed, previous=None):
    """Expose per-slide constraints to JSON-mode models, including scope IDs."""
    schema = deepcopy(schema)
    by_index = {row["slide"]: row for row in contracts}
    choices = []
    for index in allowed:
        replacement = deepcopy(schema["$defs"]["Replacement"])
        replacement["properties"]["slide"] = {"type": "integer", "const": index}
        if index in by_index:
            content = deepcopy(schema["$defs"]["EditorialSlide"])
            for key in (
                "purpose",
                "source_table_id",
                "source_columns",
                "chart_type",
                "relationship",
                "rows",
            ):
                content["properties"][key] = {
                    **content["properties"][key],
                    "const": by_index[index][key],
                }
            replacement["properties"]["content"] = content
        if previous and previous["slides"][index - 1]["purpose"] == "cover":
            content = deepcopy(replacement["properties"]["content"])
            if "$ref" in content:
                content = deepcopy(schema["$defs"]["EditorialSlide"])
            content["properties"]["purpose"] = {"type": "string", "const": "cover"}
            claim = deepcopy(schema["$defs"]["Claim"])
            claim["properties"]["text"]["maxLength"] = 140
            content["properties"]["bullets"] = {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "items": claim,
            }
            content["properties"]["source_table_id"] = {"type": "null"}
            content["properties"]["rows"] = {**content["properties"]["rows"], "maxItems": 0}
            replacement["properties"]["content"] = content
        choices.append(replacement)
    schema["properties"]["replacements"]["items"] = {"oneOf": choices}
    return schema


def validate_repaired_plan(changed, content, bounds, budget, require_cover, allowed):
    """Reject deterministic errors in changed slides without re-editing neighbours."""
    from .editorial_domain import validate_plan

    try:
        validate_plan(changed, content, bounds, budget, require_cover=require_cover)
    except ValueError as error:
        issues = validation_issues(error)
        if not issues:
            raise
        relevant = [issue for issue in issues if issue.slide is None or issue.slide in allowed]
        if relevant:
            raise PlanValidationError(relevant) from error


def numeric_evidence_hints(previous, allowed, content):
    """Locate numeric evidence for repairs without accepting or rewriting claims.

    Matching digits are candidates only: the model must verify attribution and
    units, and the independent semantic review still evaluates the final claim.
    """
    from .editorial_domain import claim_numbers, nums

    facts = {fact.id: fact.text for fact in content.facts}
    fact_numbers = {fid: set(nums(text)) for fid, text in facts.items()}
    hints = []
    for index in allowed:
        slide = previous["slides"][index - 1]
        for claim_index, claim in enumerate(slide["bullets"], 1):
            cited = [e["fact_id"] for e in claim["evidence"]]
            supported = set().union(*(fact_numbers.get(fid, set()) for fid in cited))
            missing = (
                claim_numbers(
                    claim["text"], claim["group"], slide.get("purpose", "content"), claim_index
                )
                - supported
            )
            if not missing:
                continue
            candidates = [
                {
                    "fact_id": fid,
                    "text": text,
                    "matching_numbers": sorted(missing & fact_numbers[fid]),
                }
                for fid, text in facts.items()
                if missing & fact_numbers[fid]
            ]
            hints.append(
                {
                    "claim_id": f"s{index}b{claim_index}",
                    "text": claim["text"],
                    "unsupported_numbers": sorted(missing),
                    "current_fact_ids": cited,
                    "candidate_count": len(candidates),
                    "candidate_evidence": [
                        {**row, "text": row["text"][:800]} for row in candidates[:12]
                    ],
                    "action": "Add a fact_id only if it supports the SAME subject, quantity and unit. "
                    "Otherwise remove or reword the unsupported numeric claim using the original source wording. "
                    "Do not return unchanged text with unchanged evidence; matching digits alone are not proof.",
                }
            )
    return hints
