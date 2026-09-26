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
    excluded={"app.py","cli.py","worker.py","store.py","pipeline.py","diagnostics.py","provider_transport.py"}
    files=[p for p in (ROOT/"studio").rglob("*")
           if p.suffix in (".py",".mjs") and p.name not in excluded
           and not p.name.startswith("pptagent") and "deeppresenter" not in p.parts]
    for folder in ("prompts","config","vendor/opendesign"):
        files.extend(p for p in (ROOT/folder).rglob("*") if p.is_file() and p.name!="policy.json")
    # Policy affects model permission/limits as well as generation. Exclude only
    # the scheduling field, never silently ignore security policy changes.
    from .config import POLICY
    policy={k:v for k,v in POLICY.items() if k!="generation_deadline_seconds"}
    versions={str(p.relative_to(ROOT)):digest(p.read_bytes()) for p in sorted(files)}
    versions["policy"]=policy
    for name in ("python-pptx","Pillow","numpy","defusedxml","pydantic"):
        versions["runtime:"+name]=importlib.metadata.version(name)
    versions["python"]=sys.version
    return digest(json.dumps(versions,sort_keys=True).encode())


def dependency_version(names):
    """Hash explicit stage dependencies instead of unrelated application code."""
    files=[]
    for name in names:
        path=ROOT/name
        files.extend(p for p in path.rglob('*') if p.is_file() and '__pycache__' not in p.parts) if path.is_dir() else files.append(path)
    versions={str(p.relative_to(ROOT)):digest(p.read_bytes()) for p in sorted(set(files)) if p.is_file()}
    for name in ('python-pptx','Pillow','defusedxml','pydantic','pypdfium2'):
        versions['runtime:'+name]=importlib.metadata.version(name)
    versions['python']=sys.version
    return digest(json.dumps(versions,sort_keys=True).encode())


TEMPLATE_DEPENDENCIES=[
    'studio/cache_version.py','studio/text_layout.py','studio/export_audit.py','studio/charts.py','studio/table_style.py','studio/metrics.py',
    'studio/template_analysis.py','studio/template_cache.py','studio/template.py',
    'studio/native_template.py','studio/template_adaptation.py','studio/portable_templates.py',
    'studio/text_zone_review.py','studio/artwork.py','studio/colors.py','studio/pictures.py',
    'studio/models.py','studio/contracts.py','studio/security.py','studio/security_gate.py',
    'studio/powerpoint.py','studio/render.py','studio/office.py','studio/field_style.py',
    'studio/fonts.py','studio/font_fallback.py','studio/font_identity.py','studio/font_manifest.py','studio/font_disclosure.py',
    'studio/embedded_fonts.py','studio/font_decoder.py','studio/font_decoder.mjs','studio/open_fonts.py',
    'studio/induction.py','studio/gateway.py','studio/archetype_catalog.py','studio/native_style.py',
    'studio/_vendor/portable_background_extractor','studio/_vendor/portable_text_zone_finder',
    'studio/_vendor/color_extraction','studio/_vendor/mtx_decompressor',
    'prompts/template_analyst.md','prompts/text_zone.md','config/archetypes.json',
    'config/reasoning.json','config/policy.json','requirements.lock']


def template_version():return dependency_version(TEMPLATE_DEPENDENCIES)


def stage_version(stage):
    base=['studio/cache_version.py','studio/induction.py','studio/gateway.py','studio/models.py',
          'studio/security.py','studio/security_gate.py','studio/config.py','config/reasoning.json',
          'config/policy.json','prompts/'+stage+'.md']
    dependencies={
        'template_analyst':['studio/template_analysis.py','studio/archetype_catalog.py','config/archetypes.json'],
        'text_zone':['studio/text_zone_review.py','studio/_vendor/portable_text_zone_finder'],
        'table_headers':['studio/editorial_tables.py'],
        'editorial':['studio/editorial.py','studio/editorial_repair.py','studio/editorial_outline.py','studio/content.py','studio/narrative.py'],
        'editorial_outline':['studio/editorial_outline.py','studio/editorial.py'],
        'editorial_slides':['studio/editorial_outline.py','studio/editorial.py'],
        'editorial_review':['studio/editorial.py','studio/editorial_repair.py'],
        'editorial_repair':['studio/editorial.py','studio/editorial_repair.py','studio/editorial_patch_validation.py'],
        'visual_critic':['studio/visual.py','studio/uploads.py','studio/diagnostics.py'],
        'critic':['studio/content_review.py','studio/pipeline.py'],
        'document':['studio/document.py'],
        'sections':['studio/sections.py'],
        'content_archetypes':['studio/archetypes.py','studio/archetype_catalog.py','config/archetypes.json']}
    return dependency_version(base+dependencies[stage]) if stage in dependencies else analysis_version()
