from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
from studio.templates.archetype_catalog import Archetype

JsonObject = dict[str, JsonValue]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fact(StrictModel):
    id: str
    text: str
    section: str = ""
    source: str = "user_text"
    line: int = 0
    list_item: bool = False
    emphasis: str = ""
    kind: Literal["body", "caveat"] = "body"


class TableData(StrictModel):
    id: str
    headers: list[str]
    rows: list[list[str]]
    section: str = ""
    visualization: Literal[
        "auto", "table", "metrics", "bar", "column", "column_stacked", "line", "pie"
    ] = "auto"


class ContentModel(StrictModel):
    title: str
    facts: list[Fact]
    tables: list[TableData] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    quarantined: list[dict] = Field(default_factory=list)
    headings: list[dict] = Field(default_factory=list)
    directives: list[dict] = Field(default_factory=list)


class UploadedImage(StrictModel):
    id: str
    name: str
    path: str
    sha256: str
    width: int
    height: int
    section: str = ""
    caption: str = ""
    presentation: Literal["plain", "device"] = "plain"


class Constraints(StrictModel):
    slides: int = Field(default=10, ge=1, le=30)
    audience: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=5000)
    count_mode: Literal["exact", "maximum", "minimum", "default"] = "default"
    size_preset: Literal["mini", "standard", "large"] | None = None
    summarize: bool = False
    confirm_plan: bool = False
    include_cover: bool = True


class Box(StrictModel):
    x: float
    y: float
    w: float
    h: float


class TableBorder(StrictModel):
    color: str = ""
    width: float = Field(default=0, ge=0)
    opacity: float = Field(default=1, ge=0, le=1)


class TableCellStyle(StrictModel):
    fill: str = ""
    fill_opacity: float = Field(default=0, ge=0, le=1)
    color: str = ""
    font_size: float = Field(default=0, ge=0)
    bold: bool = False
    margins: list[float] = Field(default_factory=list, max_length=4)
    borders: dict[Literal["L", "R", "T", "B"], TableBorder] = Field(default_factory=dict)


class TableTemplate(StrictModel):
    """Reusable cell geometry and styling; source sample text is never stored."""

    box: Box
    column_widths: list[float]
    row_heights: list[float]
    cells: list[list[TableCellStyle]]
    source_shape_id: int = Field(default=0, ge=0)


class TemplateResource(StrictModel):
    schema_version: Literal[1] = 1
    id: str
    kind: Literal["icon", "device_frame"]
    source_slide: int = Field(ge=1)
    shape_ids: list[int] = Field(min_length=1)
    box: Box
    preview_path: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    screen_box: Box | None = None


class Pattern(StrictModel):
    id: str
    source_slide: int
    color_scheme_id: str = ""
    source_layout: str
    text_zones: list[Box]
    role: str
    master_index: int = 0
    layout_index: int = 0
    title_zone: Box | None = None
    body_zones: list[Box] = Field(default_factory=list)
    title_size: float = 0
    background: str = ""
    foreground: str = ""
    background_image: str = ""
    safe_text_zone: dict = Field(default_factory=dict)
    title_foreground: str = ""
    title_background: str = ""
    zone_backgrounds: list[str] = Field(default_factory=list)
    zone_foregrounds: list[str] = Field(default_factory=list)
    heading_zones: list[Box | None] = Field(default_factory=list)
    graphic_count: int = 0
    graphic_order_verified: bool = False
    graphic_kind: Literal[
        "none", "cards", "sequence", "comparison", "hierarchy", "radial", "pyramid", "matrix"
    ] = "none"
    graphic_shape_ids: list[int] = Field(default_factory=list)
    graphic_edges: list[tuple[int, int]] = Field(default_factory=list, max_length=160)
    number_zones: list[Box | None] = Field(default_factory=list)
    table_style: dict = Field(default_factory=dict)
    table_template: TableTemplate | None = None
    purpose: Archetype | Literal["unknown", "service", "reference"] = "unknown"
    reusable: bool = True
    reference_image: str = ""
    image_zones: list[Box] = Field(default_factory=list)
    fields: list[dict] = Field(default_factory=list)


class Asset(StrictModel):
    id: str
    path: str
    box: Box
    occurrences: int
    role: str


class TemplateProfile(StrictModel):
    background_source: str = ""
    resource_source: str = ""
    resources: list[TemplateResource] = Field(default_factory=list)
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
    font_origin: dict = Field(default_factory=dict)
    font_roles: dict[str, str] = Field(default_factory=dict)
    font_assets: list[dict] = Field(default_factory=list)
    font_replacements: list[dict] = Field(default_factory=list)
    missing_fonts: list[dict] = Field(default_factory=list)
    font_sizes: list[float]
    title_size: float
    body_size: float
    colors: list[str]
    color_roles: dict[str, list[str]] = Field(default_factory=dict)
    color_schemes: list[dict] = Field(default_factory=list)
    color_analysis: dict = Field(default_factory=dict)
    background: str
    foreground: str
    accent: str
    margin: float
    patterns: list[Pattern]
    assets: list[Asset] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_kind: str
    layout_index: int = 0
    analysis_version: int = 1


