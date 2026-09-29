"""Execute one evaluation case against isolated app data and bounded providers."""

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import re
import signal
import threading
import time

from .live_provider import LiveProvider
from .reporting import write_json
from test_support.app_server import application


class EvaluationDeadline(BaseException):
    """Raised by the parent wall-clock deadline so stage handlers cannot swallow it."""


@contextmanager
def _hard_deadline(seconds):
    if seconds <= 0:
        raise EvaluationDeadline("Evaluation wall-clock deadline expired")
    if threading.current_thread() is not threading.main_thread() or not hasattr(
        signal, "setitimer"
    ):
        raise RuntimeError("Evaluation runtime requires a main-thread POSIX wall-clock timer")
    old_handler = signal.getsignal(signal.SIGALRM)
    old_delay, old_interval = signal.getitimer(signal.ITIMER_REAL)
    if old_delay:
        raise RuntimeError("Cannot nest the evaluation deadline inside another alarm")

    def expired(_signum, _frame):
        raise EvaluationDeadline("Evaluation exceeded its total wall-clock limit")

    started = time.monotonic()
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, float(seconds)))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_delay:
            left = max(0.001, old_delay - (time.monotonic() - started))
            signal.setitimer(signal.ITIMER_REAL, left, old_interval)


def _safe_error(exc, secret=""):
    message = str(exc)
    if secret:
        message = message.replace(secret, "[redacted]")
    message = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", message)
    return {"type": type(exc).__name__, "message": message[:2000]}


def _path(value):
    return Path(value).expanduser().resolve(strict=True)


def _validate_case(case):
    if not isinstance(case, dict) or not case.get("id"):
        raise ValueError("A materialized evaluation case requires an id")
    if case.get("input_mode") not in ("content", "brief"):
        raise ValueError("Unknown evaluation input mode")
    if case.get("interface", "http") not in ("http", "browser"):
        raise ValueError("Unknown evaluation interface")
    if not isinstance(case.get("content"), str) or not case["content"].strip():
        raise ValueError("Evaluation case content must be nonempty text")
    slides = int(case.get("slides", 5))
    if not 1 <= slides <= 30:
        raise ValueError("Evaluation slide count must be between 1 and 30")
    template = _path(case["template"])
    if template.suffix.casefold() not in (".pptx", ".potx") or not template.is_file():
        raise ValueError("Evaluation template must be an existing PPTX or POTX")
    images = [_path(path) for path in case.get("images", [])]
    if any(not path.is_file() for path in images):
        raise ValueError("Evaluation images must be existing files")
    expected_images = case.get("images_source", [])
    if expected_images and len(expected_images) != len(images):
        raise ValueError("Materialized image list differs from the registered source list")
    return template, images, slides


def _canonical_images(case, images, directory):
    """Use the source filenames embedded in Markdown even for synthetic analogs."""
    expected = case.get("images_source", [])
    if not expected:
        return images
    root = Path(directory) / "normalized-input-images"
    root.mkdir(parents=True, exist_ok=True)
    normalized = []
    for source, item in zip(images, expected, strict=True):
        name = item.get("file")
        if not name or Path(name).name != name:
            raise ValueError("Registered image name must be a basename")
        target = root / name
        if source.name == name:
            normalized.append(source)
        else:
            target.write_bytes(source.read_bytes())
            normalized.append(target)
    return normalized


def _run_http(case, directory, url, *, mode, timeout, template, images, slides):
    from scripts.check_external_templates import run

    return run(
        template,
        directory,
        url=url,
        timeout=max(1, min(float(timeout), 600)),
        poll=0.2,
        content=case["content"],
        images=images,
        image_presentation=case.get("image_presentation", "plain"),
        input_mode=case["input_mode"],
        slides=slides,
        allow_model_calls=mode in ("replay", "live"),
    )


