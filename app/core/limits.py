from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from app.core.config import MAX_EXPORTS

_export_slots = threading.BoundedSemaphore(MAX_EXPORTS)


@contextmanager
def export_slot() -> Iterator[None]:
    """Bound concurrent encoders across streaming, mixdown, and ZIP requests."""
    if not _export_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail="Audio exports are busy. Try again shortly.",
            headers={"Retry-After": "3"},
        )
    try:
        yield
    finally:
        _export_slots.release()
