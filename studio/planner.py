import re
from .models import Plans, VariantPlan, SlidePlan
from .security import INJECTION
from .content import numeric_column

NAMES = {"executive": "Главное и решения", "analytical": "Данные и доказательства", "story": "Контекст и развитие"}

def short_title(text, max_chars=145):
    sentence = re.split(r"(?<=[.!?])\s", text)[0].rstrip(".")
    if len(sentence) <= max_chars:
        return sentence
    return sentence[:max_chars].rsplit(" ", 1)[0] + "…"

def extractive_plans(package):
    facts = package.content.facts
    count = min(package.constraints.slides, len(facts))
    variants = []
    tables = {t.id: t for t in package.content.tables}
    for vi, key in enumerate(NAMES):
        ordered = list(facts)
        if key == "executive":
            ordered = sorted(facts, key=lambda f: (not bool(re.search(r"\d", f.text)), facts.index(f)))
        elif key == "analytical":
            ordered = sorted(facts, key=lambda f: (f.source not in tables, facts.index(f)))
        groups = [[] for _ in range(count)]
        # Preserve order, distribute all facts, no drop or repeated filler.
        for i, f in enumerate(ordered):
            groups[min(count-1, i * count // len(ordered))].append(f)
        slides = []
        for i, group in enumerate(groups):
            tid = next((f.source for f in group if f.source in tables), None)
            if tid:
                layout = "chart" if vi == 0 and numeric_column(tables[tid]) else "table"
            elif key == "executive":
                layout = "statement" if len(group) == 1 else "columns"
            elif key == "analytical":
                layout = "evidence"
            else:
                layout = "split"
            claim = group[0]
            # Single-fact slides use a topic title and keep the full source fact in body.
            title = (claim.section or package.content.title) if len(group) == 1 else short_title(claim.text)
            if tid:
                title = tables[tid].section or "Сравнение исходных данных"
            slides.append(SlidePlan(title=short_title(title), fact_ids=[f.id for f in group],
                layout=layout, table_id=tid, role="context" if i == 0 else "evidence" if tid else "insight"))
        variants.append(VariantPlan(key=key, title=NAMES[key], slides=slides))
    return Plans(variants=variants)

def validate_plans(plans, package):
    if [v.key for v in plans.variants] != list(NAMES):
        raise ValueError("Нужны три уникальных варианта в заданном порядке")
    facts = {f.id: f for f in package.content.facts}
    tables = {t.id: t for t in package.content.tables}
    for variant in plans.variants:
        target = min(package.constraints.slides, len(facts))
        if len(variant.slides) != target:
            raise ValueError("Неверное количество слайдов")
        used = set()
        for slide in variant.slides:
            if not slide.fact_ids or not set(slide.fact_ids) <= facts.keys():
                raise ValueError("План ссылается на несуществующие факты")
            used.update(slide.fact_ids)
            evidence = " ".join(facts[f].text for f in slide.fact_ids) + " " + package.content.title + " " + " ".join(facts[f].section for f in slide.fact_ids)
            if INJECTION.search(slide.title):
                raise ValueError("Инструкция вместо заголовка")
            if not set(re.findall(r"\d+(?:[.,]\d+)?", slide.title)) <= set(re.findall(r"\d+(?:[.,]\d+)?", evidence)):
                raise ValueError("Неподтверждённое число в заголовке")
            if slide.table_id and (slide.table_id not in tables or not any(facts[f].source == slide.table_id for f in slide.fact_ids)):
                raise ValueError("Таблица не связана с фактами слайда")
            if slide.layout in ("table", "chart") and not slide.table_id:
                raise ValueError("Нет данных для таблицы/графика")
            if slide.layout == "chart" and not numeric_column(tables[slide.table_id]):
                slide.layout = "table"
        if used != facts.keys():
            raise ValueError("В варианте потеряны исходные факты")
    signatures = {tuple((s.title, tuple(s.fact_ids), s.layout) for s in v.slides) for v in plans.variants}
    if len(signatures) != 3:
        raise ValueError("Варианты должны различаться")
    return plans

async def plan(package, gateway, timeout):
    fallback_reason = None
    if gateway.settings.mode == "api":
        try:
            raw = await gateway.json_request("planner", {"content": package.content.model_dump(),
                "constraints": package.constraints.model_dump(), "font": package.template.font,
                "palette": package.template.colors, "target_slides": min(package.constraints.slides, len(package.content.facts))},
                timeout=timeout, schema=Plans.model_json_schema())
            return validate_plans(Plans.model_validate(raw), package), None
        except Exception as exc:
            fallback_reason = f"Модельный план отклонён ({type(exc).__name__}); использован экстрактивный план без новых фактов"
    return validate_plans(extractive_plans(package), package), fallback_reason