def _run_browser(case, directory, url, *, timeout, template, images, slides):
    from io import BytesIO
    import httpx
    from playwright.sync_api import expect, sync_playwright
    from pptx import Presentation
    from scripts.check_external_templates import request_json, save_json, verify_artifacts, wait_job

    deadline = time.monotonic() + timeout

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise EvaluationDeadline("Browser evaluation exceeded its wall-clock limit")
        return value

    request_log = []
    response_log = []
    selected_repair = bool(case.get("selected_repair"))
    directory.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = None
        page = None
        tracing = False
        try:
            browser_version = browser.version
            save_json(directory / "browser-environment.json", {"chromium_version": browser_version})
            context = browser.new_context(accept_downloads=True)
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            tracing = True
            page = context.new_page()
            page.set_default_timeout(max(1000, int(remaining() * 1000)))

            def observe(request):
                if request.method != "POST":
                    return
                if not request.url.startswith(url):
                    return
                if request.url.endswith("/api/generate") or request.url.endswith("/repair"):
                    try:
                        body = request.post_data_json
                    except (ValueError, TypeError):
                        body = None
                    request_log.append({"url": request.url, "body": body})

            def observe_response(response):
                request = response.request
                if (
                    request.method == "POST"
                    and request.url.startswith(url)
                    and request.url.endswith("/api/generate")
                ):
                    try:
                        body = response.json()
                    except (ValueError, TypeError):
                        body = {}
                    response_log.append(
                        {"status": response.status, "body": body, "url": request.url}
                    )

            page.on("request", observe)
            page.on("response", observe_response)
            page.goto(
                url, wait_until="domcontentloaded", timeout=max(1000, int(remaining() * 1000))
            )
            page.locator("#template").set_input_files(str(template))
            page.locator("#input-mode").select_option(case["input_mode"])
            page.locator("#content").fill(case["content"])
            page.locator("#slides").select_option(
                "mini" if slides <= 5 else "standard" if slides <= 10 else "large"
            )
            if images:
                page.locator("#images").set_input_files([str(path) for path in images])
            with page.expect_response(
                lambda response: (
                    response.url.endswith("/api/prepare") and response.request.method == "POST"
                ),
                timeout=max(1000, int(remaining() * 1000)),
            ) as prepared_response:
                page.locator("#prepare-button").click()
            if prepared_response.value.status != 202:
                raise RuntimeError(
                    "UI preparation returned HTTP "
                    f"{prepared_response.value.status}: "
                    f"{_safe_error(RuntimeError(prepared_response.value.text()))['message']}"
                )
            package_id = prepared_response.value.json()["id"]
            with httpx.Client(trust_env=False, timeout=30) as client:
                prepared = wait_job(
                    client,
                    url,
                    package_id,
                    "preparation",
                    directory,
                    remaining(),
                    0.2,
                )
                save_json(directory / "preparation.json", prepared)
                if prepared["state"] != "ready":
                    raise RuntimeError(
                        f"Preparation ended as {prepared['state']}: {prepared.get('error')}"
                    )

                if case["input_mode"] == "brief":
                    expect(page.locator(".brief-approval")).to_be_visible(
                        timeout=max(1000, int(remaining() * 1000))
                    )
                    if not page.locator("#generate-button").is_disabled():
                        raise RuntimeError("Brief generation was enabled before UI approval")
                    draft_response = client.get(f"{url}/api/packages/{package_id}/draft")
                    draft_response.raise_for_status()
                    draft = draft_response.json()
                    if draft.get("approved") or draft.get("package_id") != package_id:
                        raise RuntimeError(
                            "Brief draft is not an unapproved draft for this package"
                        )
                    if draft.get("package_hash") != prepared.get("package_hash") or draft.get(
                        "draft_hash"
                    ) != prepared.get("draft_hash"):
                        raise RuntimeError("GET draft hashes differ from the prepared package")
                    save_json(directory / "draft.json", draft)
                    budget = (prepared.get("control", {}).get("slide_budget") or {}).get("status")
                    accept = budget == "adjusted" or bool(
                        prepared.get("constraints", {}).get("confirm_plan")
                    )
                    blocked = client.post(
                        f"{url}/api/generate",
                        json={
                            "package_id": package_id,
                            "accept_adjusted_slide_count": accept,
                        },
                    )
                    save_json(
                        directory / "preapproval-generation.json",
                        {
                            "status": blocked.status_code,
                            "body": blocked.json()
                            if "application/json" in blocked.headers.get("content-type", "")
                            else blocked.text[:2000],
                        },
                    )
                    if blocked.status_code != 409:
                        raise RuntimeError(
                            f"Pre-approval generation returned HTTP {blocked.status_code}"
                        )
                    existing_generations = [
                        job
                        for job in request_json(client, "GET", url + "/api/jobs")
                        if job.get("kind") == "generation" and job.get("package_id") == package_id
                    ]
                    if existing_generations:
                        raise RuntimeError("Blocked pre-approval request created a generation")
                    with page.expect_response(
                        lambda response: (
                            response.url.endswith(f"/api/packages/{package_id}/approve")
                            and response.request.method == "POST"
                        ),
                        timeout=max(1000, int(remaining() * 1000)),
                    ) as approval_response:
                        page.get_by_role(
                            "button", name="Утвердить план и текст", exact=True
                        ).click()
                    if approval_response.value.status != 200:
                        raise RuntimeError(
                            f"Brief approval returned HTTP {approval_response.value.status}"
                        )
                    approval = approval_response.value.json()
                    if (
                        approval.get("approved_package_hash") != draft["package_hash"]
                        or approval.get("approved_draft_hash") != draft["draft_hash"]
                    ):
                        raise RuntimeError("UI approval did not bind the sealed GET draft hashes")
                    expect(page.locator("#generate-button")).to_be_enabled(
                        timeout=max(1000, int(remaining() * 1000))
                    )
                    save_json(directory / "approval.json", approval)

                cancel = page.locator("#cancel-auto-generation")
                current_package = request_json(client, "GET", f"{url}/api/jobs/{package_id}")
                if current_package.get("auto_generation") == "scheduled":
                    expect(cancel).to_be_visible(
                        timeout=max(1000, min(5000, int(remaining() * 1000)))
                    )
                    with page.expect_response(
                        lambda response: (
                            response.url.endswith(
                                f"/api/packages/{package_id}/auto-generation/cancel"
                            )
                            and response.request.method == "POST"
                        ),
                        timeout=max(1000, int(remaining() * 1000)),
                    ) as cancelled:
                        cancel.click()
                    if cancelled.value.status != 200:
                        raise RuntimeError("UI could not cancel scheduled auto-generation")
                    save_json(directory / "auto-generation-cancel.json", cancelled.value.json())
                    current_package = request_json(client, "GET", f"{url}/api/jobs/{package_id}")
                # If the app's scheduled action already started, observe that one job
                # instead of issuing another click. Otherwise wait for the UI to settle.
                prior_generations = [r for r in request_log if r["url"].endswith("/api/generate")]
                auto_generation_started = bool(current_package.get("generation_id"))
                if current_package.get("generation_id"):
                    initial_id = current_package["generation_id"]
                elif prior_generations:
                    expect(page.locator("#generation-status")).to_be_visible(
                        timeout=max(1000, int(remaining() * 1000))
                    )
                    if not response_log:
                        response = page.wait_for_response(
                            lambda item: (
                                item.url.endswith("/api/generate") and item.request.method == "POST"
                            ),
                            timeout=max(1000, int(remaining() * 1000)),
                        )
                        response_log.append({"status": response.status, "body": response.json()})
                    generation_response_data = response_log[-1]
                    if generation_response_data.get("status") != 202:
                        raise RuntimeError("Scheduled UI generation was not accepted")
                    initial_id = generation_response_data["body"]["id"]
                else:
                    expect(page.locator("#generate-button")).to_be_enabled(
                        timeout=max(1000, int(remaining() * 1000))
                    )
                    with page.expect_response(
                        lambda response: (
                            response.url.endswith("/api/generate")
                            and response.request.method == "POST"
                        ),
                        timeout=max(1000, int(remaining() * 1000)),
                    ) as generation_response:
                        page.locator("#generate-button").click()
                    generation_response_data = {
                        "status": generation_response.value.status,
                        "body": generation_response.value.json(),
                    }
                    if generation_response_data.get("status") != 202:
                        raise RuntimeError("UI generation did not return an accepted job")
                    initial_id = generation_response_data["body"]["id"]
                initial = wait_job(
                    client,
                    url,
                    initial_id,
                    "generation",
                    directory,
                    remaining(),
                    0.2,
                )
                save_json(directory / "generation.json", initial)
                if initial["state"] not in ("completed", "needs_review", "failed"):
                    raise RuntimeError(
                        f"Generation ended as {initial['state']}: {initial.get('error')}"
                    )

                final = initial
                selected_comparison = None
                if selected_repair:
                    if (
                        initial["state"] == "failed"
                        and initial.get("failure_kind") != "quality_gate"
                    ):
                        raise RuntimeError("Selected-repair source failed outside the quality gate")
                    checkbox = page.locator("#audit-list input[type=checkbox]").first
                    checkbox.wait_for(state="visible", timeout=max(1000, int(remaining() * 1000)))
                    finding_id = checkbox.input_value()
                    if not finding_id:
                        raise RuntimeError("UI did not expose an actionable finding id")
                    finding_response = client.get(f"{url}/api/generations/{initial_id}/findings")
                    finding_response.raise_for_status()
                    source_review = finding_response.json()
                    selected = next(
                        (
                            item
                            for item in source_review.get("findings", [])
                            if item.get("id") == finding_id
                        ),
                        None,
                    )
                    if not selected or not selected.get("variant") or not selected.get("slide"):
                        raise RuntimeError("Selected finding lacks a concrete slide location")

                    def preview_hashes(generation_id, job):
                        hashes = {}
                        for variant in job.get("variants", []):
                            key = variant["key"]
                            for slide in range(1, int(variant.get("slides", 0)) + 1):
                                response = client.get(
                                    f"{url}/api/generations/{generation_id}/preview/{key}/{slide}"
                                )
                                response.raise_for_status()
                                raw = response.content
                                if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                                    raise RuntimeError(
                                        f"{generation_id} {key} slide {slide}: invalid review PNG"
                                    )
                                target = (
                                    directory
                                    / "repair-comparison"
                                    / generation_id
                                    / key
                                    / f"slide-{slide}.png"
                                )
                                target.parent.mkdir(parents=True, exist_ok=True)
                                target.write_bytes(raw)
                                hashes[(key, slide)] = sha256(raw).hexdigest()
                        return hashes

                    before_hashes = preview_hashes(initial_id, initial)
                    save_json(
                        directory / "repair-comparison" / "source-review.json",
                        source_review,
                    )
                    checkbox.check()
                    with page.expect_response(
                        lambda response: (
                            response.url.endswith(f"/api/generations/{initial_id}/repair")
                            and response.request.method == "POST"
                        ),
                        timeout=max(1000, int(remaining() * 1000)),
                    ) as repair_response:
                        page.get_by_role("button", name="Исправить выбранное").click()
                    if repair_response.value.status != 202:
                        raise RuntimeError(
                            f"UI repair returned HTTP {repair_response.value.status}"
                        )
                    repair_id = repair_response.value.json()["id"]
                    repair_request = next(
                        (item for item in reversed(request_log) if item["url"].endswith("/repair")),
                        None,
                    )
                    repair_body = repair_request["body"] if repair_request else None
                    if (
                        not isinstance(repair_body, dict)
                        or len(repair_body.get("finding_ids", [])) != 1
                        or repair_body["finding_ids"] != [finding_id]
                    ):
                        raise RuntimeError("UI repair did not submit exactly the selected finding")
                    final = wait_job(
                        client,
                        url,
                        repair_id,
                        "selected-repair",
                        directory,
                        remaining(),
                        0.2,
                    )
                    save_json(directory / "selected-repair.json", final)
                    if final["state"] not in ("completed", "needs_review"):
                        raise RuntimeError(
                            f"Selected repair ended as {final['state']}: {final.get('error')}"
                        )
                    if len([item for item in request_log if item["url"].endswith("/repair")]) != 1:
                        raise RuntimeError("UI submitted more than one selected repair")
                    after_hashes = preview_hashes(repair_id, final)
                    touched = (selected["variant"], int(selected["slide"]))
                    untouched = {
                        f"{variant}/{slide}": {
                            "source_sha256": before_hashes[(variant, slide)],
                            "repair_sha256": after_hashes[(variant, slide)],
                        }
                        for variant, slide in before_hashes
                        if (variant, slide) != touched
                    }
                    changed_untouched = [
                        name
                        for name, hashes in untouched.items()
                        if hashes["source_sha256"] != hashes["repair_sha256"]
                    ]
                    if changed_untouched:
                        raise RuntimeError(
                            "Selected repair changed untouched slides: "
                            + ", ".join(changed_untouched[:12])
                        )
                    selected_comparison = {
                        "selected_slide": f"{touched[0]}/{touched[1]}",
                        "selected_source_sha256": before_hashes[touched],
                        "selected_repair_sha256": after_hashes.get(touched),
                        "untouched_slides_compared": len(untouched),
                        "untouched_slides_unchanged": True,
                    }
                    save_json(
                        directory / "repair-comparison" / "comparison.json",
                        selected_comparison,
                    )

                page.locator("#result-grid").wait_for(
                    state="visible", timeout=max(1000, int(remaining() * 1000))
                )
                page.wait_for_function(
                    "generationId => document.querySelector('a.primary-download')?.href.includes(generationId)",
                    arg=final["id"],
                    timeout=max(1000, int(remaining() * 1000)),
                )
                with page.expect_download(timeout=max(1000, int(remaining() * 1000))) as download:
                    page.get_by_role("link", name=re.compile("Скачать PPTX")).first.click()
                ui_file = directory / "ui-download.pptx"
                download.value.save_as(ui_file)
                if not ui_file.is_file() or not Presentation(BytesIO(ui_file.read_bytes())).slides:
                    raise RuntimeError("UI PPTX download could not be opened")
                counts = verify_artifacts(client, url, final, directory)
                generate_requests = sum(
                    item["url"].endswith("/api/generate") for item in request_log
                )
                if generate_requests > 1 or (
                    not auto_generation_started and generate_requests != 1
                ):
                    raise RuntimeError(
                        "Browser workflow submitted zero or duplicate generation requests"
                    )
                generation_jobs = [
                    item
                    for item in request_json(client, "GET", url + "/api/jobs")
                    if item.get("kind") == "generation" and item.get("package_id") == package_id
                ]
                expected_ids = {initial_id}
                if selected_repair:
                    expected_ids.add(final["id"])
                if {item["id"] for item in generation_jobs} != expected_ids:
                    raise RuntimeError(
                        "Browser workflow produced an unexpected number of generation jobs"
                    )
                return {
                    "status": "passed",
                    "steps": {
                        "preparation": {"id": package_id, "state": prepared["state"]},
                        "generation": {"id": initial_id, "state": initial["state"]},
                        **(
                            {"selected_repair": {"id": final["id"], "state": final["state"]}}
                            if selected_repair
                            else {}
                        ),
                    },
                    "selected_repair": (
                        {
                            "finding_ids": [finding_id],
                            "source_generation": initial_id,
                            "repair_generation": final["id"],
                            "state": final["state"],
                            "comparison": selected_comparison,
                        }
                        if selected_repair
                        else None
                    ),
                    "generate_requests": generate_requests,
                    "automatic_generation_observed": auto_generation_started,
                    "generation_jobs": sorted(expected_ids),
                    "repair_requests": sum(item["url"].endswith("/repair") for item in request_log),
                    "slide_counts": counts,
                    "browser_version": browser_version,
                }
        finally:
            if page is not None:
                try:
                    page.screenshot(path=str(directory / "browser-final.png"), full_page=True)
                except Exception:
                    pass
            if context is not None and tracing:
                try:
                    context.tracing.stop(path=str(directory / "browser-trace.zip"))
                except Exception:
                    pass
            browser.close()


