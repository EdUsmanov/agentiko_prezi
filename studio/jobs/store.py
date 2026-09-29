from pathlib import Path
import json
import sqlite3
import time
import uuid
from hashlib import sha256
from contextlib import contextmanager


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "studio.sqlite3"
        with self.connect() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,kind TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,data TEXT NOT NULL)"
            )

            c.execute(
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,created REAL NOT NULL,level TEXT NOT NULL,event TEXT NOT NULL,data TEXT NOT NULL)"
            )
            c.execute("CREATE INDEX IF NOT EXISTS events_job ON events(job_id,id)")

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=10)
        try:
            c.row_factory = sqlite3.Row
            with c:
                yield c
        finally:
            c.close()

    def create(self, kind, data=None):
        jid = uuid.uuid4().hex
        with self.connect() as c:
            if kind == "generation":
                c.execute("BEGIN IMMEDIATE")
                if c.execute(
                    "SELECT 1 FROM jobs WHERE kind='generation' AND state IN ('accepted','running')"
                ).fetchone():
                    raise ValueError(
                        "Уже выполняется генерация. Новый запуск не ставится в скрытую очередь."
                    )
            c.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?)",
                (jid, kind, "accepted", time.time(), json.dumps(data or {}, ensure_ascii=False)),
            )
        self.directory(jid).mkdir(parents=True, exist_ok=True)
        self.log(jid, "job.created", kind=kind)
        return self.get(jid)

    def directory(self, jid):
        if len(jid) != 32 or any(c not in "0123456789abcdef" for c in jid):
            raise KeyError("Неизвестный идентификатор")
        return self.root / "jobs" / jid

    def get(self, jid):
        self.directory(jid)
        with self.connect() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if row is None:
            raise KeyError("Задание не найдено")
        return {
            "id": row["id"],
            "kind": row["kind"],
            "state": row["state"],
            "created": row["created"],
            **json.loads(row["data"]),
        }

    def update(self, jid, state=None, **fields):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT state,data FROM jobs WHERE id=?", (jid,)).fetchone()
            if row is None:
                raise KeyError(jid)
            # A terminated job cannot be resurrected by a late worker.
            if row["state"] in ("timed_out", "cancelled", "failed", "completed", "needs_review"):
                return
            data = json.loads(row["data"])
            data.update(fields)
            c.execute(
                "UPDATE jobs SET state=?,data=? WHERE id=?",
                (state or row["state"], json.dumps(data, ensure_ascii=False), jid),
            )

        if state or any(k in fields for k in ("phase", "error")):
            self.log(
                jid,
                "job.state",
                level="error" if state in ("failed", "timed_out") else "info",
                state=state or row["state"],
                **{
                    k: v
                    for k, v in fields.items()
                    if k in ("phase", "progress", "error", "elapsed_seconds")
                },
            )

    def cancel_active(self, jid, reason="Запуск прерван при остановке сервера"):
        """Cancel only an unfinished job, without racing a completed result."""
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT state,data FROM jobs WHERE id=?", (jid,)).fetchone()
            if row is None:
                raise KeyError(jid)
            if row["state"] not in ("accepted", "running"):
                return False
            data = json.loads(row["data"])
            data.update(
                error=reason,
                phase="Задание отменено",
                auto_generation="cancelled",
            )
            c.execute(
                "UPDATE jobs SET state='cancelled',data=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), jid),
            )
        self.log(jid, "job.state", state="cancelled", phase="Задание отменено")
        return True

    def recent(self, exclude_kind=None):
        with self.connect() as c:
            if exclude_kind:
                rows = c.execute(
                    "SELECT id FROM jobs WHERE kind!=? ORDER BY created DESC LIMIT 30",
                    (exclude_kind,),
                )
            else:
                rows = c.execute("SELECT id FROM jobs ORDER BY created DESC LIMIT 30")
            ids = [r[0] for r in rows]
        return [self.get(jid) for jid in ids]

    def recover(self):
        # UI pagination must not leave older active jobs blocking all new generation.
        with self.connect() as c:
            ids = [
                r[0] for r in c.execute("SELECT id FROM jobs WHERE state IN ('accepted','running')")
            ]
        for jid in ids:
            self.update(jid, "failed", error="Процесс был прерван. Повторите запуск.")

    def log(self, jid, event, level="info", **data):
        from studio.diagnostics import redact

        # Serialize before redaction so nested fields receive the same protection.
        encoded = redact(json.dumps(data, ensure_ascii=False, default=str))
        if len(encoded) > 24000:
            encoded = json.dumps(
                {"message": redact(str(data))[:23000], "truncated": True}, ensure_ascii=False
            )
        with self.connect() as c:
            c.execute(
                "INSERT INTO events(job_id,created,level,event,data) VALUES(?,?,?,?,?)",
                (jid, time.time(), level, event, encoded),
            )
            # A bounded local journal; retain the most recent 10,000 records per job.
            c.execute(
                "DELETE FROM events WHERE job_id=? AND id NOT IN (SELECT id FROM events WHERE job_id=? ORDER BY id DESC LIMIT 10000)",
                (jid, jid),
            )

    def events(self, jid, after=0, limit=500, latest=False):
        with self.connect() as c:
            order = "DESC" if latest else "ASC"
            rows = c.execute(
                f"SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id {order} LIMIT ?",
                (jid, after, min(max(limit, 1), 10000)),
            ).fetchall()
        if latest:
            rows = list(reversed(rows))
        return [
            {
                "id": r["id"],
                "created": r["created"],
                "level": r["level"],
                "event": r["event"],
                "data": json.loads(r["data"]),
            }
            for r in rows
        ]

    def generation_for(self, pid, automatic=False):
        """Claim once per immutable package, atomically across concurrent requests."""
        jid = uuid.uuid4().hex
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT state,data FROM jobs WHERE id=? AND kind='preparation'", (pid,)
            ).fetchone()
            if row is None or row["state"] != "ready":
                raise ValueError("Пакет не готов к генерации")
            data = json.loads(row["data"])
            if data.get("input_mode") == "brief" and (
                not data.get("draft_hash")
                or data.get("approved_draft_hash") != data.get("draft_hash")
                or data.get("approved_package_hash") != data.get("package_hash")
            ):
                raise ValueError("Утвердите план и текст краткого брифа перед генерацией")
            if data.get("generation_id"):
                previous = self.get(data["generation_id"])
                if automatic or previous["state"] not in ("failed", "cancelled", "timed_out"):
                    return previous, False
            if automatic and data.get("auto_generation") != "scheduled":
                return None, False
            if c.execute(
                "SELECT 1 FROM jobs WHERE kind='generation' AND state IN ('accepted','running')"
            ).fetchone():
                raise ValueError(
                    "Уже выполняется генерация. Новый запуск не ставится в скрытую очередь."
                )
            c.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?)",
                (
                    jid,
                    "generation",
                    "accepted",
                    time.time(),
                    json.dumps({"package_id": pid, "progress": 0, "phase": "Запуск"}),
                ),
            )
            data.update(generation_id=jid, auto_generation="started")
            c.execute(
                "UPDATE jobs SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), pid)
            )
        self.directory(jid).mkdir(parents=True, exist_ok=True)
        self.log(jid, "generation.created", package_id=pid, automatic=automatic)
        self.log(pid, "generation.started", generation_id=jid, automatic=automatic)
        return self.get(jid), True

    def approve_brief(self, pid, package_hash, draft_hash):
        """Approve the exact sealed package and draft in the same transaction as scheduling."""
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT state,data FROM jobs WHERE id=? AND kind='preparation'", (pid,)
            ).fetchone()
            if row is None:
                raise KeyError("Пакет не найден")
            data = json.loads(row["data"])
            if row["state"] != "ready" or data.get("input_mode") != "brief":
                raise ValueError("Пакет не ожидает утверждения краткого брифа")
            if not draft_hash or (data.get("package_hash"), data.get("draft_hash")) != (
                package_hash,
                draft_hash,
            ):
                raise ValueError("План или текст изменился. Откройте актуальную версию")
            if (
                data.get("approved_package_hash") == package_hash
                and data.get("approved_draft_hash") == draft_hash
            ):
                return self.get(pid)
            if (data.get("control", {}).get("slide_budget") or {}).get("status") == "needs_input":
                raise ValueError("Материал требует доработки перед утверждением")
            data.update(
                approved_package_hash=package_hash,
                approved_draft_hash=draft_hash,
                approved_at=time.time(),
                auto_generation="scheduled",
                auto_generate_at=time.time() + 60,
            )
            c.execute(
                "UPDATE jobs SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), pid)
            )
        self.log(pid, "brief.approved", draft_hash=draft_hash, package_hash=package_hash)
        return self.get(pid)

    def claim_draft_revision(self, pid, package_hash, draft_json):
        """Create one unapproved preparation for one edit of an immutable draft."""
        value = json.loads(draft_json) if isinstance(draft_json, str) else draft_json
        request_hash = sha256(
            json.dumps([pid, package_hash, value], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        jid = uuid.uuid4().hex
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT state,data FROM jobs WHERE id=? AND kind='preparation'", (pid,)
            ).fetchone()
            if row is None:
                raise KeyError("Пакет не найден")
            original = json.loads(row["data"])
            if row["state"] != "ready" or original.get("package_hash") != package_hash:
                raise ValueError("План или текст изменился. Откройте актуальную версию")
            if original.get("input_mode") != "brief":
                raise ValueError("Этот пакет не содержит черновик краткого брифа")
            previous = c.execute(
                "SELECT id FROM jobs WHERE kind='preparation' "
                "AND json_extract(data,'$.draft_request_hash')=?",
                (request_hash,),
            ).fetchone()
            if previous:
                return self.get(previous["id"]), False
            data = {
                "parent_package": pid,
                "input_mode": "brief",
                "template_name": original.get("template_name", ""),
                "draft_request_hash": request_hash,
                "draft": value,
                "auto_generation": "needs_confirmation",
            }
            c.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?)",
                (jid, "preparation", "accepted", time.time(), json.dumps(data, ensure_ascii=False)),
            )
            if original.get("auto_generation") == "scheduled":
                original["auto_generation"] = "cancelled"
                c.execute(
                    "UPDATE jobs SET data=? WHERE id=?",
                    (json.dumps(original, ensure_ascii=False), pid),
                )
        self.directory(jid).mkdir(parents=True, exist_ok=True)
        self.log(jid, "brief.revision_created", parent_package=pid)
        return self.get(jid), True

    def claim_repair(self, source_id, audit_hash, finding_ids):
        """Atomically reserve a revision under the same single-generator limit."""
        selection = sorted(set(finding_ids))
        if not selection or len(selection) > 30 or not audit_hash:
            raise ValueError("Выберите от 1 до 30 замечаний")
        request_hash = sha256(
            json.dumps([source_id, audit_hash, selection], separators=(",", ":")).encode()
        ).hexdigest()
        jid = uuid.uuid4().hex
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT state,data FROM jobs WHERE id=? AND kind='generation'", (source_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Генерация не найдена")
            source = json.loads(row["data"])
            reviewable = row["state"] in ("completed", "needs_review") or (
                row["state"] == "failed" and source.get("failure_kind") == "quality_gate"
            )
            if (
                not reviewable
                or not source.get("review_available")
                or source.get("audit_hash") != audit_hash
            ):
                raise ValueError("Аудит изменился или результат недоступен для исправления")
            previous = c.execute(
                "SELECT id FROM jobs WHERE kind='generation' "
                "AND json_extract(data,'$.repair_request_hash')=?",
                (request_hash,),
            ).fetchone()
            if previous:
                return self.get(previous["id"]), False
            if c.execute(
                "SELECT 1 FROM jobs WHERE kind='generation' AND state IN ('accepted','running')"
            ).fetchone():
                raise ValueError("Уже выполняется генерация или исправление")
            data = {
                "operation": "repair",
                "package_id": source["package_id"],
                "parent_generation_id": source_id,
                "audit_hash": audit_hash,
                "finding_ids": selection,
                "selected_finding_ids": selection,
                "repair_request_hash": request_hash,
                "progress": 0,
                "phase": "Подготовка выбранных исправлений",
            }
            c.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?)",
                (jid, "generation", "accepted", time.time(), json.dumps(data, ensure_ascii=False)),
            )
        self.directory(jid).mkdir(parents=True, exist_ok=True)
        self.log(jid, "repair.created", parent_generation_id=source_id, finding_ids=selection)
        return self.get(jid), True

    def cancel_auto_generation(self, pid):
        """Cancellation and launch use the same transaction boundary."""
        changed = False
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT kind,data FROM jobs WHERE id=?", (pid,)).fetchone()
            if row is None:
                raise KeyError("Задание не найдено")
            if row["kind"] != "preparation":
                raise ValueError("Это не пакет анализа")
            data = json.loads(row["data"])
            if data.get("auto_generation") == "scheduled":
                data["auto_generation"] = "cancelled"
                c.execute(
                    "UPDATE jobs SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), pid)
                )
                changed = True
        if changed:
            self.log(pid, "generation.autostart_cancelled")
        return self.get(pid)

    def scheduled(self):
        with self.connect() as c:
            ids = [
                r[0]
                for r in c.execute("SELECT id FROM jobs WHERE kind='preparation' AND state='ready'")
            ]
        return [j for jid in ids if (j := self.get(jid)).get("auto_generation") == "scheduled"]
