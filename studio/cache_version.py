"""Atomic cache writes and pipeline versioning; no organizer-library dependency."""

import importlib.metadata
import json
from pathlib import Path
import sys
import tempfile

from .config import ROOT
from .security import digest


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        json.dump(value, output, ensure_ascii=False)
        temporary = Path(output.name)
    temporary.replace(path)


def pipeline_version():
    files = [p for p in (ROOT / "studio").rglob("*") if p.suffix in (".py", ".mjs")]
    for folder in ("prompts", "config", "vendor/opendesign"):
        files.extend(p for p in (ROOT / folder).rglob("*") if p.is_file())
    files += [ROOT / "requirements.lock", ROOT / "pyproject.toml"]
    versions = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted(files)}
    for name in ("python-pptx", "Pillow", "defusedxml", "pydantic"):
        versions["runtime:" + name] = importlib.metadata.version(name)
    versions["python"] = sys.version
    return digest(json.dumps(versions, sort_keys=True).encode())


def analysis_version():
    """Version the user-input analysis cache independently of server scheduling.

    Keep coverage for analysis helpers, schemas, fonts, prompts and classifiers.
    Process restart safety is tracked separately by pipeline_version.
    """
    excluded = {
        "app.py",
        "presentation_service.py",
        "runtime.py",
        "cli.py",
        "worker.py",
        "store.py",
        "pipeline.py",
        "diagnostics.py",
        "provider_transport.py",
    }
    files = [
        p
        for p in (ROOT / "studio").rglob("*")
        if p.suffix in (".py", ".mjs")
        and p.name not in excluded
        and p.relative_to(ROOT / "studio").parts[0] != "api"
        and not p.name.startswith("pptagent")
        and "deeppresenter" not in p.parts
    ]
    for folder in ("prompts", "config", "vendor/opendesign"):
        files.extend(
            p for p in (ROOT / folder).rglob("*") if p.is_file() and p.name != "policy.json"
        )
    # Policy affects model permission/limits as well as generation. Exclude only
    # the scheduling field, never silently ignore security policy changes.
    from .config import POLICY

    policy = {k: v for k, v in POLICY.items() if k != "generation_deadline_seconds"}
    versions = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted(files)}
    versions["policy"] = policy
    for name in ("python-pptx", "Pillow", "numpy", "defusedxml", "pydantic"):
        versions["runtime:" + name] = importlib.metadata.version(name)
    versions["python"] = sys.version
    return digest(json.dumps(versions, sort_keys=True).encode())


def dependency_version(names):
    """Hash explicit stage dependencies instead of unrelated application code."""
    files = []
    for name in names:
        path = ROOT / name
        if not path.exists():
            raise FileNotFoundError(f"Cache dependency does not exist: {name}")
        files.extend(
            p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts
        ) if path.is_dir() else files.append(path)
    versions = {
        str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted(set(files)) if p.is_file()
    }
    for name in ("python-pptx", "Pillow", "defusedxml", "pydantic", "pypdfium2"):
        versions["runtime:" + name] = importlib.metadata.version(name)
    versions["python"] = sys.version
    return digest(json.dumps(versions, sort_keys=True).encode())


TEMPLATE_DEPENDENCIES = [
    "studio/cache_version.py",
    "studio/composition/text_layout.py",
    "studio/checks/export_audit.py",
    "studio/composition/charts.py",
    "studio/composition/chart_layout.py",
    "studio/composition/table_style.py",
    "studio/composition/metrics.py",
    "studio/templates/template_analysis.py",
    "studio/templates/template_resources.py",
    "studio/preparation/intelligence.py",
    "studio/preparation",
    "studio/templates/template_cache.py",
    "studio/templates/parsing.py",
    "studio/templates/template_geometry.py",
    "studio/composition/shape_geometry.py",
    "studio/composition/native_surface.py",
    "studio/composition/pptx_text.py",
    "studio/contents/content_sources.py",
    "studio/templates/native_template.py",
    "studio/templates/template_adaptation.py",
    "studio/templates/portable_templates.py",
    "studio/checks/text_zone_review.py",
    "studio/checks/raster_review.py",
    "studio/templates/artwork.py",
    "studio/templates/colors.py",
    "studio/composition/pictures.py",
    "studio/models.py",
    "studio/composition/contracts.py",
    "studio/security.py",
    "studio/security_gate.py",
    "studio/composition/powerpoint.py",
    "studio/composition/render.py",
    "studio/composition/office.py",
    "studio/templates/field_style.py",
    "studio/templates/fonts.py",
    "studio/templates/font_coverage.py",
    "studio/templates/font_identity.py",
    "studio/templates/font_extraction.py",
    "studio/templates/font_disclosure.py",
    "studio/providers/induction.py",
    "studio/providers/gateway.py",
    "studio/templates/archetype_catalog.py",
    "studio/templates/native_style.py",
    "studio/_vendor/portable_background_extractor",
    "studio/_vendor/portable_text_zone_finder",
    "studio/_vendor/color_extraction",
    "studio/_vendor/font_extraction",
    "prompts/template_analyst.md",
    "prompts/template_resources.md",
    "prompts/text_zone.md",
    "prompts/background_raster.md",
    "config/archetypes.json",
    "config/reasoning.json",
    "config/policy.json",
    "requirements.lock",
]