def execute_case(
    case,
    directory,
    *,
    mode,
    settings=None,
    max_requests=None,
    timeout=600,
):
    """Run a materialized case; return status and physical request count on every path."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    summary = {
        "case_id": case.get("id") if isinstance(case, dict) else None,
        "mode": mode,
        "status": "inconclusive",
        "model_requests": 0,
        "provider_requests": [],
        "generator_usage": {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cached_input_tokens": None,
            "reasoning_tokens": None,
            "reported_requests": 0,
        },
    }
    provider = None
    secret = getattr(settings, "api_key", "") if settings is not None else ""
    try:
        template, source_images, slides = _validate_case(case)
        images = _canonical_images(case, source_images, directory)
        input_hashes = {
            "template": sha256(template.read_bytes()).hexdigest(),
            "images": {str(path): sha256(path.read_bytes()).hexdigest() for path in source_images},
        }
        if mode not in ("replay", "live"):
            raise ValueError("Evaluation mode must be replay or live")
        if mode == "replay" and not case.get("cassette"):
            raise ValueError(
                "Replay case is missing its finite cassette; live fallback is disabled"
            )
        if mode == "live":
            if settings is None or settings.mode != "api" or not settings.api_key:
                raise ValueError("Live mode requires explicit API settings and credentials")
            if max_requests is None or max_requests <= 0:
                raise ValueError("Live mode requires a positive physical-request bound")
            if timeout <= 0:
                raise ValueError("Live mode requires a positive total wall-clock limit")
            from studio.providers.gateway import validate_model_policy

            validate_model_policy(settings)
            from dataclasses import replace

            settings = replace(
                settings,
                data_dir=directory / "server" / "data",
                execution_kind="live",
                download_fonts=False,
            )
            provider = LiveProvider(
                settings,
                max_requests=max_requests,
                timeout=timeout,
                request_dir=directory / "provider-requests",
            )
        elif settings is not None:
            raise ValueError("Settings are accepted only for explicitly bounded live mode")

        with _hard_deadline(timeout):
            with application(
                directory / "server",
                cassette=Path(case["cassette"]) if mode == "replay" else None,
                settings=settings,
                live_provider=provider,
            ) as (url, replay, _runtime_settings):
                if case.get("interface", "http") == "browser":
                    result = _run_browser(
                        case,
                        directory,
                        url,
                        timeout=max(0.001, timeout - (time.monotonic() - started)),
                        template=template,
                        images=images,
                        slides=slides,
                    )
                else:
                    result = _run_http(
                        case,
                        directory,
                        url,
                        mode=mode,
                        timeout=max(0.001, timeout - (time.monotonic() - started)),
                        template=template,
                        images=images,
                        slides=slides,
                    )
                summary.update(result)
                if replay is not None:
                    replay.assert_consumed()
        summary["status"] = _classify_result(result, directory)
        summary["source_hashes"] = input_hashes
        current_hashes = {
            "template": sha256(template.read_bytes()).hexdigest(),
            "images": {str(path): sha256(path.read_bytes()).hexdigest() for path in source_images},
        }
        summary["source_unchanged"] = input_hashes == current_hashes
        if not summary["source_unchanged"]:
            summary["status"] = "failed"
            summary["error"] = {
                "type": "SourceChanged",
                "message": "Materialized template or image bytes changed during evaluation",
            }
    except EvaluationDeadline as exc:
        summary["status"] = "inconclusive"
        summary["error"] = _safe_error(exc, secret)
    except Exception as exc:
        summary["status"] = _classify_exception(exc, directory)
        summary["error"] = _safe_error(exc, secret)
    finally:
        if provider is not None:
            provider.shutdown()
            summary.update(provider.summary())
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        try:
            summary["template_sha256"] = sha256(template.read_bytes()).hexdigest()
            summary["images_sha256"] = {
                str(path): sha256(path.read_bytes()).hexdigest() for path in images
            }
        except (UnboundLocalError, OSError):
            pass
        write_json(directory / "summary.json", summary)
    return summary


def _classify_result(result, directory):
    """Separate deterministic contract defects from incomplete provider runs."""
    if result.get("status") != "failed":
        return result.get("status", "inconclusive")
    snapshots = []
    for name in ("preparation.json", "generation.json", "selected-repair.json"):
        path = Path(directory) / name
        if path.is_file():
            try:
                snapshots.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                pass
    server = Path(directory) / "server"
    for path in server.glob("*-diagnostics.json"):
        try:
            diagnostics = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if diagnostics.get("state") == "failed":
            kind = diagnostics.get("checks", {}).get("failure_kind")
            if kind == "quality_gate":
                return "failed"
            snapshots.append({"state": "failed", "failure_kind": kind})
    for snapshot in snapshots:
        if snapshot.get("failure_kind") == "quality_gate":
            return "failed"
        if snapshot.get("state") in ("failed", "timed_out", "cancelled"):
            return "inconclusive"
    failure = result.get("failure") or result.get("error") or {}
    message = str(failure.get("message", "")).casefold()
    if any(
        marker in message
        for marker in (
            "timeout",
            "timed out",
            "exceeded",
            "connection",
            "provider",
            "model",
            "http 4",
            "http 5",
            "budget",
            "not ready",
            "returned http",
        )
    ):
        return "inconclusive"
    # A completed app job followed by a failed artifact/cache/approval assertion
    # is a reproducible application defect, not a judgment-quality ambiguity.
    return "failed"


def _classify_exception(exc, directory):
    """Treat an incomplete/model-side job as inconclusive; keep contract defects hard."""
    for path in (Path(directory) / "server").glob("*-diagnostics.json"):
        try:
            diagnostics = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if diagnostics.get("state") == "failed":
            kind = diagnostics.get("checks", {}).get("failure_kind")
            return "failed" if kind == "quality_gate" else "inconclusive"
    if isinstance(exc, (FileNotFoundError, OSError, TimeoutError, ValueError)):
        return "inconclusive"
    message = str(exc).casefold()
    if any(
        marker in message
        for marker in (
            "timeout",
            "timed out",
            "provider",
            "model",
            "json",
            "incomplete",
            "not completed",
            "http 4",
            "http 5",
            "budget",
            "connection",
            "ended as failed",
        )
    ):
        return "inconclusive"
    return "failed"