class SlidePlan(StrictModel):
    title: str = Field(min_length=1, max_length=240)
    fact_ids: list[str]
    layout: Literal[
        "statement", "split", "columns", "table", "chart", "process", "evidence", "divider"
    ] = "split"
    table_id: str | None = None
    role: Literal["context", "insight", "evidence", "action", "appendix"] = "insight"
    pattern_id: str | None = None
    background_pattern_id: str | None = None
    purpose: Archetype | Literal["auto"] = "auto"
    chart_type: Literal["auto", "bar", "column", "column_stacked", "line", "pie"] = "auto"
    chart_style: Literal["standard", "readable"] = "standard"


class VariantPlan(StrictModel):
    key: Literal["executive", "analytical", "story"]
    title: str
    slides: list[SlidePlan]


class Plans(StrictModel):
    variants: list[VariantPlan] = Field(min_length=1, max_length=3)

    @field_validator("variants")
    @classmethod
    def complete_variant_selection(cls, variants):
        keys = [v.key for v in variants]
        if keys not in (["executive"], ["executive", "analytical", "story"]):
            raise ValueError("Expected one executive variant or all three ordered variants")
        return variants


class DraftBullet(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    fact_ids: list[str] = Field(default_factory=list, max_length=300)
    proposed: bool = False


class DraftSlide(StrictModel):
    title: str = Field(min_length=1, max_length=240)
    purpose: Archetype | Literal["auto"] = "content"
    bullets: list[DraftBullet] = Field(min_length=1, max_length=30)


class BriefDraft(StrictModel):
    schema_version: Literal[1] = 1
    slides: list[DraftSlide] = Field(min_length=1, max_length=30)


class SlideBudget(StrictModel):
    status: Literal["exact", "adjusted", "needs_input", "ready", "ok", "sufficient"]
    requested: int | None = None
    count_mode: str | None = None
    planned: int | None = None
    required: int | None = None
    cover: int | None = None
    dividers: int | None = None
    content_slides: int | None = None
    requested_range: list[int] | None = None
    fit_issues: list[JsonObject] = Field(default_factory=list)
    message: str = ""


class PreparationControl(StrictModel):
    """Execution decisions sealed with the package, separate from audit diagnostics."""

    model_mode: Literal["", "api", "extractive"] = ""
    planning_status: Literal["", "needs_input", "completed", "degraded"] = ""
    planning_source: str = ""
    planned_slides: int | None = None
    slide_budget: SlideBudget | None = None


class PreparedPackage(StrictModel):
    schema_version: Literal[2] = 2
    id: str
    created_at: str
    template: TemplateProfile
    content: ContentModel
    original_content: ContentModel | None = None
    constraints: Constraints
    manifest: dict
    analysis: dict = Field(default_factory=dict)
    prepared_plans: Plans | None = None
    images: list[UploadedImage] = Field(default_factory=list)
    input_mode: Literal["content", "brief"] = "content"
    draft: BriefDraft | None = None
    brief_evidence: ContentModel | None = None
    control: PreparationControl = Field(default_factory=PreparationControl)


class Element(StrictModel):
    kind: Literal["text", "rect", "line", "image", "table", "chart"]
    box: Box
    text: str = ""
    font: str = ""
    size: float = 0
    color: str = ""
    fill: str = ""
    fill_opacity: float = Field(default=1, ge=0, le=1)
    body_fill: str = ""
    body_fill_opacity: float = Field(default=0, ge=0, le=1)
    bold: bool = False
    field_style: dict = Field(default_factory=dict)
    table_template: TableTemplate | None = None
    role: str = "body"
    rows: list[list[str]] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    value_labels: list[str] = Field(default_factory=list)
    unit: str = ""
    image_path: str = ""
    image_id: str = ""
    resource_id: str = ""
    source_ids: list[str] = Field(default_factory=list)
    background_hint: str = ""
    bullet: bool = False
    bold_prefix: str = ""
    chart_type: Literal["bar", "column", "column_stacked", "line", "pie"] = "bar"
    category_title: str = ""
    series_names: list[str] = Field(default_factory=list)
    series_values: list[list[float]] = Field(default_factory=list)
    chart_style: Literal["standard", "readable"] = "standard"


class SlideScene(StrictModel):
    title: str
    background: str
    elements: list[Element]
    source_ids: list[str]
    layout: str
    purpose: Archetype | Literal["auto"] = "auto"
    pattern_id: str | None = None
    background_pattern_id: str | None = None
    strategy: str = "token_composition"
    notes: str = ""


class ContextualAudit(StrictModel):
    findings: list["ContextualFinding"] = Field(max_length=100)


class Finding(StrictModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    slide: int = 0
    element: int | None = None
    repaired: bool = False


class ContextualFinding(StrictModel):
    variant: Literal["executive", "analytical", "story"]
    slide: int = Field(ge=1, le=30)
    title_quote: str
    fact_ids: list[str] = Field(min_length=1, max_length=300)
    code: str
    severity: Literal["warning", "error"]
    message: str


class AuditFinding(StrictModel):
    id: str
    source: str
    variant: Literal["executive", "analytical", "story"] | None = None
    slide: int | None = Field(default=None, ge=1, le=30)
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    action: Literal["change_layout", "readable_chart"] | None = None
    unsupported_reason: str = ""
    element: int | None = None
    scene_box: Box | None = None
    repaired: bool = False


class FinalAuditSnapshot(StrictModel):
    schema_version: Literal[1] = 1
    generation_id: str
    package_id: str
    findings: list[AuditFinding]
    quality_report: JsonObject
    parent_generation_id: str | None = None
    selected_finding_ids: list[str] = Field(default_factory=list)
    generated_at: float
    files: dict[str, str] = Field(default_factory=dict)
