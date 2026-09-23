from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Fact(StrictModel):
    id: str
    text: str
    section: str = ""
    source: str = "user_text"
    line: int = 0

class TableData(StrictModel):
    id: str
    headers: list[str]
    rows: list[list[str]]
    section: str = ""

class ContentModel(StrictModel):
    title: str
    facts: list[Fact]
    tables: list[TableData] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    quarantined: list[dict] = Field(default_factory=list)

class Constraints(StrictModel):
    slides: int = Field(default=10, ge=1, le=30)
    audience: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=5000)
    count_mode: Literal["exact", "maximum", "default"] = "default"

class Box(StrictModel):
    x: float
    y: float
    w: float
    h: float

class Pattern(StrictModel):
    id: str
    source_slide: int
    source_layout: str
    text_zones: list[Box]
    role: str

class Asset(StrictModel):
    id: str
    path: str
    box: Box
    occurrences: int
    role: str

class TemplateProfile(StrictModel):
    sha256: str
    name: str
    width: float
    height: float
    slide_count: int
    master_count: int
    layout_count: int
    object_count: int
    placeholder_count: int
    fonts: list[str]
    font: str
    font_file: str = ""
    font_sizes: list[float]
    title_size: float
    body_size: float
    colors: list[str]
    background: str
    foreground: str
    accent: str
    margin: float
    patterns: list[Pattern]
    assets: list[Asset] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_kind: str
    layout_index: int = 0

class PreparedPackage(StrictModel):
    schema_version: int = 1
    id: str
    created_at: str
    template: TemplateProfile
    content: ContentModel
    constraints: Constraints
    manifest: dict

class SlidePlan(StrictModel):
    title: str = Field(min_length=1, max_length=240)
    fact_ids: list[str]
    layout: Literal["statement", "split", "columns", "table", "chart", "process", "evidence"] = "split"
    table_id: str | None = None
    role: Literal["context", "insight", "evidence", "action", "appendix"] = "insight"

class VariantPlan(StrictModel):
    key: Literal["executive", "analytical", "story"]
    title: str
    slides: list[SlidePlan]

class Plans(StrictModel):
    variants: list[VariantPlan] = Field(min_length=3, max_length=3)

class Element(StrictModel):
    kind: Literal["text", "rect", "line", "image", "table", "chart"]
    box: Box
    text: str = ""
    font: str = ""
    size: float = 0
    color: str = ""
    fill: str = ""
    bold: bool = False
    role: str = "body"
    rows: list[list[str]] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    unit: str = ""
    image_path: str = ""
    source_ids: list[str] = Field(default_factory=list)

class SlideScene(StrictModel):
    title: str
    background: str
    elements: list[Element]
    source_ids: list[str]
    layout: str
    pattern_id: str | None = None
    strategy: str = "token_composition"
    notes: str = ""

class Finding(StrictModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    slide: int = 0
    element: int | None = None
    repaired: bool = False
