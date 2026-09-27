from .preparation import preparation_diagnostics as preparation_diagnostics
from .generation_reviews import review_exported_content as review_exported_content
from .review_grounding import grounded_review as grounded_review
from pathlib import Path
import subprocess
from .config import ROOT, Settings
from .models import PreparedPackage
from .security import digest
from .template import analyze_template
from .gateway import ModelGateway
from .analysis import prepare_intelligence
from .security_gate import check_package


def revision():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True, timeout=2
        ).strip()
    except Exception:
        return "uncommitted"


def versions():
    files = (
        list((ROOT / "prompts").glob("*.md"))
        + list((ROOT / "config").glob("*.json"))
        + list((ROOT / "config").glob("*.yaml"))
        + list((ROOT / "studio").rglob("*.py"))
        + list((ROOT / "studio").rglob("*.mjs"))
    )
    return {str(f.relative_to(ROOT)): digest(f.read_bytes()) for f in files}


def prepare(
    store,
    job_id,
    text,
    audience,
    instructions,
    slides,
    settings=None,
    content_model=None,
    base_constraints=None,
):
    from .preparation import PreparationRequest, PreparationServices, run_preparation

    request = PreparationRequest(
        text, audience, instructions, slides, content_model, base_constraints
    )
    services = PreparationServices(
        analyze_template, prepare_intelligence, ModelGateway, versions, revision
    )
    return run_preparation(
        store, job_id, request, settings or Settings(data_dir=store.root), services
    )


def load_package(store, package_id):
    job = store.get(package_id)
    if job["state"] != "ready":
        raise ValueError("Подготовка не завершена")
    raw = (store.directory(package_id) / "package.json").read_bytes()
    if digest(raw) != job["package_hash"]:
        raise ValueError("Подготовленный пакет был изменён после анализа")
    package = PreparedPackage.model_validate_json(raw)
    if digest((store.directory(package_id) / "input.pptx").read_bytes()) != package.template.sha256:
        raise ValueError("Исходный шаблон изменён после анализа")
    font_hash = package.template.font_origin.get("sha256")
    if font_hash and digest(Path(package.template.font_file).read_bytes()) != font_hash:
        raise ValueError("Шрифт изменён после анализа. Повторите подготовку.")
    for asset in package.template.font_assets:
        if (
            not Path(asset["path"]).is_file()
            or digest(Path(asset["path"]).read_bytes()) != asset["sha256"]
        ):
            raise ValueError("Начертание шрифта изменено после анализа. Повторите подготовку.")
    for asset in package.images:
        path = Path(asset.path)
        if (
            not path.resolve().is_relative_to((store.root / "jobs").resolve())
            or not path.is_file()
            or digest(path.read_bytes()) != asset.sha256
        ):
            raise ValueError("Изображение изменено после анализа. Повторите подготовку.")
    for relative, sha in package.manifest.get("template_layers", {}).items():
        layer = store.directory(package_id) / relative
        if not layer.is_file() or digest(layer.read_bytes()) != sha:
            raise ValueError("Фоновый слой изменён после анализа. Повторите подготовку.")
    check_package(package, store.directory(package_id) / "input.pptx")
    return package


async def generate(store, job_id, settings):
    gateway = ModelGateway(settings)
    try:
        return await _generate(store, job_id, settings, gateway)
    finally:
        if hasattr(gateway, "aclose"):
            await gateway.aclose()


async def _generate(store, job_id, settings, gateway):
    from .generation import run_generation

    package = load_package(store, store.get(job_id)["package_id"])
    return await run_generation(store, job_id, settings, gateway, package, versions(), revision())
