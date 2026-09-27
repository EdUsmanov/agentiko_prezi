"""Choose a plan and run the configured design engine within one deadline."""

from pathlib import Path
from .config import Settings
from .gateway import ModelGateway
from .models import PreparedPackage
from .planner import plan, validate_plans, assign_compositions
from .stage_results import PlanningResult, EngineReport
from .stage_runtime import GenerationDeadline
from .store import Store


async def plan_generation(
    package: PreparedPackage,
    settings: Settings,
    gateway: ModelGateway,
    directory: Path,
    store: Store,
    job_id: str,
    deadline: GenerationDeadline,
) -> PlanningResult:
    if package.prepared_plans and package.analysis.get("model_mode") == settings.mode:
        plans = assign_compositions(validate_plans(package.prepared_plans, package), package)
        analysis = package.analysis
        model_preparation_failed = (
            analysis.get("planning_status") == "degraded"
            or analysis.get("template_semantics", {}).get("status") in ("partial", "failed")
            or analysis.get("document_structure", {}).get("status") in ("degraded", "failed")
        )
        fallback = (
            (
                "; ".join(analysis.get("warnings", []))
                or "Модельная подготовка завершилась не полностью"
            )
            if model_preparation_failed
            else None
        )
        planning_source = package.analysis["planning_source"]
    else:
        # Backward-compatible path for immutable packages created before semantic preparation.
        plans, fallback = await plan(package, gateway, min(120, deadline.remaining(60)))
        planning_source = "extractive" if fallback or settings.mode == "extractive" else "model"
    engine_report = {"engine": "native", "status": "completed"}
    if settings.engine == "deeppresenter":
        from .deeppresenter import design

        store.update(job_id, phase="DeepPresenter: выбор и проверка композиций", progress=20)
        # Reserve export + final audit inside the SAME server deadline for all variants.
        budget = None if deadline.deadline_at is None else min(90, deadline.remaining(150))
        try:
            plans, engine_report = await design(
                package, plans, gateway, directory / "design-runtime", budget
            )
        except TimeoutError as exc:
            deadline.remaining()  # Distinguish sub-stage timeout from the configured job deadline.
            raise ValueError(
                "DeepPresenter не успел завершить выбор композиций за бюджет отдельного запроса. "
                "Модель не ответила вовремя или этап не завершён. Повторите генерацию: повторный анализ не нужен. "
                "Движок не заменялся автоматически."
            ) from exc
        except Exception as exc:
            raise ValueError(
                "DeepPresenter не завершил проверенную композицию ("
                + type(exc).__name__
                + "). Подмена движка не выполнялась."
            ) from exc
    return PlanningResult(
        plans, planning_source, fallback, EngineReport.model_validate(engine_report)
    )
