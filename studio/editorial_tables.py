"""Recover collapsed headers without changing source rows or inventing labels."""

from .models import StrictModel
from pydantic import Field
from .induction import validated_request


class HeaderRepair(StrictModel):
    table_id: str
    headers: list[str] = Field(min_length=2, max_length=8)


class HeaderRepairs(StrictModel):
    tables: list[HeaderRepair] = Field(min_length=1, max_length=30)


def validate_headers(raw, broken):
    parsed = HeaderRepairs.model_validate(raw)
    by_id = {t.id: t for t in broken}
    if len(parsed.tables) != len(by_id) or {t.table_id for t in parsed.tables} != set(by_id):
        raise ValueError("Cover every broken table exactly once")
    for item in parsed.tables:
        source = by_id[item.table_id]
        original = "".join("".join(source.headers).casefold().split())
        if len(item.headers) != len(source.headers) or any(
            not h.strip() or "".join(h.casefold().split()) not in original for h in item.headers
        ):
            raise ValueError(
                "Headers must split the existing header text; never infer new units or labels"
            )
        if len(set(item.headers)) != len(item.headers):
            raise ValueError("Duplicate headers")
        if "".join("".join(item.headers).casefold().split()) != original:
            raise ValueError("Preserve the full original header text and order, including units")
    return parsed.model_dump()


async def resolve_headers(content, gateway):
    broken = [t for t in content.tables if any(not h.strip() for h in t.headers)]
    if not broken:
        return content, []
    raw = await validated_request(
        gateway,
        "table_headers",
        {"tables": [t.model_dump() for t in broken]},
        HeaderRepairs.model_json_schema(),
        lambda r: validate_headers(r, broken),
        timeout=120,
    )
    result = content.model_copy(deep=True)
    changes = []
    for item in raw["tables"]:
        table = next(t for t in result.tables if t.id == item["table_id"])
        changes.append(
            {"table_id": table.id, "original": table.headers, "resolved": item["headers"]}
        )
        table.headers = item["headers"]
    return result, changes
