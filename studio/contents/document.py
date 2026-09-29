"""Evidence-preserving document induction, before slide planning."""

import re
from typing import Literal
from pydantic import Field
from studio.models import StrictModel


class BlockRole(StrictModel):
    fact_id: str
    kind: Literal["body", "heading", "caveat", "visualization"]


class DocumentRoles(StrictModel):
    blocks: list[BlockRole] = Field(min_length=1, max_length=300)


def visualization(text):
    from studio.contents.parsing import plain_inline

    text = plain_inline(text)
    if re.match(
        r"^(?:данные\s+для.*карточ|(?:четыре|\d+)\s+цифр[ыа]?\s+для\s+финального\s+слайда|показатели\s+разных\s+типов:\s*отдельные\s+карточки)",
        text,
        re.I,
    ):
        return "metrics"
    if not re.match(
        r"^(?:(?:рекомендуемый\s+вид|тип\s+(?:графика|визуализации)|visualization)\s*:|данные\s+для\b)",
        text,
        re.I,
    ):
        return None
    if re.search(r"линейн|линий|линии|\bline\b", text, re.I):
        return "line"
    if re.search(r"кругов|кольцев|pie|donut", text, re.I):
        return "pie"
    if re.search(r"горизонталь|horizontal", text, re.I):
        return "bar"
    if re.search(r"накопительн", text, re.I):
        return "column_stacked"
    if re.search(r"столбчат|столбц|column|bar", text, re.I):
        return "column"
    if re.search(r"таблиц|table", text, re.I):
        return "table"
    if re.search(r"график", text, re.I):
        return "line"
    return None


async def structure_document(package, gateway, progress=None):
    content = package.content
    original = [f.model_dump() for f in content.facts]
    roles = {
        f.id: "visualization" if f.source == "user_text" and visualization(f.text) else "body"
        for f in content.facts
    }
    status = "deterministic"
    from studio.contents.parsing import slide_heading

    explicit_outline = bool(content.facts) and all(
        slide_heading(f.section) is not None for f in content.facts
    )
    if explicit_outline and gateway.settings.mode == "api":
        status = "completed"
    # Explicit markup already supplies headings. Plain text needs induction too.
    if gateway.settings.mode == "api" and not explicit_outline:
        from studio.providers.induction import validated_request, InductionFailure

        status = "completed"
        failed_blocks = []
        for offset in range(0, len(content.facts), 24):
            batch = content.facts[offset : offset + 24]
            if progress:
                progress(
                    f"Проверяем структуру текста: блоки {offset + 1}–{offset + len(batch)} из {len(content.facts)}"
                )
            schema = DocumentRoles.model_json_schema()
            schema["$defs"]["BlockRole"]["properties"]["fact_id"] = {
                "type": "string",
                "enum": [f.id for f in batch],
            }
            schema["properties"]["blocks"].update(minItems=len(batch), maxItems=len(batch))

            def validate(raw):
                parsed = DocumentRoles.model_validate(raw)
                if [b.fact_id for b in parsed.blocks] != [f.id for f in batch]:
                    raise ValueError("Block coverage or order changed")
                return parsed.model_dump()

            try:
                result = await validated_request(
                    gateway,
                    "document",
                    {"blocks": [f.model_dump() for f in batch]},
                    schema,
                    validate,
                    timeout=120,
                    progress=progress,
                )
            except InductionFailure as exc:
                failed_blocks.extend(f.id for f in batch)
                package.analysis["document_error"] = str(exc)
                status = "degraded"
                continue
            for block, fact in zip(DocumentRoles.model_validate(result).blocks, batch):
                # Tables and substantive/numeric assertions may never disappear as metadata.
                if block.kind == "heading" and (
                    fact.source != "user_text"
                    or fact.list_item
                    or len(fact.text) > 160
                    or re.search(r"[.!?]\s*$|\d", fact.text)
                ):
                    failed_blocks.append(fact.id)
                    status = "degraded"
                    continue
                if block.kind == "visualization" and (
                    fact.source != "user_text" or not visualization(fact.text)
                ):
                    failed_blocks.append(fact.id)
                    status = "degraded"
                    continue
                # Explicit presentation hints are deterministic metadata, not
                # evidence that a model can accidentally promote to body copy.
                if roles[fact.id] != "visualization":
                    roles[fact.id] = block.kind
        # A model may call every short block a heading. Never erase all evidence.
        if not any(kind in ("body", "caveat") for kind in roles.values()):
            roles = {f.id: "body" for f in content.facts}
            failed_blocks = [f.id for f in content.facts]
            status = "degraded"
        if status == "degraded":
            package.analysis.setdefault("warnings", []).append(
                f"Для {len(set(failed_blocks))} блоков текста не подтверждён модельный разбор. "
                "Они сохранены как исходный текст с разметкой Markdown, без удаления утверждений. Проверьте структуру результата."
            )
        package.analysis["document_fallback_blocks"] = list(dict.fromkeys(failed_blocks))
    facts = []
    section = ""
    explicit_section = None
    for fact in content.facts:
        if fact.section != explicit_section:
            explicit_section = fact.section
            section = fact.section
        kind = roles[fact.id]
        if kind == "heading":
            section = fact.text
            content.headings.append({"id": fact.id, "text": fact.text, "line": fact.line})
            if not facts:
                content.title = fact.text
        elif fact.source == "user_text" and visualization(fact.text):
            hint = visualization(fact.text)
            content.directives.append(
                {
                    "id": fact.id,
                    "text": fact.text,
                    "section": section,
                    "visualization": hint,
                    "line": fact.line,
                }
            )
        else:
            fact.section = section
            fact.kind = "caveat" if kind == "caveat" else "body"
            facts.append(fact)
    if not facts:
        raise ValueError("После структурного разбора нет содержательных утверждений")
    content.facts = facts
    for table in content.tables:
        evidence = next((f for f in facts if f.source == table.id), None)
        if evidence:
            table.section = evidence.section
        hints = [d for d in content.directives if d["section"] == table.section]
        if hints:
            table.visualization = hints[-1]["visualization"]
    package.analysis["document_structure"] = {
        "version": 2,
        "status": status,
        "method": "explicit_slide_markup" if explicit_outline else "block_role_induction",
        "source_blocks": original,
        "headings": content.headings,
        "directives": content.directives,
        "body_blocks": len(facts),
    }
