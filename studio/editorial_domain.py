"""Evidence-linked selection, synthesis and count-constrained editorial planning."""

from collections import Counter
from typing import Literal
import re
from pydantic import Field
from .models import StrictModel, Fact, ContentModel, TableData
from .repair_errors import PlanValidationError, RepairIssue, plan_error
from .archetype_catalog import Archetype
from .narrative_data import DataRow, choose_visualization


class Citation(StrictModel):
    fact_id: str
    quote: str | None = Field(default=None, min_length=1, max_length=3000)


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=300)
    group: str = Field(default="", max_length=80)
    parent_group: str | None = Field(default=None, max_length=80)
    evidence: list[Citation] = Field(min_length=1, max_length=12)


class EditorialSlide(StrictModel):
    title: str = Field(min_length=1, max_length=140)
    purpose: Archetype = "content"
    bullets: list[Claim] = Field(min_length=1, max_length=6)
    source_table_id: str | None = None
    source_columns: list[int] = Field(default_factory=list, max_length=8)
    chart_type: Literal["auto", "table", "line", "column", "column_stacked", "bar", "pie"] = "auto"
    relationship: Literal["none", "comparison", "time", "share", "table"] = "none"
    rows: list[DataRow] = Field(default_factory=list, max_length=12)


class Omission(StrictModel):
    fact_id: str
    reason: Literal["detail", "duplicate", "off_topic"]
    explanation: str = Field(min_length=1, max_length=240)


class EditorialPlan(StrictModel):
    slides: list[EditorialSlide] = Field(min_length=1, max_length=30)
    omitted: list[Omission] = Field(default_factory=list, max_length=300)


class Verdict(StrictModel):
    claim_id: str
    supported: bool
    meaning_preserved: bool
    # Review diagnostics are not slide copy; preserve actionable detail.
    issue: str = Field(default="", max_length=2000)


class EditorialReview(StrictModel):
    claims: list[Verdict] = Field(min_length=1, max_length=180)
    missing_essential_fact_ids: list[str] = Field(default_factory=list, max_length=300)
    repair_slide_indices: list[int] = Field(default_factory=list, max_length=30)
    narrative_coherent: bool
    explanation: str = Field(default="", max_length=800)


_ORDINAL_WORDS = {
    "1": r"перв(?:ый|ая|ое|ые|ого|ой|ому|ым|ом|ую|ых|ыми)|first",
    "2": r"втор(?:ой|ая|ое|ые|ого|ому|ым|ом|ую|ых|ыми)|second",
    "3": r"трет(?:ий|ья|ье|ьи|ьего|ьей|ьему|ьим|ьем|ью|ьих|ьими)|third",
    "4": r"четвёрт(?:ый|ая|ое|ые|ого|ой|ому|ым|ом|ую|ых|ыми)|четверт(?:ый|ая|ое|ые|ого|ой|ому|ым|ом|ую|ых|ыми)|fourth",
    "5": r"пят(?:ый|ая|ое|ые|ого|ой|ому|ым|ом|ую|ых|ыми)|fifth",
    "6": r"шест(?:ой|ая|ое|ые|ого|ому|ым|ом|ую|ых|ыми)|sixth",
    "7": r"седьм(?:ой|ая|ое|ые|ого|ому|ым|ом|ую|ых|ыми)|seventh",
    "8": r"восьм(?:ой|ая|ое|ые|ого|ому|ым|ом|ую|ых|ыми)|eighth",
    "9": r"девят(?:ый|ая|ое|ые|ого|ой|ому|ым|ом|ую|ых|ыми)|ninth",
    "10": r"десят(?:ый|ая|ое|ые|ого|ой|ому|ым|ом|ую|ых|ыми)|tenth",
}
_ORDINAL_PATTERNS = {
    number: re.compile(r"\b(?:" + words + r")\b", re.IGNORECASE)
    for number, words in _ORDINAL_WORDS.items()
}


def nums(text):
    """Normalize digits and spelled ordinals in cited facts to the same value."""
    values = Counter(
        n.replace(",", ".")
        for n in re.findall(r"[-−+]?\d+(?:[.,]\d+)?", re.sub(r"(?<=\d)[-–—](?=\d)", " ", text))
    )
    for number, pattern in _ORDINAL_PATTERNS.items():
        count = len(pattern.findall(text))
        if count:
            values[number] += count
    return values


