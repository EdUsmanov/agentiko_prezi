"""Job-scoped diagnostics: metadata and redacted tracebacks, never model payloads."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import os
import re
import traceback

_current = ContextVar("studio_diagnostics", default=None)
_secrets = set()


def configure(secret=""):
    if secret:
        _secrets.add(secret)
    root = logging.getLogger()
    if not any(isinstance(h, JobHandler) for h in root.handlers):
        root.addHandler(JobHandler())
    logging.getLogger("studio").setLevel(logging.INFO)


def redact(value):
    text = str(value)
    secrets = _secrets | {v for k,v in os.environ.items()
        if v and any(word in k.upper() for word in ("API_KEY", "TOKEN", "PASSWORD", "SECRET"))}
    for secret in sorted(secrets, key=len, reverse=True):
        if len(secret) >= 6:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(bearer\s+)[^\s\"',;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|password|secret|access_token)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&}]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@", r"\1[REDACTED]@", text)
    return text


@contextmanager
def scope(store, job_id):
    token = _current.set((store, job_id))
    try:
        yield
    finally:
        _current.reset(token)


def event(name, level="info", **data):
    current = _current.get()
    if current:
        current[0].log(current[1], name, level=level, **data)


def background_work():
    """Library refreshes yield API admission to interactive user jobs."""
    current=_current.get()
    return bool(current and current[1]=='library')


def exception(name, error):
    event(name, "error", error_type=type(error).__name__,
          traceback="".join(traceback.format_exception(type(error), error, error.__traceback__)))


class JobHandler(logging.Handler):
    def emit(self, record):
        if not _current.get() or not record.name.startswith("studio"):
            return
        data = {"logger": record.name, "message": record.getMessage()}
        if record.exc_info:
            data["traceback"] = "".join(traceback.format_exception(*record.exc_info))
        event(getattr(record, "event", "application.log"), record.levelname.lower(), **data)


async def capture_stream(store, job_id, stream, event_name="worker.output"):
    """Redact complete lines: secrets must not leak across pipe chunk boundaries."""
    pending=bytearray();discarding=False
    while chunk:=await stream.read(4096):
        for fragment in chunk.splitlines(keepends=True):
            if not discarding:
                pending.extend(fragment)
                if len(pending)>32768:
                    pending.clear();discarding=True
            if fragment.endswith((b"\n",b"\r")):
                if discarding:
                    store.log(job_id,event_name,message="[oversized output line omitted]")
                else:
                    store.log(job_id,event_name,message=pending.decode("utf-8","replace"))
                pending.clear();discarding=False
    if pending:
        store.log(job_id,event_name,message=pending.decode("utf-8","replace"))


def stage_summary(calls):
    """Call durations can overlap; aggregates are not wall-clock job duration."""
    stages={}
    for call in calls:
        row=stages.setdefault(call['stage'],dict(calls=0,cache_hits=0,failed=0,seconds=0,queue_seconds=0,provider_seconds=0,backoff_seconds=0))
        row['calls']+=1;row['failed']+=call.get('status')!='completed'
        row['cache_hits']+=bool(call.get('cache_hit'))
        for name in ('seconds','queue_seconds','provider_seconds','backoff_seconds'):
            row[name]=round(row[name]+call.get(name,0),3)
    return {'scope':'sum_of_calls; stages may overlap; provider includes response transfer', 'stages':stages}
