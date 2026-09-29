"""Values passed between the template and content preparation stages."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..config import Settings
from studio.providers.gateway import ModelGateway
from ..models import (
    BriefDraft,
    Constraints,
    ContentModel,
    JsonObject,
    PreparedPackage,
    TemplateProfile,
)


@dataclass(frozen=True)
class PreparationRequest:
    text: str
    audience: str
    instructions: str
    slides: int | None
    content_model: ContentModel | None = None
    base_constraints: Constraints | None = None
    input_mode: str = "content"
    draft: BriefDraft | None = None
    allowed_fact_ids: list[str] | None = None


@dataclass(frozen=True)
class TemplatePreparation:
    profile: TemplateProfile
    cached_analysis: JsonObject | None


@dataclass
class TemplateAnalysisResult:
    profile: TemplateProfile
    analysis: JsonObject
    template_layers: dict[str, str]


@dataclass(frozen=True)
class PreparationServices:
    analyze_template: Callable[..., TemplateProfile]
    prepare_intelligence: Callable[..., Awaitable[PreparedPackage]]
    gateway_factory: Callable[[Settings], ModelGateway]
    versions: Callable[[], dict[str, str]]
    revision: Callable[[], str]
