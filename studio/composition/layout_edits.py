"""Authorized layout operations shared by repair and stage results."""

from typing import Literal
from pydantic import Field
from studio.models import StrictModel


class LayoutEdit(StrictModel):
    variant: Literal["executive", "analytical", "story"]
    slide: int = Field(ge=1, le=30)
    operation: Literal["change_layout", "readable_chart"] = "change_layout"
    pattern_id: str | None = Field(default=None, min_length=1, max_length=120)


class RepairBatch(StrictModel):
    edits: list[LayoutEdit] = Field(max_length=30)
