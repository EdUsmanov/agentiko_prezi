"""Generation orchestration: stage ordering, progress and ownership of results."""

from .config import Settings
from .gateway import ModelGateway
from .models import PreparedPackage
from .store import Store
from .stage_runtime import GenerationDeadline, StageClock
from .generation_planning import plan_generation
from .generation_composition import compose_generation, export_variants
from .generation_reviews import review_generation
from .generation_audit import audit_variant_exports, audit_diversity, summarize_audits
from .generation_publication import publish_generation


async def run_generation(
    store: Store,
    job_id: str,
    settings: Settings,
    gateway: ModelGateway,
    package: PreparedPackage,
    version_snapshot: dict[str, str],
    git_revision: str,
) -> None:
    clock = StageClock()
    job = store.get(job_id)
    directory = store.directory(job_id)
    if package.analysis.get("slide_budget", {}).get("status") == "needs_input":
        raise ValueError(package.analysis["slide_budget"]["message"])
    if package.template.analysis_version < 9:
        raise ValueError(
            "Обновлён анализ шаблонов: повторите анализ материалов перед новой генерацией. Старые результаты сохранены; новый анализ определяет смысловые блоки и архетипы по общему каталогу."
        )
    deadline = GenerationDeadline(job["deadline_at"])
    count = job.get("variant_count", 3)
    store.update(
        job_id,
        "running",
        phase="Планирование одного варианта" if count == 1 else "Планирование трёх вариантов",
        progress=8,
    )
    planning = await plan_generation(package, settings, gateway, directory, store, job_id, deadline)
    clock.checkpoint("planning_and_design_seconds")
    (directory / "generation-content.json").write_text(package.content.model_dump_json(indent=2))
    (directory / "plans.json").write_text(planning.plans.model_dump_json(indent=2))
    store.update(job_id, phase="Вёрстка, аудит и экспорт", progress=35)
    source = store.directory(package.id) / "input.pptx"
    composition = compose_generation(planning.plans, package, directory)
    store.update(job_id, phase="Создаём файлы PPTX, PDF и предпросмотр", progress=50)
    clock.checkpoint("composition_and_local_audit_seconds")
    variants = await export_variants(composition, package, source, directory, deadline)
    clock.checkpoint("render_and_export_seconds")
    store.update(job_id, phase="Сверяем готовые презентации с исходным материалом", progress=88)
    reviewed = await review_generation(
        composition,
        variants,
        package,
        directory,
        source,
        gateway,
        settings,
        store,
        job_id,
        deadline,
        clock.checkpoint,
    )
    audit_variant_exports(reviewed.variants, reviewed.plans, reviewed.decks, package, directory)
    diversity = audit_diversity(
        reviewed.plans, reviewed.decks, package, directory, composition.diversity
    )
    audit = summarize_audits(
        reviewed.variants,
        package,
        settings,
        diversity,
        reviewed.contextual,
        reviewed.visual,
        planning.fallback,
        planning.source,
    )
    clock.checkpoint("final_local_audits_seconds")
    publish_generation(
        store,
        job_id,
        job,
        package,
        settings,
        gateway,
        directory,
        deadline,
        planning,
        composition,
        reviewed,
        audit,
        clock.timings,
        version_snapshot,
        git_revision,
    )
