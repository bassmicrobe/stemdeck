from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from typing import Any

from app.core.models import Job

_MAX_JOB_ENTRIES = 300
_MAX_SYSTEM_ENTRIES = 1000
_counter = itertools.count(1)
_lock = threading.Lock()
_system_entries: deque[dict[str, Any]] = deque(maxlen=_MAX_SYSTEM_ENTRIES)


def _clean_message(message: object) -> str:
    return " ".join(str(message or "").split())[:600]


def add_job_log(
    job: Job,
    message: object,
    *,
    level: str = "info",
    stage: str | None = None,
    progress: float | int | None = None,
) -> dict[str, Any] | None:
    text = _clean_message(message)
    if not text:
        return None
    normalized_level = level if level in {"debug", "info", "warning", "error"} else "info"
    try:
        progress_percent = round(max(0.0, min(1.0, float(progress))) * 100)
    except (TypeError, ValueError):
        progress_percent = None

    with _lock:
        previous = job.logs[-1] if job.logs else None
        if (
            previous
            and previous.get("message") == text
            and previous.get("level") == normalized_level
            and previous.get("stage") == (_clean_message(stage) or None)
            and previous.get("progress_percent") == progress_percent
        ):
            return previous
        entry = {
            "id": next(_counter),
            "timestamp": time.time(),
            "job_id": job.id,
            "level": normalized_level,
            "message": text,
            "stage": _clean_message(stage) or None,
            "progress_percent": progress_percent,
        }
        job.logs.append(entry)
        if len(job.logs) > _MAX_JOB_ENTRIES:
            del job.logs[:-_MAX_JOB_ENTRIES]
        _system_entries.append(entry)
        return entry


def add_system_log(message: object, *, level: str = "info") -> dict[str, Any] | None:
    text = _clean_message(message)
    if not text:
        return None
    normalized_level = level if level in {"debug", "info", "warning", "error"} else "info"
    with _lock:
        entry = {
            "id": next(_counter),
            "timestamp": time.time(),
            "job_id": None,
            "level": normalized_level,
            "message": text,
            "stage": None,
            "progress_percent": None,
        }
        _system_entries.append(entry)
        return entry


def job_logs(job: Job, *, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    safe_limit = max(1, min(500, int(limit)))
    with _lock:
        entries = list(job.logs)
    return [entry for entry in entries if _entry_id(entry) > after][-safe_limit:]


def system_logs(*, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    safe_limit = max(1, min(500, int(limit)))
    with _lock:
        entries = list(_system_entries)
    return [entry for entry in entries if _entry_id(entry) > after][-safe_limit:]


def _entry_id(entry: dict[str, Any]) -> int:
    try:
        return int(entry.get("id", 0))
    except (TypeError, ValueError):
        return 0
