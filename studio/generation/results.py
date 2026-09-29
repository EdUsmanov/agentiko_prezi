"""Validated generation boundaries, separate from the persisted package format.

Use attributes within orchestration. ``wire()`` is the explicit JSON boundary for
legacy review implementations, HTTP responses, manifests and saved diagnostics.
Only extensible diagnostic payloads (events, field bindings) use JSON maps.
"""

from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from studio.composition.layout_edits import LayoutEdit
from studio.models import Plans, SlideScene, Finding, ContextualFinding, JsonObject

VariantKey = Literal["executive", "analytical", "story"]


class StageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, strict=True)

    def wire(self) -> JsonObject:
        # Do not introduce missing optional keys into historical report shapes.
        return self.model_dump(mode="json", exclude_unset=True)


class RepairRecord(StageResult):
    code: str
    message: str
    slide: int = 0
    element: int | None = None
    severity: Literal["info", "warning", "error"] = "info"
    repaired: bool = False
    scale: float | None = None


class RenderingResult(StageResult):
    preview_source: Literal["libreoffice_pptx", "scene_model"]
    native_render: bool
    object_findings: list[Finding] = Field(default_factory=list)
    object_repairs: list[RepairRecord] = Field(default_factory=list)
    font_substitutions: list[JsonObject] = Field(default_factory=list)


class SemanticBinding(StageResult):
    status: Literal["general", "specialized"]
    reason: str
    archetype: str
    pattern_id: str | None
    fields: list[JsonObject]


class VariantResult(StageResult):
    key: VariantKey
    title: str
    slides: int = Field(ge=1, le=30)
    rendering: RenderingResult
    template_strategies: list[str]
    findings: list[Finding]
    repairs: list[RepairRecord]
    initial_errors: int = Field(ge=0)
    export_findings: list[Finding] = Field(default_factory=list, exclude=True)
    semantic_bindings: list[SemanticBinding] = Field(default_factory=list)

    def review_input(self) -> JsonObject:
        # Export findings are transient repair evidence, not duplicate final findings.
        return {**self.wire(), "export_findings": [f.model_dump() for f in self.export_findings]}


class ContentReviewResult(StageResult):
    status: Literal["not_run", "completed", "failed"]
    findings: list[ContextualFinding]
    reason: str = ""
    type: Literal["exported_pptx_text_review"] = "exported_pptx_text_review"
    checked: int | None = Field(default=None, ge=0)
    unique_slides: int | None = Field(default=None, ge=0)
    batches: int | None = Field(default=None, ge=0)


class VisualReviewFinding(StageResult):
    variant: VariantKey
    slide: int = Field(ge=1, le=30)
    code: str
    severity: Literal["warning", "error"]
    message: str


class VisualReviewResult(StageResult):
    status: Literal["not_run", "completed", "failed"]
    findings: list[VisualReviewFinding]
    checked: int = Field(ge=0)
    total: int = Field(ge=0)
    batches: list[JsonObject]
    model: str
    method: str
    reason: str = ""
    request_adjustments: list[JsonObject] = Field(default_factory=list)
    http_status: int | None = None
    seconds: float | None = Field(default=None, ge=0)
    refinement_review: "VisualReviewResult | None" = None
    revision: int | None = None

    @model_validator(mode="after")
    def complete_coverage(self):
        if self.checked > self.total or self.status == "completed" and self.checked != self.total:
            raise ValueError("Completed visual review must cover every rendered slide exactly once")
        return self


class RefinementReport(StageResult):
    status: Literal[
        "not_needed", "not_run", "running", "unresolved", "rejected", "completed", "failed"
    ]
    attempts: int = Field(ge=0)
    accepted: bool
    edits: list[LayoutEdit]
    proposed_edits: list[LayoutEdit] = Field(default_factory=list)
    rejected_proposals: int = Field(default=0, ge=0)
    proposal_error: str = ""
    reason: str = ""
    seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def accepted_revision(self):
        if self.accepted != (self.status == "completed") or self.accepted and not self.edits:
            raise ValueError("An accepted refinement requires completed review and explicit edits")
        return self


class EngineReport(StageResult):
    engine: Literal["native", "deeppresenter"]
    status: Literal["completed", "failed", "timed_out"]
    commit: str = ""
    mode: str = ""
    turns: int = Field(default=0, ge=0)
    model_requests: int = Field(default=0, ge=0)
    budget_seconds: float | None = None
    events: list[JsonObject] = Field(default_factory=list)
    repairs: list[JsonObject] = Field(default_factory=list)
    seconds: float | None = Field(default=None, ge=0)
    visual_reflection: bool = False


class MeaningfulDiversity(StageResult):
    verified: bool
    pairs: list[JsonObject]
    method: str


class BackgroundDiversity(StageResult):
    background_colors: dict[str, int] = Field(default_factory=dict)
    source_slides: list[int | None]
    families: list[str]
    unique_backgrounds: int = Field(ge=0)
    largest_use: int = Field(ge=0)
    adjacent_repeats: int = Field(ge=0)


class DiversityResult(StageResult):
    policy: str
    distinct: int = Field(ge=0)
    expected: int = Field(ge=0)
    verified: bool
    adjustments: list[JsonObject]
    signatures: dict[str, str]
    findings: list[Finding]
    method: str = ""
    meaningful: MeaningfulDiversity | None = None
    within_decks: dict[str, BackgroundDiversity] = Field(default_factory=dict)


@dataclass(frozen=True)
class PlanningResult:
    plans: Plans
    source: str
    fallback: str | None
    engine: EngineReport


@dataclass
class CompositionResult:
    plans: Plans
    decks: dict[str, list[SlideScene]]
    initial_findings: dict[str, list[Finding]]
    repairs: dict[str, list[Finding]]
    diversity: DiversityResult
    cache_hits: int
    cache_misses: int


@dataclass(frozen=True)
class ReviewedGeneration:
    plans: Plans
    decks: dict[str, list[SlideScene]]
    variants: list[VariantResult]
    contextual: ContentReviewResult
    visual: VisualReviewResult
    refinement: RefinementReport

    def __post_init__(self) -> None:
        expected = [(v.key, len(v.slides)) for v in self.plans.variants]
        actual = [(v.key, v.slides) for v in self.variants]
        if actual != expected or len({v.key for v in self.variants}) != len(expected):
            raise ValueError("Export results do not match the ordered variant plans")
        if set(self.decks) != {v.key for v in self.variants} or any(
            len(self.decks[v.key]) != v.slides for v in self.variants
        ):
            raise ValueError("Export results do not match the composed decks")


@dataclass(frozen=True)
class FinalAuditResult:
    variants: list[VariantResult]
    diversity: DiversityResult
    warnings: list[str]
    font_substitutions: list[JsonObject]
    errors: int
    native_preview: bool
    model_degraded: bool
