"""Browser suite is explicit: python -m pytest -q e2e (Chromium required)."""

from pathlib import Path
import shutil
import hashlib
import re
from urllib.parse import urlsplit
import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture
def browser():
    # Release Playwright's loop before a separate E2E runner starts its own.
    with sync_playwright() as runner:
        browser = runner.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def browser_page(browser, request, tmp_path):
    label = re.sub(r"[^a-zA-Z0-9_.-]+", "_", request.node.name)[:100]
    suffix = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:10]
    artifacts = Path("test-results/browser") / (label + "-" + suffix)
    artifacts.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(accept_downloads=True, viewport={"width": 1440, "height": 1050})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    page.set_default_timeout(30_000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def guard(route):
        host = urlsplit(route.request.url).hostname
        if host not in ("127.0.0.1", "localhost", None):
            errors.append("External browser request blocked: " + host)
            route.abort()
        else:
            route.continue_()

    context.route("**/*", guard)
    yield page
    # Store artifacts even on failure; CI uploads them instead of hiding a flaky retry.
    try:
        page.screenshot(path=str(artifacts / "page.png"), full_page=True)
        context.tracing.stop(path=str(artifacts / "trace.zip"))
    finally:
        context.close()
        app = tmp_path / "app"
        for name in ("server.log", "replay-report.json"):
            if (app / name).is_file():
                shutil.copy2(app / name, artifacts / name)
        for diagnostic in app.glob("*-diagnostics.json"):
            shutil.copy2(diagnostic, artifacts / diagnostic.name)
    assert not errors, errors