_TIME_WORD_STEMS = (
    "январ",
    "феврал",
    "март",
    "апрел",
    "мая",
    "май",
    "июн",
    "июл",
    "август",
    "сентябр",
    "октябр",
    "ноябр",
    "декабр",
    "зим",
    "весн",
    "лето",
    "летом",
    "осен",
    "утр",
    "вечер",
    "ноч",
    "недел",
    "месяц",
    "квартал",
    "januar",
    "februar",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "winter",
    "spring",
    "summer",
    "autumn",
    "morning",
    "evening",
    "week",
    "month",
    "quarter",
)


def grounded_time_label(label, evidence):
    """Require a visible time marker that occurs in the cited source facts."""
    label = label.casefold()
    evidence = evidence.casefold()
    years = re.findall(r"(?<!\d)(?:1\d{3}|20\d{2}|21\d{2})(?!\d)", label)
    dates = re.findall(r"(?<!\d)\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?(?!\d)", label)
    if any(value in evidence for value in years + dates):
        return True
    return any(
        re.search(r"\b" + stem, label) and re.search(r"\b" + stem, evidence)
        for stem in _TIME_WORD_STEMS
    )


def validate_plan(raw, content, bounds, character_budget=600, require_cover=False):
    plan = EditorialPlan.model_validate(raw)
    if not bounds[0] <= len(plan.slides) <= bounds[1]:
        raise plan_error(
            "slide_count",
            f"Count must be within {bounds}; select essential ideas, do not add slides",
            slide=None,
            action="stop",
        )
    facts = {f.id: f for f in content.facts}
    tables = {t.id: t for t in content.tables}
    used = set()
    signatures = {}
    issues = []
    if require_cover and plan.slides[0].purpose != "cover":
        issues.append(
            RepairIssue(
                code="cover_required",
                message="s1: first slide must be a concise cover included in the total slide count",
                slide=1,
                action="revise_content",
            )
        )
    for slide_index, slide in enumerate(plan.slides, 1):
        try:
            if slide.purpose == "timeline" and any(
                not b.group.strip() or not re.search(r"[A-Za-zА-Яа-я]", b.text)
                for b in slide.bullets
            ):
                raise plan_error(
                    "timeline_labels",
                    f"s{slide_index}: timeline needs a dated group label and a concise event or stage name in each text, not bare dates or numbered dates",
                    slide=slide_index,
                    action="revise_content",
                )
            if slide.purpose == "divider":
                raise plan_error(
                    "divider_disallowed",
                    f"s{slide_index}: divider filler is not allowed",
                    slide=slide_index,
                    action="revise_content",
                )
            if slide.purpose == "cover" and (
                slide_index != 1
                or len(slide.bullets) > 1
                or slide.source_table_id
                or sum(len(b.text) for b in slide.bullets) > 140
            ):
                raise plan_error(
                    "cover_contract",
                    f"s{slide_index}: cover must be first, with one short grounded subtitle and no table",
                    slide=slide_index,
                    action="revise_content",
                )
            if sum(len(c.text) + len(c.group) for c in slide.bullets) > character_budget:
                issues.append(
                    RepairIssue(
                        code="text_budget",
                        message=f"s{slide_index}: condense bullets and labels to {character_budget} characters; retain essential meaning",
                        slide=slide_index,
                        action="shorten_text",
                    )
                )
            # The table ID already binds unchanged cells to their source. It is
            # evidence for the slide, not automatically for individual claims.
            if slide.source_table_id and slide.source_table_id not in tables:
                raise plan_error(
                    "unknown_table",
                    f"s{slide_index}: Unknown source_table_id; use a supplied table ID",
                    slide=slide_index,
                    action="revise_content",
                )
            slide_sources = {
                f.id
                for f in facts.values()
                if slide.source_table_id and f.source == slide.source_table_id
            }
            labels = {b.group for b in slide.bullets}
            for b in slide.bullets:
                if b.parent_group is not None and (
                    b.parent_group not in labels or b.parent_group == b.group
                ):
                    raise plan_error(
                        "invalid_parent",
                        f"s{slide_index}: A structural parent must be another explicit group",
                        slide=slide_index,
                        action="revise_content",
                    )
            parents = {}
            for b in slide.bullets:
                if b.group in parents and parents[b.group] != b.parent_group:
                    raise plan_error(
                        "conflicting_parent",
                        f"s{slide_index}: Conflicting structural parent",
                        slide=slide_index,
                        action="revise_content",
                    )
                parents[b.group] = b.parent_group
            for label in parents:
                seen = set()
                node = label
                while node is not None:
                    if node in seen:
                        raise plan_error(
                            "cyclic_hierarchy",
                            f"s{slide_index}: Cyclic hierarchy",
                            slide=slide_index,
                            action="revise_content",
                        )
                    seen.add(node)
                    node = parents.get(node)
            for claim_index, claim in enumerate(slide.bullets, 1):
                evidence = []
                for cite in claim.evidence:
                    if cite.fact_id not in facts:
                        raise plan_error(
                            "unknown_fact",
                            f"s{slide_index}b{claim_index}: unknown source fact {cite.fact_id}",
                            slide=slide_index,
                            claim=claim_index,
                            action="revise_content",
                        )
                    if cite.quote is None:
                        # Resolve references on the server instead of making the
                        # model transcribe evidence. Review still sees the FULL fact.
                        cite.quote = facts[cite.fact_id].text[:3000]
                    if cite.quote not in facts[cite.fact_id].text:
                        raise plan_error(
                            "invalid_citation",
                            f"s{slide_index}b{claim_index}: citation {cite.fact_id} must quote its source fact exactly; omit quote to resolve the original on the server",
                            slide=slide_index,
                            claim=claim_index,
                            action="revise_content",
                        )
                    slide_sources.add(cite.fact_id)
                    evidence.append(facts[cite.fact_id].text)
                if slide.purpose == "timeline" and not grounded_time_label(
                    claim.group, " ".join(evidence)
                ):
                    issues.append(
                        RepairIssue(
                            code="timeline_without_time",
                            message=(
                                f"s{slide_index}b{claim_index}: timeline labels need dates or time "
                                "markers present in cited source facts. Use process for explicit "
                                "steps, or content for narrated events; replace the current label."
                            ),
                            slide=slide_index,
                            claim=claim_index,
                            action="revise_content",
                        )
                    )
                # Repeating an evidenced value is valid; ownership/context are checked
                # independently by the semantic reviewer. Include visible labels.
                missing = set(nums(claim.text + " " + claim.group)) - set(nums(" ".join(evidence)))
                if missing:
                    issues.append(
                        RepairIssue(
                            code="unsupported_number",
                            message=f"s{slide_index}b{claim_index}: Unsupported number {sorted(missing)}; cite source facts containing it or remove it",
                            slide=slide_index,
                            claim=claim_index,
                            action="revise_content",
                        )
                    )
                signature = " ".join(claim.text.casefold().split())
                if signature in signatures:
                    raise plan_error(
                        "duplicate_claim",
                        f"s{slide_index}b{claim_index}: duplicate of {signatures[signature]}: {claim.text}. Rewrite or remove only this duplicate claim.",
                        slide=slide_index,
                        claim=claim_index,
                        action="revise_content",
                    )
                signatures[signature] = f"s{slide_index}b{claim_index}"
            evidence = " ".join(facts[f].text for f in slide_sources)
            missing = set(nums(slide.title)) - set(nums(evidence))
            if missing:
                issues.append(
                    RepairIssue(
                        code="unsupported_number",
                        message=f"s{slide_index} title: Unsupported number {sorted(missing)}; cite the dated source or remove it",
                        slide=slide_index,
                        action="revise_content",
                    )
                )
            if slide.source_table_id:
                if slide.source_table_id not in tables:
                    raise plan_error(
                        "unknown_table",
                        f"s{slide_index}: Unknown source_table_id; use a supplied table ID",
                        slide=slide_index,
                        action="revise_content",
                    )
                if slide.rows:
                    raise plan_error(
                        "duplicate_data_binding",
                        f"s{slide_index}: source_table_id already retains all source rows. Set rows=[]; do not also extract rows.",
                        slide=slide_index,
                        action="revise_content",
                    )
            if slide.source_columns:
                columns = slide.source_columns
                if (
                    not slide.source_table_id
                    or len(columns) < 2
                    or columns[0] != 0
                    or len(set(columns)) != len(columns)
                    or any(
                        c < 0 or c >= len(tables[slide.source_table_id].headers) for c in columns
                    )
                ):
                    raise plan_error(
                        "invalid_columns",
                        f"s{slide_index}: invalid source table column projection",
                        slide=slide_index,
                        action="revise_content",
                    )
            if slide.chart_type != "auto" and not (slide.source_table_id or slide.rows):
                raise plan_error(
                    "missing_chart_data",
                    f"s{slide_index}: chart needs source data",
                    slide=slide_index,
                    action="revise_content",
                )
            for row_index, row in enumerate(slide.rows, 1):
                if row.fact_id not in slide_sources:
                    raise plan_error(
                        "uncited_row",
                        f"s{slide_index} row {row_index}: cite the source fact {row.fact_id} in this slide before using its data",
                        slide=slide_index,
                        action="revise_content",
                    )
                for field in ("label", "value"):
                    value = getattr(row, field)
                    if value not in facts[row.fact_id].text:
                        raise plan_error(
                            "ungrounded_row",
                            f"s{slide_index} row {row_index}: {field} {value!r} must be a verbatim substring of source fact {row.fact_id}; copy the original spelling, without abbreviating or changing numbers",
                            slide=slide_index,
                            action="revise_content",
                        )
            if slide.rows and (
                len(slide.rows) < 2
                or slide.relationship == "none"
                or len({r.label for r in slide.rows}) != len(slide.rows)
            ):
                raise plan_error(
                    "invalid_data_relationship",
                    f"s{slide_index}: Data relationship and distinct categories required",
                    slide=slide_index,
                    action="revise_content",
                )
            used.update(slide_sources)
        except PlanValidationError as exc:
            issues.extend(exc.issues)
    for tid, table in tables.items():
        uses = [(i, s) for i, s in enumerate(plan.slides, 1) if s.source_table_id == tid]
        if uses:
            covered = set(
                c for _, s in uses for c in (s.source_columns or range(len(table.headers)))
            )
            if covered != set(range(len(table.headers))):
                issues.append(
                    RepairIssue(
                        code="missing_table_columns",
                        message=f"s{uses[0][0]}: retain every source table column across its separate charts",
                        slide=uses[0][0],
                        action="revise_content",
                    )
                )
    if issues:
        raise PlanValidationError(issues)
    omitted = [o.fact_id for o in plan.omitted]
    if len(omitted) != len(set(omitted)) or not set(omitted) <= set(facts):
        raise plan_error(
            "invalid_omissions",
            "Invalid or contradictory omission references",
            slide=None,
            action="stop",
        )
    # Models can label a table as omitted from prose because it is already
    # displayed as data. source_table_id retains its cells, so its owner fact
    # is represented, never omitted. Reconcile this derived bookkeeping only
    # after validating table IDs, projections and column coverage above.
    displayed_tables = {slide.source_table_id for slide in plan.slides if slide.source_table_id}
    displayed_facts = {fid for fid, fact in facts.items() if fact.source in displayed_tables}
    # Explicit, validated citations also prove inclusion. A model's stale
    # omission list must not contradict the content we actually render.
    plan.omitted = [o for o in plan.omitted if o.fact_id not in displayed_facts | used]
    omitted = [o.fact_id for o in plan.omitted]
    # Compute coverage on the server. The separate reviewer still sees every
    # omitted source fact and must flag any lost essential meaning.
    for fid in facts:
        if fid not in used and fid not in omitted:
            plan.omitted.append(
                Omission(
                    fact_id=fid,
                    reason="detail",
                    explanation="Не включено в краткую версию; оригинал сохранён.",
                )
            )
    from .security_gate import check_text_fields

    check_text_fields(editorial=plan.model_dump_json())
    return plan.model_dump()