def template_version():
    return dependency_version(TEMPLATE_DEPENDENCIES)


STAGE_BASE_DEPENDENCIES = [
    "studio/cache_version.py",
    "studio/providers/induction.py",
    "studio/providers/gateway.py",
    "studio/models.py",
    "studio/templates/archetype_catalog.py",
    "studio/checks/repair_errors.py",
    "studio/generation/results.py",
    "studio/composition/layout_edits.py",
    "studio/checks/repair_policy.py",
    "studio/security.py",
    "studio/security_gate.py",
    "studio/config.py",
    "config/reasoning.json",
    "config/policy.json",
]

STAGE_DEPENDENCIES = {
    "author": ["studio/contents/author.py"],
    "template_resources": ["studio/templates/template_resources.py"],
    "template_analyst": [
        "studio/templates/template_analysis.py",
        "studio/templates/colors.py",
        "studio/templates/archetype_catalog.py",
        "config/archetypes.json",
    ],
    "text_zone": ["studio/checks/text_zone_review.py", "studio/_vendor/portable_text_zone_finder"],
    "table_headers": ["studio/contents/editorial_tables.py"],
    "editorial": [
        "studio/contents/editorial_domain.py",
        "studio/contents/narrative_data.py",
        "studio/contents/narrative_layout.py",
        "studio/contents/editorial.py",
        "studio/contents/editorial_repair.py",
        "studio/contents/editorial_outline.py",
        "studio/contents/parsing.py",
        "studio/contents/narrative.py",
    ],
    "editorial_outline": [
        "studio/contents/editorial_domain.py",
        "studio/contents/narrative_data.py",
        "studio/contents/narrative_layout.py",
        "studio/contents/editorial_outline.py",
        "studio/contents/editorial.py",
    ],
    "editorial_slides": [
        "studio/contents/editorial_domain.py",
        "studio/contents/narrative_data.py",
        "studio/contents/narrative_layout.py",
        "studio/contents/editorial_outline.py",
        "studio/contents/editorial.py",
    ],
    "editorial_review": [
        "studio/contents/editorial_domain.py",
        "studio/contents/narrative_data.py",
        "studio/contents/narrative_layout.py",
        "studio/contents/editorial.py",
        "studio/contents/editorial_repair.py",
    ],
    "editorial_repair": [
        "studio/contents/editorial_domain.py",
        "studio/contents/narrative_data.py",
        "studio/contents/narrative_layout.py",
        "studio/contents/editorial.py",
        "studio/contents/editorial_repair.py",
        "studio/contents/editorial_patch_validation.py",
    ],
    "visual_critic": [
        "studio/checks/visual.py",
        "studio/contents/uploads.py",
        "studio/diagnostics.py",
    ],
    "critic": [
        "studio/checks/content_review.py",
        "studio/generation/reviews.py",
        "studio/checks/review_grounding.py",
    ],
    "document": ["studio/contents/document.py"],
    "sections": ["studio/contents/sections.py", "studio/contents/section_metadata.py"],
    "content_archetypes": [
        "studio/contents/archetypes.py",
        "studio/templates/archetype_catalog.py",
        "config/archetypes.json",
    ],
}


def stage_version(stage):
    if stage not in STAGE_DEPENDENCIES:
        return analysis_version()
    return dependency_version(
        STAGE_BASE_DEPENDENCIES + STAGE_DEPENDENCIES[stage] + [f"prompts/{stage}.md"]
    )
