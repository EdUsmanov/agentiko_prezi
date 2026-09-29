"""Propose content for an explicitly selected short brief."""

import re
from pydantic import Field
from studio.models import StrictModel, Fact
from studio.providers.induction import InductionFailure, validated_request
from studio.security import INJECTION


class Proposals(StrictModel):
    proposals: list[str] = Field(min_length=1, max_length=30)


class AuthorValidationError(ValueError):
    """A safe, fixed explanation to feed back to the model and user."""


async def expand_brief(package, gateway, timeout):
    missing = package.constraints.slides - len(package.content.facts)
    if gateway.settings.mode != "api" or missing <= 0:
        return package, None
    try:

        def validate(raw):
            proposals = Proposals.model_validate(raw).proposals
            if len(proposals) != missing or len(set(x.casefold() for x in proposals)) != missing:
                raise AuthorValidationError("Неверное число или дубликаты проектных тезисов")
            for proposal in proposals:
                if not 50 <= len(proposal) <= 320:
                    raise AuthorValidationError("Проектный тезис должен содержать 50–320 символов")
                if INJECTION.search(proposal):
                    raise AuthorValidationError("Проектный тезис содержит недопустимую инструкцию")
                if re.search(r"\d", proposal):
                    raise AuthorValidationError(
                        "Проектный тезис содержит число; числа остаются в исходных фактах"
                    )
            return {"proposals": proposals}

        response = await validated_request(
            gateway,
            "author",
            {
                "brief": package.content.model_dump(),
                "audience": package.constraints.audience,
                "instructions": package.constraints.instructions,
                "requested_count": missing,
            },
            Proposals.model_json_schema(),
            validate,
            timeout=timeout,
        )
        proposals = response["proposals"]
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
                    text=p,
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
        reason = exc.__cause__ if isinstance(exc, InductionFailure) else exc
        detail = str(reason) if isinstance(reason, AuthorValidationError) else type(exc).__name__
        return (
            package,
            f"Расширение краткого brief не выполнено ({detail}); неподтверждённое содержание не добавлено",
        )
