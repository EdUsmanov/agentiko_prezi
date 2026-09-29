"""Saved-design preview and a real manual single-deck UI flow without external models."""

import subprocess
import sys
from zipfile import ZipFile
from playwright.sync_api import expect
from studio.examples import index_examples
from studio.cache_version import atomic_json
from test_support.app_server import application
from test_support.inputs import make_template, BRIEF


def test_saved_design_selection_and_manual_single_generation(browser_page, tmp_path):
    page = browser_page
    first = make_template(tmp_path / "First.pptx")
    second = make_template(tmp_path / "Second.potx", dark=True)
    with application(tmp_path / "app") as (url, _, settings):
        rows = index_examples([first, second], settings)
        rows[1]["variant_count"] = 1
        atomic_json(settings.data_dir / "references/index.json", rows)
        # The real template-only service runs in its own event loop, outside Playwright's.
        subprocess.run(
            [
                sys.executable,
                "-c",
                """
import asyncio, sys
from pathlib import Path
from studio.config import Settings
from studio.reference_analysis import preanalyze_reference
async def main():
    for rid in sys.argv[2:]:
        await preanalyze_reference(Settings(data_dir=Path(sys.argv[1])), rid)
asyncio.run(main())
""",
                str(settings.data_dir),
                *[r["id"] for r in rows],
            ],
            check=True,
            timeout=180,
        )
        page.goto(url)
        expect(page.locator("#reference option")).to_have_count(3)
        page.locator("#reference").select_option(rows[0]["id"])
        expect(page.locator("#saved-design")).to_contain_text("Технический профиль готов")
        expect(page.locator("#generate-button")).to_be_disabled()
        assert page.request.get(url + "/api/jobs").json() == []
        page.locator("#reference").select_option(rows[1]["id"])
        expect(page.locator("#saved-design")).to_contain_text("Second.potx")
        expect(page.locator('input[name="variant-count"][value="1"]')).to_be_checked()
        page.locator("#content").fill(BRIEF)
        expect(page.locator("#saved-design")).to_contain_text("Second.potx")
        page.locator("#extra-settings > summary").click()
        page.locator("#slides").select_option("mini")
        page.locator("#instructions").fill("Ровно 3 слайда")
        with page.expect_response(
            lambda r: r.url.endswith("/api/prepare") and r.request.method == "POST"
        ) as response:
            page.locator("#prepare-button").click()
        pid = response.value.json()["id"]
        expect(page.locator("#generate-button")).to_be_enabled(timeout=120_000)
        expect(page.locator(".analysis-variant-card:visible")).to_have_count(1)
        saved = page.request.get(url + "/api/jobs/" + pid).json()
        assert saved["auto_generation"] == "manual" and "auto_generate_at" not in saved
        assert saved["analysis"]["template_cache"]["hit"] is True
        assert len(page.request.get(url + "/api/jobs").json()) == 1
        with page.expect_response(
            lambda r: r.url.endswith("/api/generate") and r.request.method == "POST"
        ) as response:
            page.locator("#generate-button").click()
        request = response.value.request.post_data_json
        assert request["variant_count"] == 1
        jid = response.value.json()["id"]
        page.wait_for_function(
            "() => !document.querySelector('#results').hidden || !!document.querySelector('#generation-error')",
            timeout=180_000,
        )
        expect(page.locator("#results")).to_be_visible()
        expect(page.locator(".result-card")).to_have_count(1)
        expect(page.locator("#result-timings")).to_contain_text("Генерация одной презентации")
        done = page.request.get(url + "/api/jobs/" + jid).json()
        assert done["errors"] == 0 and len(done["variants"]) == 1
        assert done["deadline_at"] - done["created"] == 300
        with ZipFile(settings.data_dir / "jobs" / jid / "presentations.zip") as z:
            assert len([name for name in z.namelist() if name.endswith(".pptx")]) == 1
        page.locator("#new-presentation").click()
        page.locator("#reference").select_option("")
        expect(page.locator("#saved-design")).to_be_hidden()
        expect(page.locator('input[name="variant-count"][value="3"]')).to_be_checked()
        page.locator("#template").set_input_files(first)
        expect(page.locator("#profile-empty")).to_be_visible()
        expect(page.locator("#prep-state")).to_have_text("Ожидает анализа")
        expect(page.locator("#generate-button")).to_be_disabled()
