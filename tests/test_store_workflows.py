"""Concurrency and immutable-version guarantees at the SQLite boundary."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from studio.jobs.store import Store


def brief(store):
    job = store.create("preparation", {"input_mode": "brief"})
    store.update(
        job["id"],
        "ready",
        package_hash="package-v1",
        draft_hash="draft-v1",
        auto_generation="needs_confirmation",
    )
    return job["id"]


def generation(store, state="completed", **fields):
    pid = store.create("preparation")["id"]
    store.update(pid, "ready")
    gid = store.generation_for(pid)[0]["id"]
    store.update(gid, state, audit_hash="audit-v1", review_available=True, **fields)
    return gid


def race(call, count=8):
    barrier = Barrier(count)

    def run(_):
        barrier.wait()
        return call()

    with ThreadPoolExecutor(count) as pool:
        return list(pool.map(run, range(count)))


def test_brief_approval_binds_both_hashes_and_cannot_reset_cancelled_timer(tmp_path):
    store = Store(tmp_path)
    pid = brief(store)
    for automatic in (False, True):
        with pytest.raises(ValueError, match="Утвердите"):
            store.generation_for(pid, automatic=automatic)
    for p, d in [("old-package", "draft-v1"), ("package-v1", "old-draft")]:
        with pytest.raises(ValueError, match="изменился"):
            store.approve_brief(pid, p, d)
    approved = store.approve_brief(pid, "package-v1", "draft-v1")
    assert approved["auto_generation"] == "manual"
    assert "auto_generate_at" not in approved
    assert store.generation_for(pid, automatic=True) == (None, False)
    # Existing persisted timers remain cancellable and repeated approval cannot restore one.
    store.update(pid, auto_generation="scheduled", auto_generate_at=4102444800)
    store.cancel_auto_generation(pid)
    again = store.approve_brief(pid, "package-v1", "draft-v1")
    assert again["auto_generation"] == "cancelled"
    assert again["approved_at"] == approved["approved_at"]
    results = race(lambda: store.generation_for(pid))
    assert len({job["id"] for job, _ in results}) == 1
    assert sum(created for _, created in results) == 1


@pytest.mark.parametrize("legacy_timer", [False, True])
def test_draft_edit_is_idempotent_and_does_not_inherit_approval(tmp_path, legacy_timer):
    store = Store(tmp_path)
    pid = brief(store)
    store.approve_brief(pid, "package-v1", "draft-v1")
    if legacy_timer:
        store.update(pid, auto_generation="scheduled", auto_generate_at=4102444800)
    results = race(lambda: store.claim_draft_revision(pid, "package-v1", {"slides": []}))
    assert len({job["id"] for job, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    new = results[0][0]
    assert new["parent_package"] == pid
    assert new["auto_generation"] == "needs_confirmation"
    assert "approved_package_hash" not in new
    assert store.get(pid)["auto_generation"] == ("cancelled" if legacy_timer else "manual")
    assert store.get(pid)["package_hash"] == "package-v1"


def test_repair_atomic_idempotency_selection_and_competing_launch(tmp_path):
    store = Store(tmp_path)
    gid = generation(store)
    original = store.get(gid)
    with pytest.raises(ValueError, match="Аудит изменился"):
        store.claim_repair(gid, "old-audit", ["a"])
    results = race(lambda: store.claim_repair(gid, "audit-v1", ["b", "a", "a"]))
    assert len({job["id"] for job, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    child = results[0][0]
    assert child["finding_ids"] == ["a", "b"]
    assert child["parent_generation_id"] == gid
    with pytest.raises(ValueError, match="Уже выполняется"):
        store.claim_repair(gid, "audit-v1", ["c"])
    assert store.get(gid) == original
    assert store.cancel_active(child["id"])
    store.update(child["id"], "completed", result="late output")
    assert store.get(child["id"])["state"] == "cancelled"
    assert "result" not in store.get(child["id"])
    assert store.claim_repair(gid, "audit-v1", ["a", "b"])[0]["id"] == child["id"]


@pytest.mark.parametrize("quality_gate", [False, True])
def test_only_quality_failure_is_a_reviewable_draft(tmp_path, quality_gate):
    store = Store(tmp_path)
    gid = generation(store, "failed", failure_kind="quality_gate" if quality_gate else "network")
    if quality_gate:
        assert store.claim_repair(gid, "audit-v1", ["a"])[1]
    else:
        with pytest.raises(ValueError, match="недоступен"):
            store.claim_repair(gid, "audit-v1", ["a"])


@pytest.mark.parametrize("state", ["completed", "needs_review", "failed", "cancelled", "timed_out"])
def test_terminal_generation_cannot_be_modified_by_late_worker(tmp_path, state):
    store = Store(tmp_path)
    gid = generation(store, state)
    before = store.get(gid)
    store.update(gid, "running", progress=50)
    store.update(gid, "completed", published=True)
    assert store.get(gid) == before


def test_recovered_job_cannot_publish_after_restart(tmp_path):
    store = Store(tmp_path)
    job = store.create("generation")
    store.update(job["id"], "running")
    store.recover()
    store.update(job["id"], "completed", download="presentations.zip")
    assert store.get(job["id"])["state"] == "failed"
    assert "download" not in store.get(job["id"])
