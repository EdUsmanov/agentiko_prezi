from pathlib import Path
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
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
            if row["state"] in ("timed_out", "cancelled"):
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

    def cancel_active(self, jid):
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
                error="Запуск прерван при остановке сервера",
                phase="Анализ прерван",
                auto_generation="cancelled",
            )
            c.execute(
                "UPDATE jobs SET state='cancelled',data=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), jid),
            )
        self.log(jid, "job.state", state="cancelled", phase="Анализ прерван")
        return True

    def recent(self):
        with self.connect() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM jobs ORDER BY created DESC LIMIT 30")]
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
        from .diagnostics import redact

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
