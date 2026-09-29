"""Offline, source-backed overview of one published generation."""

import base64
from collections import Counter
from html import escape
import json
from pathlib import Path

from studio.composition.artifacts import public_path


def write_evidence(directory: Path, manifest: dict, package=None) -> Path:
    directory = Path(directory)
    report = manifest["quality_report"]
    variants = manifest.get("variants", [])
    cards = []
    rows = []
    for variant in variants:
        key = variant["key"]
        scenes = public_path(directory, f"{key}/slides.json")
        if scenes is None:
            raise ValueError("Для доказательного отчёта отсутствует опубликованный слайд")
        slide_data = json.loads(scenes.read_text())
        representative = next(
            (
                i
                for i, scene in enumerate(slide_data, 1)
                if scene.get("purpose") not in ("cover", "divider")
            ),
            1,
        )
        preview = public_path(directory, f"{key}/slide-{representative}.png")
        if preview is None:
            raise ValueError("Для доказательного отчёта отсутствует опубликованный слайд")
        data = base64.b64encode(preview.read_bytes()).decode("ascii")
        cards.append(
            f'<article><img alt="Слайд {representative} варианта {escape(variant["title"])}" '
            f'src="data:image/png;base64,{data}"><h3>{escape(variant["title"])}</h3>'
            f"<p>Представлен содержательный слайд {representative} из {len(slide_data)}. "
            f"<code>{escape(key)}.pptx</code> в корне ZIP.</p></article>"
        )
        for number, scene in enumerate(slide_data, 1):
            kinds = Counter(element["kind"] for element in scene.get("elements", []))
            rows.append(
                "<tr>"
                f"<td>{escape(key)}</td><td>{number}</td><td>{escape(scene.get('title', ''))}</td>"
                f"<td>{escape(scene.get('pattern_id') or 'token:auto')}</td>"
                f"<td>{escape(', '.join(scene.get('source_ids', [])))}</td>"
                f"<td>{escape(', '.join(f'{kind}: {count}' for kind, count in sorted(kinds.items())))}</td>"
                "</tr>"
            )
    findings = Counter(item.get("source", "unknown") for item in report.get("findings", []))
    checks = manifest.get("checks", {})
    elapsed = manifest.get("elapsed_seconds")
    elapsed_label = f"{elapsed:.1f} с" if isinstance(elapsed, (float, int)) else "не измерено"
    audited = (
        " · ".join(f"{escape(k)}: {v}" for k, v in sorted(findings.items())) or "замечаний нет"
    )
    matrix = [
        (
            "Три варианта одного материала",
            "подтверждено" if len(variants) == 3 else "не подтверждено",
        ),
        (
            "Нативный редактируемый PPTX",
            "PPTX повторно открыт" if checks.get("native_pptx_reopened") else "не подтверждено",
        ),
        (
            "PDF и HTML",
            "сформированы"
            if checks.get("pdf_pages") and checks.get("html_live_dom")
            else "не подтверждено",
        ),
        (
            "Рендер готового PPTX",
            "подтверждён" if checks.get("native_pptx_render") else "не подтверждён",
        ),
        ("Одна колода до 5 минут", "измерьте по каждой колоде; текущий job: " + elapsed_label),
        ("Три разных шаблона", "требует сравнения трёх отдельных запусков"),
        ("Генерация изображений", "не реализована"),
    ]
    matrix_rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>" for label, value in matrix
    )
    comparison = report.get("repair_comparison")
    revision = (
        f"<p>Ревизия после выбора пользователя: выбранных замечаний не обнаружено {len(comparison.get('selected_not_observed', []))}, "
        f"всё ещё обнаружено {len(comparison.get('selected_still_present', []))}, новых {len(comparison.get('new', []))}. "
        "Отсутствие прежнего кода при повторном аудите не гарантирует, что причина устранена.</p>"
        if isinstance(comparison, dict)
        else "<p>Выборочный ремонт для этого запуска не применялся.</p>"
    )
    profile = package.template if package is not None else None
    palette = ", ".join(profile.colors) if profile else "нет данных в данном отчёте"
    fonts = ", ".join(profile.fonts) if profile else "нет данных в данном отчёте"
    template_info = (
        f"Шаблон: {escape(profile.name)}; SHA-256 {escape(profile.sha256)}; "
        f"разобрано {profile.slide_count} исходных слайдов и {len(profile.patterns)} паттернов."
        if profile
        else "Сведения о шаблоне не включены в этот автономный отчёт."
    )
    source_counts = Counter(f.source for f in package.content.facts) if package else Counter()
    source_rows = (
        "".join(
            f"<tr><th>{escape(source)}</th><td>{count}</td></tr>"
            for source, count in sorted(source_counts.items())
        )
        or "<tr><td colspan='2'>Нет данных</td></tr>"
    )
    versions = manifest.get("generation_versions", {})
    pinned = {k: v for k, v in versions.items() if k.startswith(("prompts/", "config/"))}
    mode = manifest.get("run_provenance", {})
    provenance = escape(json.dumps(mode, ensure_ascii=False, sort_keys=True))
    timings = escape(
        json.dumps(manifest.get("timings", {}), ensure_ascii=False, sort_keys=True, indent=2)
    )
    source_manifest = manifest.get("input_manifest", {})
    source_provenance = escape(
        json.dumps(
            {
                key: source_manifest[key]
                for key in (
                    "template_sha256",
                    "content_sha256",
                    "constraints_sha256",
                    "uploaded_images",
                    "brief_revision_provenance",
                    "submitted_draft_hash",
                    "opendesign",
                )
                if key in source_manifest
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    version_provenance = escape(json.dumps(versions, ensure_ascii=False, sort_keys=True, indent=2))
    resources = manifest.get("resource_usage", {})
    reused = resources.get("used", [])
    fallbacks = resources.get("device_fallbacks", [])
    diversity_label = (
        "не применяется для одного варианта"
        if len(variants) == 1
        else str(manifest.get("composition_diversity", {}).get("verified", "не проверено"))
    )
    html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Доказательства генерации {escape(manifest["run_id"])}</title><style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:1200px;margin:auto;padding:24px;color:#172333;background:#f6f8fb}}h1,h2{{line-height:1.2}}section,article{{background:white;border:1px solid #dce2ea;border-radius:10px;padding:18px;margin:14px 0}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}article img{{width:100%;height:auto}}table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;border:1px solid #dce2ea;text-align:left;vertical-align:top}}.scroll{{overflow:auto}}code{{font-size:.9em}}small{{color:#556}}
</style></head><body><h1>Доказательства генерации</h1><p>Запуск <code>{escape(manifest["run_id"])}</code>. Данные взяты из финального манифеста и опубликованных сцен этого запуска; изображения встроены в HTML. Время выполнения задания: {elapsed_label}.</p>
<section><h2>Результаты</h2><div class="cards">{"".join(cards)}</div><p>Проверка различий вариантов: {escape(diversity_label)}. Содержание и стиль сверяйте по строкам ниже.</p></section>
<section><h2>Исходный шаблон и дизайн-система</h2><p>{template_info}</p><p>Палитра: {escape(palette)}. Шрифты: {escape(fonts)}.</p><p>Ресурсы шаблона: {len(profile.resources) if profile else "неизвестно"}; размещено {len(reused)}; скриншотов оставлено без рамки {len(fallbacks)}. Каждый выбранный ресурс связан со слайдом исходного PPTX. Файлы оригинала не встроены в этот отчёт.</p></section>
<section><h2>Происхождение материала и исполнения</h2><p>Режим ввода: {escape(package.input_mode if package else "неизвестно")}. Тезисов с пометкой предложения модели в утверждённом черновике: {manifest.get("model_proposal_count", 0)}. Типы итоговых фактов:</p><table>{source_rows}</table><p>Исполнение: <code>{provenance}</code>. Идентификатор модели: {escape(str(manifest.get("model", {}).get("id") or "не указан"))}. Git commit: <code>{escape(manifest.get("git_commit", "не указан"))}</code>.</p><details><summary>Версии исходного кода, промптов и конфигурации ({len(versions)}, из них {len(pinned)} промптов/настроек)</summary><pre>{version_provenance}</pre></details><details><summary>Входной манифест</summary><pre>{source_provenance}</pre></details><details><summary>Тайминги этапов и модели</summary><pre>{timings}</pre></details></section>
<section><h2>Проверки и исправления</h2><p>Итог: {escape(report.get("status", ""))}; ошибок {report.get("errors", 0)}, предупреждений {report.get("warnings", 0)}. Источники замечаний: {audited}.</p><p>Контекстуальная проверка: {escape(manifest.get("contextual_audit", {}).get("status", "неизвестно"))}; визуальная: {escape(manifest.get("visual_audit", {}).get("status", "неизвестно"))}. OCR: не выполнялся.</p>{revision}</section>
<section><h2>Матрица требований</h2><table>{matrix_rows}</table><small>Статус этого запуска не доказывает работу на другом неизвестном шаблоне или в браузерах жюри.</small></section>
<section><h2>След фактов и макетов</h2><div class="scroll"><table><thead><tr><th>Вариант</th><th>Слайд</th><th>Заголовок</th><th>Паттерн</th><th>ID фактов</th><th>Объекты</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section></body></html>"""
    path = directory / "evidence.html"
    path.write_text(html)
    return path
