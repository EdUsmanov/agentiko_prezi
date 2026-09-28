"""Historical and current analysis reports through the real HTTP API and DOM."""

from playwright.sync_api import expect
from studio.store import Store
from test_support.app_server import application


def test_archetype_reports_in_saved_packages(browser_page, tmp_path):
    page = browser_page
    groups = [
        {"title": "Задача", "purpose": "problem", "fact_ids": ["f1"]},
        {"title": "Решение", "purpose": "solution", "fact_ids": ["f2"]},
    ]
    editorial = {"status": "completed", "method": "reviewed_editorial_groups", "units": []}
    cases = [
        (
            "current-editorial",
            {"archetypes": {**editorial, "reviewed_groups": groups}},
            "Слайдов с определённым назначением в редакторском плане: 2",
        ),
        (
            "legacy-editorial",
            {"archetypes": editorial, "narrative": {"groups": groups}},
            "Слайдов с определённым назначением в редакторском плане: 2",
        ),
        (
            "classifier",
            {"archetypes": {"status": "completed", "units": groups}},
            "Смысловых блоков проверено по каталогу: 2",
        ),
        (
            "empty-classifier",
            {"archetypes": {"status": "completed", "units": []}},
            "Нет данных о проверенных смысловых блоках",
        ),
        (
            "missing-units",
            {"archetypes": {"status": "completed"}},
            "Нет данных о проверенных смысловых блоках",
        ),
        (
            "empty-editorial",
            {"archetypes": {**editorial, "reviewed_groups": []}, "narrative": {"groups": groups}},
            "Нет данных о назначении слайдов",
        ),
        (
            "degraded",
            {"archetypes": {"status": "degraded"}},
            "частично: неподтверждённые блоки сохранены как обычный текст",
        ),
        ("not-run", {}, "не определялись — нужен новый анализ в режиме LLM"),
    ]
    with application(tmp_path / "app") as (url, _, settings):
        store = Store(settings.data_dir)
        for name, analysis, _ in cases:
            job = store.create("preparation", {"template_name": name})
            store.update(
                job["id"],
                state="ready",
                analysis=analysis,
                template={
                    "name": name,
                    "font": "Play",
                    "colors": ["#FFFFFF"],
                    "slide_count": 2,
                    "layout_count": 1,
                    "patterns": [],
                },
                content={"facts": 2, "tables": 0},
                constraints={"slides": 2},
                auto_generation="disabled",
            )
        page.goto(url)
        page.locator("#nav-history").click()
        for name, _, message in cases:
            row = page.locator(".history-item").filter(has=page.get_by_text(name, exact=True))
            row.get_by_role("button", name="Использовать пакет").click()
            expect(
                page.locator(".analysis-checks dt")
                .filter(has_text="Архетипы содержания")
                .locator("xpath=following-sibling::dd[1]")
            ).to_have_text(message)
        assert len(page.request.get(url + "/api/jobs").json()) == len(cases)
