"""Versioned contract for a directory extraction report."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pyapi.domain.font_model import MissingFont


class FontFileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presentation: str
    status: Literal["ok", "error"]
    slides: int = Field(default=0, ge=0)
    resolvedVariants: int = Field(default=0, ge=0)
    unresolvedVariants: int = Field(default=0, ge=0)
    warningCount: int = Field(default=0, ge=0)
    modelPath: str | None = None
    unresolved: list[MissingFont] = Field(default_factory=list)
    error: str | None = None


class BatchCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    templates: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    slides: int = Field(ge=0)
    resolvedVariants: int = Field(ge=0)
    unresolvedVariants: int = Field(ge=0)


class BatchFontReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1] = 1
    counts: BatchCounts
    presentations: list[FontFileResult]

    @classmethod
    def from_results(cls, results: list[FontFileResult]) -> "BatchFontReport":
        return cls(
            counts=BatchCounts(
                templates=len(results),
                succeeded=sum(row.status == "ok" for row in results),
                failed=sum(row.status == "error" for row in results),
                slides=sum(row.slides for row in results),
                resolvedVariants=sum(row.resolvedVariants for row in results),
                unresolvedVariants=sum(row.unresolvedVariants for row in results),
            ),
            presentations=results,
        )
