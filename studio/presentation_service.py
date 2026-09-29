"""Presentation workflow without HTTP request or response types."""

import asyncio
from collections.abc import AsyncIterable, Awaitable, Callable, Sequence
import json
import os
from pathlib import Path
import shutil
import uuid

from .cache_version import pipeline_version
from .config import Settings
from studio.composition.artifacts import public_path
from studio.contents.brief import (
    apply_edited_draft,
    assert_draft_matches_package,
    draft_hash as hash_draft,
)
from studio.contents.parsing import parse_constraints
from studio.providers.deeppresenter import readiness
from .diagnostics import redact
from studio.templates.examples import sources
from .models import BriefDraft
from .pipeline import load_package
from .security import digest
from .security_gate import check_text_fields
from studio.jobs.store import Store
from studio.jobs.runtime import JobRuntime
from studio.contents.uploads import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    MAX_TOTAL_BYTES,
    bind_image_sections,
    sanitize_image,
)


class ApplicationError(Exception):
    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


class PresentationService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        runtime: JobRuntime,
        *,
        load_operation=None,
        version_operation=None,
    ):
        self.settings = settings
        self.store = store
        self.runtime = runtime
        self.load_operation = load_operation or load_package
        self.version_operation = version_operation or pipeline_version
        self.loaded_pipeline_version = self.version_operation()

    def restart_required(self):
        return self.version_operation() != self.loaded_pipeline_version

    def require_current_pipeline(self):
        if self.restart_required():
            raise ApplicationError(
                "unavailable",
                "Код пайплайна обновлён. Перезапустите приложение перед новым анализом или генерацией.",
            )

    def references(self):
        return sources(self.settings)

    def recent_jobs(self) -> list[dict]:
        return self.store.recent(exclude_kind="template")

    def get_job(self, jid: str) -> dict:
        return self.store.get(jid)

    def preparation_finished(self, pid: str) -> None:
        job = self.store.get(pid)
        if job["state"] != "ready":
            return
        if job.get("auto_generation") == "scheduled":
            self.store.log(pid, "generation.scheduled", delay_seconds=60)
            self.runtime.schedule_auto_generation(pid)
        else:
            self.store.log(pid, "generation.confirmation_required")

    def _persist_request(self, jid: str, payload: dict) -> None:
        """Publish a complete request before the child process can start."""
        directory = self.store.directory(jid)
        target = directory / "request.json"
        temporary = directory / f"request-{uuid.uuid4().hex}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(payload, output, ensure_ascii=False)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def _reference(self, reference_id: str):
        ref = next((r for r in self.references() if r["id"] == reference_id), None)
        if not ref:
            raise ApplicationError("missing", "Неизвестный пример")
        return ref

    @staticmethod
    def _validate_template_name(name: str):
        if name and not name.lower().endswith((".pptx", ".potx")):
            raise ApplicationError("invalid", "Поддерживаются PPTX и POTX без макросов")

    async def _save_template(
        self, jid: str, source: Path | None, chunks: AsyncIterable[bytes] | None
    ) -> Path:
        target = self.store.directory(jid) / "input.pptx"
        if source is not None:
            shutil.copyfile(source, target)
        else:
            size = 0
            with target.open("wb") as out:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self.settings.max_upload_bytes:
                        self.store.update(jid, "failed", error="Шаблон превышает 60 МБ")
                        raise ApplicationError("too_large", "Шаблон превышает 60 МБ")
                    out.write(chunk)
        return target

    async def analyze_template(
        self,
        *,
        reference_id: str = "",
        template_name: str = "",
        template_chunks: AsyncIterable[bytes] | None = None,
    ) -> dict:
        check_text_fields(template_name=template_name)
        self.require_current_pipeline()
        if bool(reference_id) == bool(template_name):
            raise ApplicationError("invalid", "Выберите один шаблон: файл или пример")
        ref = self._reference(reference_id) if reference_id else None
        self._validate_template_name(template_name)
        job = self.store.create(
            "template", {"template_name": ref["name"] if ref else Path(template_name).name}
        )
        source = self.settings.data_dir / "references" / ref["id"] / "input.pptx" if ref else None
        target = await self._save_template(job["id"], source, template_chunks)
        self.store.update(job["id"], upload_sha256=digest(target.read_bytes()))
        self.runtime.analyze_template(job["id"])
        return self.store.get(job["id"])

    async def prepare(
        self,
        *,
        text: str,
        audience: str = "",
        instructions: str = "",
        slides: int | None = None,
        size_preset: str | None = None,
        reference_id: str = "",
        template_job_id: str = "",
        template_name: str = "",
        template_chunks: AsyncIterable[bytes] | None = None,
        images: Sequence[tuple[str, Callable[[int], Awaitable[bytes]]]] = (),
        input_mode: str = "content",
        image_presentation: str = "plain",
    ) -> dict:
        check_text_fields(
            content=text,
            audience=audience,
            instructions=instructions,
            template_name=template_name,
            image_names="\n".join(name for name, _ in images),
        )
        self.require_current_pipeline()
        if len(images) > MAX_IMAGES:
            raise ApplicationError("invalid", "Допускается до 12 изображений")
        if sum(bool(choice) for choice in (reference_id, template_job_id, template_name)) != 1:
            raise ApplicationError("invalid", "Выберите один шаблон: файл или пример")
        if not text.strip():
            raise ApplicationError("invalid", "Добавьте текст")
        if input_mode not in ("content", "brief") or image_presentation not in ("plain", "device"):
            raise ApplicationError("invalid", "Неизвестный режим подготовки")
        ref = self._reference(reference_id) if reference_id else None
        template_job = None
        if template_job_id:
            template_job = self.store.get(template_job_id)
            if template_job["kind"] != "template":
                raise ApplicationError("invalid", "Укажите задание анализа шаблона")
            source = self.store.directory(template_job_id) / "input.pptx"
            if not source.is_file() or digest(source.read_bytes()) != template_job.get(
                "upload_sha256"
            ):
                raise ApplicationError(
                    "conflict", "Загруженный шаблон изменился. Выберите его повторно."
                )
            check_text_fields(template_name=template_job["template_name"])
        self._validate_template_name(template_name)
        name = (
            template_job["template_name"]
            if template_job
            else ref["name"]
            if ref
            else Path(template_name).name
        )
        job = self.store.create("preparation", {"template_name": name})
        if ref:
            source = self.settings.data_dir / "references" / ref["id"] / "input.pptx"
        elif not template_job:
            source = None
        await self._save_template(job["id"], source, template_chunks)
        assets = []
        total = 0
        try:
            for filename, read in images:
                raw = await read(MAX_IMAGE_BYTES + 1)
                total += len(raw)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("Суммарный размер изображений превышает 24 МБ")
                asset = await asyncio.to_thread(
                    sanitize_image, raw, filename, self.store.directory(job["id"]), len(assets) + 1
                )
                asset.presentation = image_presentation
                if any(existing.name.casefold() == asset.name.casefold() for existing in assets):
                    raise ValueError("У картинок должны быть разные имена файлов")
                assets.append(asset)
            assets = bind_image_sections(assets, text)
            (self.store.directory(job["id"]) / "images.json").write_text(
                json.dumps([asset.model_dump() for asset in assets], ensure_ascii=False)
            )
        except ValueError as exc:
            self.store.update(job["id"], "failed", error=str(exc))
            raise ApplicationError("invalid", str(exc)) from exc
        base = (
            parse_constraints(slides, audience, instructions, size_preset) if size_preset else None
        )
        self._persist_request(
            job["id"],
            {
                "text": text,
                "audience": audience,
                "instructions": instructions,
                "slides": slides,
                "content_model": None,
                "base_constraints": base.model_dump() if base else None,
                "input_mode": input_mode,
                "image_presentation": image_presentation,
                "draft": None,
            },
        )
        self.runtime.prepare(job["id"], template_job_id=template_job_id)
        return self.store.get(job["id"])

    def generate(
        self,
        package_id: str,
        *,
        accept_adjusted_slide_count: bool = False,
        automatic: bool = False,
    ) -> dict:
        self.require_current_pipeline()
        if self.settings.engine == "deeppresenter" and not readiness()["ready"]:
            raise ApplicationError(
                "unavailable", "DeepPresenter не готов: установите зависимости deeppresenter"
            )
        try:
            prepared_job = self.store.get(package_id)
            if prepared_job.get("input_mode") == "brief" and prepared_job.get(
                "approved_draft_hash"
            ) != prepared_job.get("draft_hash"):
                raise ValueError("Подтвердите план и текст краткого брифа")
            package = self.load_operation(self.store, package_id)
            if package.input_mode == "brief":
                assert_draft_matches_package(package)
                if (
                    package.draft is None
                    or hash_draft(package.draft) != prepared_job.get("draft_hash")
                    or prepared_job.get("approved_package_hash") != prepared_job.get("package_hash")
                    or prepared_job.get("approved_draft_hash") != prepared_job.get("draft_hash")
                ):
                    raise ValueError("План и текст краткого брифа требуют подтверждения")
            budget = package.control.slide_budget
            if budget and budget.status == "needs_input":
                raise ValueError(budget.message)
            if (
                budget and budget.status == "adjusted" or package.constraints.confirm_plan
            ) and not accept_adjusted_slide_count:
                raise ValueError(
                    (budget.message if budget else "План подготовлен.")
                    + " Подтвердите генерацию с предложенным количеством слайдов."
                )
            job, created = self.store.generation_for(package_id, automatic=automatic)
            if not created:
                return job or self.store.get(package_id)
            deadline = (
                job["created"] + self.settings.deadline_seconds
                if self.settings.deadline_seconds is not None
                else None
            )
            self.store.update(
                job["id"],
                deadline_at=deadline,
                slide_count_decision={
                    "mode": "automatic" if automatic else "confirmed",
                    "count": package.control.planned_slides or package.constraints.slides,
                    "message": (
                        "Система предложила и автоматически выбрала оптимальное количество слайдов"
                        if automatic
                        else "Пользователь подтвердил предложенный план"
                    ),
                },
            )
            job = self.store.get(job["id"])
        except ValueError as exc:
            raise ApplicationError("conflict", str(exc)) from exc
        self.runtime.supervise(job)
        return job

    def retry_fonts(self, pid: str) -> dict:
        self.require_current_pipeline()
        job = self.store.get(pid)
        if job["kind"] != "preparation" or job["state"] != "waiting_fonts":
            raise ApplicationError("conflict", "Задание не ожидает шрифтов")
        directory = self.store.directory(pid)
        raw = (directory / "font-resume.json").read_bytes()
        if (
            digest(raw) != job["resume_hash"]
            or digest((directory / "input.pptx").read_bytes()) != job["template_hash"]
        ):
            raise ApplicationError(
                "conflict", "Сохранённый вход изменён. Загрузите материалы повторно."
            )
        saved = json.loads(raw)
        self.store.update(pid, "accepted", error=None, phase="Повторная проверка шрифтов")
        self._persist_request(
            pid,
            {
                "text": saved["text"],
                "audience": saved["audience"],
                "instructions": saved["instructions"],
                "slides": saved["slides"],
                "content_model": saved["content_model"],
                "base_constraints": saved["base_constraints"],
                "input_mode": saved.get("input_mode", job.get("input_mode", "content")),
                "draft": saved.get("draft"),
                "allowed_fact_ids": saved.get("allowed_fact_ids"),
            },
        )
        self.runtime.prepare(pid)
        return self.store.get(pid)

    def cancel_auto_generation(self, pid: str) -> dict:
        try:
            return self.store.cancel_auto_generation(pid)
        except ValueError as exc:
            raise ApplicationError("conflict", str(exc)) from exc

    def revise(
        self,
        pid: str,
        *,
        instructions: str,
        slides: int | None = None,
        size_preset: str | None = None,
    ) -> dict:
        check_text_fields(instructions=instructions)
        self.require_current_pipeline()
        package = self.load_operation(self.store, pid)
        new = self.store.create(
            "preparation",
            {
                "template_name": package.template.name,
                "parent_package": pid,
                "change_explanation": "Создана новая версия входных ограничений. Исходный пакет и результаты сохранены.",
            },
        )
        shutil.copyfile(
            self.store.directory(pid) / "input.pptx", self.store.directory(new["id"]) / "input.pptx"
        )
        (self.store.directory(new["id"]) / "images.json").write_text(
            json.dumps([asset.model_dump() for asset in package.images], ensure_ascii=False)
        )
        self.store.cancel_auto_generation(pid)
        base = parse_constraints(slides, package.constraints.audience, instructions, size_preset)
        if slides is None and size_preset is None and base.count_mode == "default":
            base = package.constraints.model_copy(deep=True)
        base.summarize = base.confirm_plan = True
        self._persist_request(
            new["id"],
            {
                "text": "",
                "audience": package.constraints.audience,
                "instructions": instructions,
                "slides": slides,
                "content_model": (package.original_content or package.content).model_dump(),
                "base_constraints": base.model_dump(),
                "input_mode": package.input_mode,
                "draft": package.draft.model_dump() if package.draft else None,
            },
        )
        self.runtime.prepare(new["id"])
        return self.store.get(new["id"])

    def get_draft(self, pid: str) -> dict:
        job = self.store.get(pid)
        if job["kind"] != "preparation" or job["state"] != "ready":
            raise ApplicationError("conflict", "Черновик ещё не готов")
        package = self.load_operation(self.store, pid)
        if package.input_mode != "brief" or package.draft is None:
            raise ApplicationError("conflict", "Для этого пакета нет краткого брифа")
        try:
            assert_draft_matches_package(package)
        except ValueError as exc:
            raise ApplicationError("conflict", str(exc)) from exc
        if hash_draft(package.draft) != job.get("draft_hash"):
            raise ApplicationError("conflict", "Черновик изменился. Повторите подготовку")
        return {
            "package_id": pid,
            "package_hash": job["package_hash"],
            "draft_hash": job["draft_hash"],
            "draft": package.draft.model_dump(mode="json"),
            "approved": job.get("approved_draft_hash") == job.get("draft_hash")
            and job.get("approved_package_hash") == job.get("package_hash"),
        }

    def update_draft(self, pid: str, *, package_hash: str, draft: BriefDraft) -> dict:
        self.require_current_pipeline()
        job = self.store.get(pid)
        if job["kind"] != "preparation" or job["state"] != "ready":
            raise ApplicationError("conflict", "Пакет не готов к исправлению")
        if job.get("package_hash") != package_hash:
            raise ApplicationError("conflict", "Пакет изменился. Обновите черновик")
        package = self.load_operation(self.store, pid)
        if package.input_mode != "brief" or package.draft is None:
            raise ApplicationError("conflict", "Этот пакет не содержит краткий бриф")
        try:
            assert_draft_matches_package(package)
        except ValueError as exc:
            raise ApplicationError("conflict", str(exc)) from exc
        source_content = package.original_content or package.brief_evidence
        if source_content is None:
            raise ApplicationError("conflict", "Исходный текст брифа недоступен")
        try:
            allowed_fact_ids = {
                fid
                for slide in package.draft.slides
                for bullet in slide.bullets
                for fid in bullet.fact_ids
            }
            apply_edited_draft(source_content, draft, allowed_fact_ids=allowed_fact_ids)
            new, created = self.store.claim_draft_revision(
                pid, package_hash, draft.model_dump_json()
            )
        except ValueError as exc:
            raise ApplicationError("invalid", str(exc)) from exc
        if not created:
            return new
        directory = self.store.directory(new["id"])
        try:
            shutil.copyfile(self.store.directory(pid) / "input.pptx", directory / "input.pptx")
            shutil.copyfile(self.store.directory(pid) / "images.json", directory / "images.json")
            self._persist_request(
                new["id"],
                {
                    "text": "",
                    "audience": package.constraints.audience,
                    "instructions": package.constraints.instructions,
                    # Editing copy does not turn a size range into an exact count.
                    # The complete existing contract travels in base_constraints.
                    "slides": None,
                    "content_model": source_content.model_dump(),
                    "base_constraints": package.constraints.model_dump(),
                    "input_mode": "brief",
                    "draft": draft.model_dump(mode="json"),
                    "allowed_fact_ids": sorted(allowed_fact_ids),
                },
            )
        except Exception:
            self.store.update(new["id"], "failed", error="Не удалось сохранить исправленный бриф")
            raise
        self.runtime.prepare(new["id"])
        return self.store.get(new["id"])

    def approve_brief(self, pid: str, *, package_hash: str, draft_hash: str) -> dict:
        self.require_current_pipeline()
        try:
            package = self.load_operation(self.store, pid)
            assert_draft_matches_package(package)
            if package.draft is None or draft_hash != hash_draft(package.draft):
                raise ValueError("План или текст изменился. Откройте актуальную версию")
            job = self.store.approve_brief(pid, package_hash, draft_hash)
        except ValueError as exc:
            raise ApplicationError("conflict", str(exc)) from exc
        if job.get("auto_generation") == "scheduled":
            self.runtime.schedule_auto_generation(pid)
        return job

    def _review_snapshot(self, gid: str):
        from studio.checks.review_snapshot import load_snapshot, snapshot_hash

        job = self.store.get(gid)
        if job["kind"] != "generation" or not job.get("review_available"):
            raise ApplicationError("conflict", "Проверенный аудит недоступен")
        if job["state"] == "failed" and job.get("failure_kind") != "quality_gate":
            raise ApplicationError("conflict", "Черновик не прошёл завершённую проверку")
        if job["state"] not in ("completed", "needs_review", "failed"):
            raise ApplicationError("conflict", "Аудит ещё не готов")
        try:
            snapshot = load_snapshot(self.store, gid)
        except ValueError as exc:
            raise ApplicationError("conflict", str(exc)) from exc
        audit_hash = snapshot_hash(snapshot)
        if job.get("audit_hash") != audit_hash:
            raise ApplicationError("conflict", "Аудит изменился. Обновите результаты")
        return snapshot, audit_hash

    def findings(self, gid: str) -> dict:
        snapshot, audit_hash = self._review_snapshot(gid)
        return {"audit_hash": audit_hash, **snapshot.model_dump(mode="json")}

    def preview_path(self, gid: str, variant: str, slide: int) -> Path:
        snapshot, _ = self._review_snapshot(gid)
        if variant not in ("executive", "analytical", "story") or slide < 1 or slide > 30:
            raise ApplicationError("missing", "Предпросмотр не найден")
        name = f"{variant}/slide-{slide}.png"
        if name not in snapshot.files:
            raise ApplicationError("missing", "Предпросмотр не найден")
        path = public_path(self.store.directory(gid), name)
        if path is None:
            raise ApplicationError("missing", "Предпросмотр не найден")
        return path

    def repair(self, gid: str, *, audit_hash: str, finding_ids: list[str]) -> dict:
        self.require_current_pipeline()
        snapshot, current_hash = self._review_snapshot(gid)
        if current_hash != audit_hash:
            raise ApplicationError("conflict", "Аудит изменился. Обновите список замечаний")
        if len(finding_ids) != len(set(finding_ids)):
            raise ApplicationError("invalid", "Повторяющиеся замечания")
        findings = {finding.id: finding for finding in snapshot.findings}
        if any(fid not in findings for fid in finding_ids):
            raise ApplicationError("invalid", "Замечание отсутствует в этом аудите")
        if any(findings[fid].action is None for fid in finding_ids):
            raise ApplicationError(
                "invalid", "Для выбранного замечания нет безопасного исправления"
            )
        try:
            job, created = self.store.claim_repair(gid, audit_hash, finding_ids)
        except ValueError as exc:
            raise ApplicationError("conflict", str(exc)) from exc
        if created:
            deadline = (
                job["created"] + self.settings.deadline_seconds
                if self.settings.deadline_seconds is not None
                else None
            )
            self.store.update(job["id"], deadline_at=deadline)
            job = self.store.get(job["id"])
            self.runtime.supervise(job)
        return job

    async def cancel_job(self, jid: str) -> dict:
        job = self.store.get(jid)
        if job["kind"] not in ("template", "preparation", "generation"):
            raise ApplicationError("invalid", "Этот тип задания нельзя отменить")
        try:
            return await self.runtime.cancel(jid)
        except ValueError as exc:
            raise ApplicationError("conflict", str(exc)) from exc

    def diagnostics(self, jid: str, *, after: int = -1, download: bool = False) -> dict:
        job = self.store.get(jid)
        attachments = {}
        for name in (
            "failure-report.json",
            "pptagent-private.log",
            "background-model.json",
            "text-zones.json",
        ):
            path = self.store.directory(jid) / name
            if path.is_file():
                with path.open("rb") as source:
                    source.seek(max(0, path.stat().st_size - 64000))
                    attachments[name] = redact(source.read(64000).decode("utf-8", "replace"))
        checks = {
            key: job[key]
            for key in (
                "diagnostics",
                "warnings",
                "quality_report",
                "contextual_audit",
                "visual_audit",
                "refinement",
                "composition_diversity",
                "font_substitutions",
                "missing_fonts",
                "auto_error",
            )
            if key in job
        }
        checks["variants"] = [
            {
                key: variant[key]
                for key in ("key", "title", "findings", "repairs", "audit_scope")
                if key in variant
            }
            for variant in job.get("variants", [])
        ]
        checks = json.loads(redact(json.dumps(checks, ensure_ascii=False)))
        return {
            "job_id": jid,
            "state": job["state"],
            "phase": job.get("phase"),
            "error": redact(job.get("error", "")),
            "events": self.store.events(
                jid,
                max(0, after),
                10000 if download else 500,
                latest=after < 0 and not download,
            ),
            "checks": checks,
            "attachments": attachments,
            "retention": "Последние 10 000 событий; файлы — последние 64 КБ. Ключи скрыты, запросы и ответы модели целиком не записываются.",
        }

    def artifact_path(self, jid: str, filename: str) -> Path:
        job = self.store.get(jid)
        if job["state"] not in ("ready", "completed", "needs_review", "waiting_fonts"):
            raise ApplicationError("conflict", "Артефакты ещё не готовы")
        allowed = {
            "manifest.json",
            "final-audit.json",
            "evidence.html",
            "presentations.zip",
            "plans.json",
            "DESIGN.md",
            "tokens.json",
            "opendesign.json",
            "analysis.json",
            "font-model.json",
            "layout-font-model.json",
            "color-model.json",
            "background-model.json",
            "text-zones.json",
        }
        if job["state"] == "waiting_fonts" and filename not in {
            "font-model.json",
            "layout-font-model.json",
        }:
            raise ApplicationError("conflict", "Доступен только отчёт о шрифтах")
        variants = ("executive", "analytical", "story")
        allowed |= {
            f"{variant}/{name}"
            for variant in variants
            for name in ("deck.pptx", "deck.pdf", "deck.html", "slides.json", "FONT_LICENSES.txt")
        }
        allowed |= {f"{variant}/slide-{i}.png" for variant in variants for i in range(1, 31)}
        path = public_path(self.store.directory(jid), filename) if filename in allowed else None
        if path is None:
            raise ApplicationError("missing", "Файл не найден")
        return path
