from pathlib import Path
import json
import subprocess
from studio.config import ROOT, Settings
from studio.models import PreparedPackage
from studio.security import digest
from studio.templates.parsing import analyze_template
from studio.providers.gateway import ModelGateway
from studio.preparation.intelligence import prepare_intelligence
from studio.security_gate import check_package


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
    input_mode="content",
    draft=None,
    allowed_fact_ids=None,
):
    from studio.preparation.contracts import PreparationRequest, PreparationServices
    from studio.preparation.orchestrator import run_preparation

    request = PreparationRequest(
        text,
        audience,
        instructions,
        slides,
        content_model,
        base_constraints,
        input_mode,
        draft,
        allowed_fact_ids,
    )
    services = PreparationServices(
        analyze_template, prepare_intelligence, ModelGateway, versions, revision
    )
    return run_preparation(
        store, job_id, request, settings or Settings(data_dir=store.root), services
    )


def preanalyze_template(store, job_id, settings=None):
    from studio.preparation.contracts import PreparationServices
    from studio.preparation.orchestrator import run_template_preanalysis

    services = PreparationServices(
        analyze_template, prepare_intelligence, ModelGateway, versions, revision
    )
    return run_template_preanalysis(
        store, job_id, settings or Settings(data_dir=store.root), services
    )


def load_package(store, package_id):
    job = store.get(package_id)
    if job["state"] != "ready":
        raise ValueError("Подготовка не завершена")
    raw = (store.directory(package_id) / "package.json").read_bytes()
    if digest(raw) != job["package_hash"]:
        raise ValueError("Подготовленный пакет был изменён после анализа")
    if json.loads(raw).get("schema_version") != 2:
        raise ValueError("Формат подготовленного пакета устарел. Повторите подготовку материалов")
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
    if package.template.resources:
        source = Path(package.template.resource_source).resolve()
        if source != (store.directory(package_id) / "input.pptx").resolve():
            raise ValueError("Источник ресурсов не совпадает с загруженным шаблоном")
        for resource in package.template.resources:
            preview = Path(resource.preview_path).resolve()
            if not preview.is_relative_to(store.root) or not preview.is_file():
                raise ValueError("Ресурс шаблона недоступен. Повторите подготовку")
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
    from studio.generation.flow import run_generation

    job = store.get(job_id)
    if job.get("operation") == "repair":
        from studio.generation.repair import run_repair

        return await run_repair(
            store,
            job_id,
            settings,
            gateway,
            load_package(store, job["package_id"]),
            versions(),
            revision(),
        )
    package = load_package(store, job["package_id"])
    if package.input_mode == "brief":
        source = store.get(package.id)
        from studio.contents.brief import assert_draft_matches_package, draft_hash

        assert_draft_matches_package(package)

        if (
            package.draft is None
            or source.get("approved_package_hash") != source.get("package_hash")
            or source.get("approved_draft_hash") != draft_hash(package.draft)
        ):
            raise ValueError("Утвердите план и текст краткого брифа перед генерацией")
    return await run_generation(store, job_id, settings, gateway, package, versions(), revision())
