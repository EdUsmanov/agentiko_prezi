"""Public editorial API; domain validation is independent of repair orchestration."""

from studio.contents.editorial_domain import (
    Citation as Citation,
    Claim as Claim,
    EditorialSlide as EditorialSlide,
    Omission as Omission,
    EditorialPlan as EditorialPlan,
    Verdict as Verdict,
    EditorialReview as EditorialReview,
    nums as nums,
    validate_plan as validate_plan,
    review_payload as review_payload,
    validate_review as validate_review,
    apply_plan as apply_plan,
)


async def prepare_editorial(
    package, gateway, progress=None, *, starting_plan=None, quality_feedback=None
):
    from studio.contents.editorial_repair import prepare_with_targeted_repairs

    return await prepare_with_targeted_repairs(
        package, gateway, progress, starting_plan=starting_plan, quality_feedback=quality_feedback
    )
