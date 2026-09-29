from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import BriefDraft


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_id: str
    accept_adjusted_slide_count: bool = False


class ReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instructions: str = Field(min_length=1, max_length=5000)
    slides: int | None = Field(default=None, ge=1, le=30)
    size_preset: Literal["mini", "standard", "large"] | None = None


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_hash: str = Field(min_length=64, max_length=64)
    draft_hash: str = Field(min_length=64, max_length=64)


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_hash: str = Field(min_length=64, max_length=64)
    draft: BriefDraft


class RepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audit_hash: str = Field(min_length=64, max_length=64)
    finding_ids: list[str] = Field(min_length=1, max_length=30)
