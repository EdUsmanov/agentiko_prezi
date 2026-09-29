from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from studio.models import BriefDraft


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_id: str
    accept_adjusted_slide_count: bool = False
    variant_count: Literal[1, 3] = 3

    @field_validator("variant_count", mode="before")
    @classmethod
    def strict_variant_count(cls, value):
        if type(value) is not int or value not in (1, 3):
            raise ValueError("Выберите одну или три презентации")
        return value


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