def review_payload(plan, content):
    # Evidence is resolved from the complete source by ID. Avoid sending each
    # source quotation twice in claims and again in the slide descriptions.
    claims = []
    slides = []
    for i, slide in enumerate(plan["slides"], 1):
        ids = []
        for j, claim in enumerate(slide["bullets"], 1):
            cid = f"s{i}b{j}"
            ids.append(cid)
            claims.append(
                {
                    "claim_id": cid,
                    "title": slide["title"],
                    **claim,
                    "evidence": [{"fact_id": e["fact_id"]} for e in claim["evidence"]],
                }
            )
        slides.append({k: v for k, v in slide.items() if k != "bullets"} | {"claim_ids": ids})
    return {
        "source": content.model_dump(),
        "claims": claims,
        "slides": slides,
        "omitted": plan["omitted"],
    }


def validate_review(raw, claim_ids, source_ids):
    review = EditorialReview.model_validate(raw)
    if [c.claim_id for c in review.claims] != claim_ids:
        raise ValueError("Review must cover every claim in order")
    if not set(review.missing_essential_fact_ids) <= set(source_ids):
        raise ValueError("Unknown source fact in review")
    return review.model_dump()


def apply_plan(package, plan, review, bounds, source_content=None):
    source = source_content or package.original_content or package.content.model_copy(deep=True)
    original_tables = {t.id: t for t in source.tables}
    facts = []
    tables = []
    groups = []
    bindings = []
    provenance = []
    for i, slide in enumerate(plan["slides"], 1):
        ids = []
        semantic = {}
        parents = {}
        for j, claim in enumerate(slide["bullets"], 1):
            fid = f"summary-{i}-{j}"
            ids.append(fid)
            facts.append(Fact(id=fid, text=claim["text"], section=slide["title"]))
            label = claim["group"] or (
                str(j) if slide["purpose"] in ("process", "timeline") else ""
            )
            semantic.setdefault(label, []).append(fid)
            parents[label] = claim.get("parent_group")
            provenance.append({"fact_id": fid, **claim})
        tid = slide["source_table_id"]
        if tid:
            table = original_tables[tid].model_copy(deep=True)
            columns = slide.get("source_columns", [])
            if columns:
                table.headers = [table.headers[c] for c in columns]
                table.rows = [[row[c] for c in columns] for row in table.rows]
            # Each slide owns a separate object, even when it projects the same source.
            tid = f"summary-data-{i}"
            table.id = tid
            table.section = slide["title"]
            if slide.get("chart_type", "auto") != "auto":
                table.visualization = slide["chart_type"]
            elif slide["relationship"] != "none":
                table.visualization = choose_visualization(table, slide["relationship"])
            tables.append(table)
        elif slide["rows"]:
            tid = f"summary-data-{i}"
            table = TableData(
                id=tid,
                headers=["Показатель / период", "Значение"],
                rows=[[r["label"], r["value"]] for r in slide["rows"]],
                section=slide["title"],
            )
            table.visualization = (
                slide.get("chart_type", "auto")
                if slide.get("chart_type", "auto") != "auto"
                else choose_visualization(table, slide["relationship"])
            )
            tables.append(table)
        if tid:
            fid = f"table-{i}"
            ids.append(fid)
            facts.append(
                Fact(
                    id=fid,
                    text="; ".join(" — ".join(row) for row in tables[-1].rows),
                    source=tid,
                    section=slide["title"],
                )
            )
        groups.append({"title": slide["title"], "purpose": slide["purpose"], "fact_ids": ids})
        if not tid:
            bindings.append(
                {
                    "purpose": slide["purpose"],
                    "fact_ids": ids,
                    "groups": [
                        {"label": label, "fact_ids": fids, "parent": parents[label]}
                        for label, fids in semantic.items()
                    ],
                }
            )
    package.content = ContentModel(
        title=source.title,
        facts=facts,
        tables=tables,
        warnings=source.warnings,
        quarantined=source.quarantined,
    )
    package.analysis["section_groups"] = groups
    package.analysis["editorial"] = {
        "status": "completed",
        "source_fact_count": len(source.facts),
        "summary_fact_count": len(facts),
        "provenance": provenance,
        "omitted": [
            dict(o, source_text=next(f.text for f in source.facts if f.id == o["fact_id"]))
            for o in plan["omitted"]
        ],
        "review": review,
        "bindings": bindings,
        "plan": plan,
        "requested_range": list(bounds),
    }
    package.analysis["narrative"] = {
        "status": "completed",
        "requested_range": list(bounds),
        "groups": groups,
        "message": f"Выделены главные мысли для {len(groups)} слайдов. Второстепенных фрагментов исключено: {len(plan['omitted'])}. Оригинал и причины отбора сохранены.",
        "method": "evidence_linked_editorial_selection",
    }
    package.analysis["document_structure"] = {
        "status": "completed",
        "method": "editorial_selection_and_synthesis",
        "body_blocks": len(facts),
    }
