from pathlib import Path
import json
import sqlite3
import time
import uuid

class Store:
    def __init__(self, root: Path):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.db=self.root/"studio.sqlite3"
        with self.connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,kind TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,data TEXT NOT NULL)")

    def connect(self):
        c=sqlite3.connect(self.db,timeout=10)
        c.row_factory=sqlite3.Row
        return c

    def create(self,kind,data=None):
        jid=uuid.uuid4().hex
        with self.connect() as c:
            if kind=="generation":
                c.execute("BEGIN IMMEDIATE")
                if c.execute("SELECT 1 FROM jobs WHERE kind='generation' AND state IN ('accepted','running')").fetchone():
                    raise ValueError("Уже выполняется генерация. Новый запуск не ставится в скрытую очередь.")
            c.execute("INSERT INTO jobs VALUES (?,?,?,?,?)",(jid,kind,"accepted",time.time(),json.dumps(data or {},ensure_ascii=False)))
        self.directory(jid).mkdir(parents=True,exist_ok=True)
        return self.get(jid)

    def directory(self,jid):
        if len(jid)!=32 or any(c not in "0123456789abcdef" for c in jid):
            raise KeyError("Неизвестный идентификатор")
        return self.root/"jobs"/jid

    def get(self,jid):
        self.directory(jid)
        with self.connect() as c:
            row=c.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone()
        if row is None:
            raise KeyError("Задание не найдено")
        return {"id":row["id"],"kind":row["kind"],"state":row["state"],"created":row["created"],**json.loads(row["data"])}

    def update(self,jid,state=None,**fields):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT state,data FROM jobs WHERE id=?",(jid,)).fetchone()
            if row is None:
                raise KeyError(jid)
            # A terminated job cannot be resurrected by a late worker.
            if row["state"] in ("timed_out","cancelled"):
                return
            data=json.loads(row["data"]);data.update(fields)
            c.execute("UPDATE jobs SET state=?,data=? WHERE id=?",(state or row["state"],json.dumps(data,ensure_ascii=False),jid))

    def recent(self):
        with self.connect() as c:
            ids=[r[0] for r in c.execute("SELECT id FROM jobs ORDER BY created DESC LIMIT 30")]
        return [self.get(jid) for jid in ids]

    def recover(self):
        for job in self.recent():
            if job["state"] in ("accepted","running"):
                self.update(job["id"],"failed",error="Процесс был прерван. Повторите запуск.")
