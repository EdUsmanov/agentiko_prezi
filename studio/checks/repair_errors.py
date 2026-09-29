"""Machine-readable repair decisions; diagnostic wording never controls routing."""

from collections.abc import Iterable
from typing import Literal

from pydantic import ConfigDict, Field, ValidationError

from studio.models import StrictModel

RepairAction = Literal["revise_content", "shorten_text", "adapt_layout", "stop"]


class RepairIssue(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str
    action: RepairAction
    slide: int | None = Field(default=None, ge=1)
    claim: int | None = Field(default=None, ge=1)
    element: int | None = Field(default=None, ge=0)


class RepairFailure(ValueError):
    """Compatible with existing ValueError boundaries, with explicit locations."""

    def __init__(self, issues: Iterable[RepairIssue]):
        self.issues = tuple(issues)
        if not self.issues:
            raise ValueError("A repair failure must contain at least one issue")
        super().__init__("; ".join(issue.message for issue in self.issues))

    def public(self) -> list[dict]:
        return [issue.model_dump(mode="json") for issue in self.issues]


class PlanValidationError(RepairFailure):
    pass


class LayoutCapacityError(RepairFailure):
    """Deterministic layout alternatives exhausted; prose is not the remedy."""


def plan_error(
    code: str,
    message: str,
    *,
    slide: int | None = None,
    claim: int | None = None,
    action: RepairAction = "revise_content",
) -> PlanValidationError:
    return PlanValidationError(
        [RepairIssue(code=code, message=message, slide=slide, claim=claim, action=action)]
    )


def validation_issues(error: ValueError) -> tuple[RepairIssue, ...]:
    if isinstance(error, RepairFailure):
        return error.issues
    if not isinstance(error, ValidationError):
        # An unexpected failure must not become an editorial request merely
        # because a source quotation happens to contain an identifier like s2.
        return ()
    issues = []
    for row in error.errors(include_input=False, include_context=False, include_url=False):
        loc = row["loc"]
        slide = (
            loc[1] + 1 if len(loc) > 1 and loc[0] == "slides" and isinstance(loc[1], int) else None
        )
        claim = (
            loc[3] + 1 if len(loc) > 3 and loc[2] == "bullets" and isinstance(loc[3], int) else None
        )
        issues.append(
            RepairIssue(
                code="schema." + row["type"],
                message=row["msg"],
                slide=slide,
                claim=claim,
                action="revise_content" if slide else "stop",
            )
        )
    return tuple(issues)
