"""UI-only contracts with canned API responses; no worker or model is contacted."""

import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect


WEB = Path(__file__).resolve().parents[1] / "web"
ORIGIN = "http://127.0.0.1:8765"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg=="
)


def _page_with_api(page, api):
    def route(request):
        path = urlsplit(request.request.url).path
        if path == "/":
            return request.fulfill(path=WEB / "index.html", content_type="text/html")
        if path.startswith("/static/"):
            source = (WEB / path.removeprefix("/static/")).resolve()
            if source.is_relative_to(WEB) and source.is_file():
                return request.fulfill(path=source)
            return request.fulfill(status=404)
        if "/preview/" in path or path.endswith(".png"):
            return request.fulfill(body=PNG, content_type="image/png")
        status, value = api(request.request.method, path, request.request.post_data_json)
        return request.fulfill(
            status=status,
            body=json.dumps(value, ensure_ascii=False),
            content_type="application/json",
        )

    page.route(ORIGIN + "/**", route)
    page.goto(ORIGIN)
    expect(page.locator("#model-mode")).to_have_text("Автономно · без LLM")


def _prepared(pid, *, approved=False):
    return {
        "id": pid,
        "kind": "preparation",
        "state": "ready",
        "created": 1,
        "input_mode": "brief",
        "template_name": "brand.pptx",
        "template": {
            "name": "brand.pptx",
            "colors": [],
            "slide_count": 3,
            "patterns": [{}],
            "layout_count": 1,
            "font": "Play",
            "font_origin": {"kind": "local"},
            "width": 960,
            "height": 540,
        },
        "content": {"facts": 1, "tables": 0, "images": 0},
        "constraints": {"slides": 3},
        "analysis": {"planned_slides": 3},
        "auto_generation": "scheduled" if approved else "needs_confirmation",
        "auto_generate_at": 4102444800,
    }


def _draft(pid, *, approved=False, edited=False):
    return {
        "package_id": pid,
        "package_hash": ("b" if edited else "a") * 64,
        "draft_hash": ("d" if edited else "c") * 64,
        "approved": approved,
        "draft": {
            "schema_version": 1,
            "slides": [
                {
                    "title": "План",
                    "purpose": "content",
                    "bullets": [
                        {
                            "text": "Новый тезис" if edited else "Исходный тезис",
                            "fact_ids": ["f1"],
                            "proposed": False,
                        }
                    ],
                }
            ],
        },
    }


def test_brief_edit_and_approval_bind_both_hashes(browser_page):
    page = browser_page
    first, second = "a" * 32, "b" * 32
    jobs = {first: _prepared(first), second: _prepared(second)}
    drafts = {first: _draft(first), second: _draft(second, edited=True)}
    posts = []

    def api(method, path, body):
        if path == "/api/health":
            return 200, {"model_mode": "extractive", "engine": "native"}
        if path == "/api/runtime":
            return 200, {"restart_required": False}
        if path == "/api/references":
            return 200, []
        if path == "/api/jobs":
            return 200, [jobs[first]]
        if path.startswith("/api/jobs/"):
            return 200, jobs[path.rsplit("/", 1)[1]]
        if path.endswith("/draft") and method == "GET":
            return 200, drafts[path.split("/")[3]]
        if method == "POST":
            posts.append((path, body))
            if path == f"/api/packages/{first}/draft":
                return 202, {"id": second, "state": "accepted"}
            if path == f"/api/packages/{second}/approve":
                jobs[second] = _prepared(second, approved=True)
                drafts[second]["approved"] = True
                return 200, jobs[second]
            if path == f"/api/packages/{second}/auto-generation/cancel":
                jobs[second] = {**jobs[second], "auto_generation": "cancelled"}
                return 200, jobs[second]
        raise AssertionError(f"Unexpected UI request: {method} {path}")

    _page_with_api(page, api)
    expect(page.locator("#generate-button")).to_be_disabled()
    page.locator("#nav-history").click()
    page.get_by_role("button", name="Использовать пакет").click()
    expect(page.locator(".brief-approval")).to_be_visible()
    page.get_by_label("Тезис 1 слайда 1").fill("Новый тезис")
    expect(page.locator("#generate-button")).to_be_disabled()
    page.get_by_role("button", name="Сохранить изменения в новом черновике").click()
    expect(page.locator(".brief-approval")).to_be_visible()
    assert posts[0] == (
        f"/api/packages/{first}/draft",
        {
            "package_hash": "a" * 64,
            "draft": {
                "schema_version": 1,
                "slides": [
                    {
                        "title": "План",
                        "purpose": "content",
                        "bullets": [
                            {
                                "text": "Новый тезис",
                                "fact_ids": ["f1"],
                                "proposed": False,
                            }
                        ],
                    }
                ],
            },
        },
    )
    expect(page.locator("#generate-button")).to_be_disabled()
    page.get_by_role("button", name="Утвердить план и текст", exact=True).click()
    expect(page.locator("#generate-button")).to_be_enabled()
    assert (
        f"/api/packages/{second}/approve",
        {
            "package_hash": "b" * 64,
            "draft_hash": "d" * 64,
        },
    ) in posts
    cancel = page.locator("#cancel-auto-generation")
    expect(cancel).to_be_visible()
    cancel.click()
    assert (f"/api/packages/{second}/auto-generation/cancel", None) in posts
    expect(page.locator("#generate-button")).to_be_enabled()


