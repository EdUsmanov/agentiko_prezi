"""Structured output of the reference-font extraction and resolution pipeline."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FontUse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: str
    script: Literal["latin", "ea", "cs", "sym"]
    weight: int = Field(ge=100, le=900)
    style: Literal["normal", "italic"]
    sizesPt: list[float] | None = None
    source: str
    evidence: Literal["observed", "placeholder", "size-heuristic", "text-heuristic"]
    shapeIds: list[str]
    characters: int | None = None
    assetId: str | None = None
    status: Literal["embedded", "installed", "downloaded", "missing"]


class SlideFontData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(gt=0)
    kind: Literal["title", "content"]
    elements: dict[str, list[FontUse]] | None = None


class FontRoleSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: str
    script: Literal["latin", "ea", "cs", "sym"]
    weight: int
    style: Literal["normal", "italic"]
    sizesPt: list[float] | None = None
    slideNumbers: list[int]
    sources: list[str]
    evidence: list[str]
    assetId: str | None = None
    status: Literal["embedded", "installed", "downloaded", "missing"]


class FontAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    family: str
    resolvedFamily: str
    weight: int
    style: Literal["normal", "italic"]
    source: Literal["embedded", "installed", "downloaded"]
    origin: str | None = None
    path: str
    sha256: str
    bytes: int


class MissingFont(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: str
    weight: int
    style: Literal["normal", "italic"]


class PresentationFontData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[2] = 2
    presentation: str
    slides: list[SlideFontData]
    usageBySlideKind: dict[str, dict[str, list[FontRoleSummary]]]
    fontAssets: list[FontAsset]
    unresolved: list[MissingFont]
    warnings: list[str]
