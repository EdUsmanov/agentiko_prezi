"""Optional brief expansion in preparation (legacy packages: during generation)."""

import re
from pydantic import Field
from .models import StrictModel, Fact
from .security import INJECTION


class Proposals(StrictModel):
    proposals: list[str] = Field(min_length=1, max_length=30)


async def expand_brief(package, gateway, timeout):
    missing = package.constraints.slides - len(package.content.facts)
    if gateway.settings.mode != "api" or missing <= 0:
        return package, None
    try:
        response = await gateway.json_request(
            "author",
            {
                "brief": package.content.model_dump(),
                "audience": package.constraints.audience,
                "instructions": package.constraints.instructions,
                "requested_count": missing,
            },
            timeout=timeout,
            schema=Proposals.model_json_schema(),
        )
        proposals = Proposals.model_validate(response).proposals
        if len(proposals) != missing or len(set(x.casefold() for x in proposals)) != missing:
            raise ValueError("Неверное число или дубликаты проектных тезисов")
        for p in proposals:
            if not (30 <= len(p) <= 400) or INJECTION.search(p) or re.search(r"\d", p):
                raise ValueError("Проектный тезис содержит неподтверждённое число или инструкцию")
        result = package.model_copy(deep=True)
        existing = {f.id for f in result.content.facts}
        next_id = 1
        for p in proposals:
            while f"draft{next_id}" in existing:
                next_id += 1
            fact_id = f"draft{next_id}"
            existing.add(fact_id)
            result.content.facts.append(
                Fact(
                    id=fact_id,
                    text="Проектный тезис — требует проверки: " + p,
                    section="Предлагаемый подход",
                    source="model_proposal",
                    line=0,
                )
            )
        result.content.warnings.append(
            f"Модель добавила {missing} проектных тезисов для короткого brief. Они помечены в слайдах и не являются подтверждёнными фактами."
        )
        return result, None
    except Exception as exc:
        return (
            package,
            f"Расширение краткого brief не выполнено ({type(exc).__name__}); неподтверждённое содержание не добавлено",
        )