def test_quality_failed_draft_has_preview_and_selected_repair(browser_page):
    page = browser_page
    pid, source, child = "a" * 32, "b" * 32, "c" * 32
    source_job = {
        "id": source,
        "kind": "generation",
        "state": "failed",
        "created": 2,
        "package_id": pid,
        "review_available": True,
        "failure_kind": "quality_gate",
        "error": "Quality gate rejected output",
    }
    child_job = {
        "id": child,
        "kind": "generation",
        "state": "needs_review",
        "created": 3,
        "package_id": pid,
        "analysis_seconds": 1,
        "elapsed_seconds": 2,
        "font_substitutions": [],
        "model_mode": "extractive",
        "variants": [
            {"key": key, "title": key, "slides": 1} for key in ("executive", "analytical", "story")
        ],
    }
    audit_hash = "f" * 64
    source_audit = {
        "audit_hash": audit_hash,
        "generation_id": source,
        "package_id": pid,
        "files": {"executive/slide-1.png": "saved"},
        "findings": [
            {
                "id": "layout-1",
                "source": "variant",
                "variant": "executive",
                "slide": 1,
                "code": "overlap",
                "severity": "error",
                "message": "Текст перекрыт",
                "action": "change_layout",
                "repaired": False,
            },
            {
                "id": "meaning-2",
                "source": "contextual_audit",
                "variant": None,
                "slide": None,
                "code": "content",
                "severity": "warning",
                "message": "Проверьте смысл",
                "action": None,
                "unsupported_reason": "Нужна правка содержания",
                "repaired": False,
            },
        ],
        "quality_report": {"status": "blocked"},
    }
    child_audit = {
        "audit_hash": "e" * 64,
        "generation_id": child,
        "package_id": pid,
        "parent_generation_id": source,
        "files": {"executive/slide-1.png": "saved2"},
        "findings": [],
        "quality_report": {
            "repair_comparison": {
                "selected_not_observed": ["layout-1"],
                "selected_still_present": [],
                "new": [],
            }
        },
    }
    posts = []

    def api(method, path, body):
        if path == "/api/health":
            return 200, {"model_mode": "extractive", "engine": "native"}
        if path == "/api/runtime":
            return 200, {"restart_required": False}
        if path == "/api/references":
            return 200, []
        if path == "/api/jobs":
            return 200, [source_job]
        if path == f"/api/jobs/{pid}":
            return 200, {"id": pid, "template": {"width": 960, "height": 540}}
        if path == f"/api/jobs/{child}":
            return 200, child_job
        if path == f"/api/generations/{source}/findings":
            return 200, source_audit
        if path == f"/api/generations/{child}/findings":
            return 200, child_audit
        if method == "POST" and path == f"/api/generations/{source}/repair":
            posts.append(body)
            return 202, {"id": child, "state": "accepted"}
        raise AssertionError(f"Unexpected UI request: {method} {path}")

    _page_with_api(page, api)
    page.locator("#nav-history").click()
    page.get_by_role("button", name="Аудит черновика").click()
    expect(page.locator("#results")).to_be_visible()
    expect(page.locator("#download-all")).to_be_hidden()
    expect(page.locator("#manifest-link")).to_be_hidden()
    expect(page.locator("#result-grid")).to_be_hidden()
    expect(page.locator("#audit-list img")).to_have_count(1)
    assert page.locator("#audit-list img").first.get_attribute("src") == (
        f"/api/generations/{source}/preview/executive/1"
    )
    assert page.locator("#audit-list input[type=checkbox]").count() == 1
    assert (
        page.locator("#audit-list input[type=checkbox]").first.get_attribute("value") == "layout-1"
    )
    page.locator("#audit-list input[type=checkbox]").check()
    page.get_by_role("button", name="Исправить выбранное").click()
    expect(page.locator("#result-summary")).to_contain_text("Требуется проверка")
    assert posts == [{"audit_hash": audit_hash, "finding_ids": ["layout-1"]}]
    assert page.locator("#download-all").get_attribute("href") == (
        f"/api/jobs/{child}/files/presentations.zip"
    )
    expect(page.locator("#audit-list")).to_contain_text("Выбранные замечания: не обнаружено 1")
