from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
_MULTIPART_OVERHEAD_BYTES = 64 * 1024
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if not 0 < port <= 65535:
            return None
        return parsed.scheme, parsed.hostname.lower().rstrip("."), port
    except ValueError:
        return None


def allowed_hosts() -> tuple[str, ...]:
    raw = os.environ.get("STEMDECK_ALLOWED_HOSTS", "").strip()
    if not raw:
        return _LOCAL_HOSTS
    hosts = tuple(value.strip().lower().strip("[]") for value in raw.split(","))
    for host in hosts:
        authority = f"[{host}]" if ":" in host else host
        parsed = _origin(f"http://{authority}")
        if parsed is None or parsed[1] != host or "*" in host:
            raise ValueError("STEMDECK_ALLOWED_HOSTS must contain exact hostnames or IP addresses")
    return tuple(dict.fromkeys((*_LOCAL_HOSTS, *hosts)))


class RequestGuard:
    """Protect the single-user backend before reading or parsing request bodies."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self.hosts = allowed_hosts()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        current_origin = _origin(f"{scope['scheme']}://{headers.get('host', '')}")
        if current_origin is None or current_origin[1] not in self.hosts:
            await JSONResponse({"detail": "Untrusted request host"}, status_code=400)(
                scope, receive, send
            )
            return
        is_api = scope["path"] == "/api" or scope["path"].startswith("/api/")
        if not is_api:
            await self.app(scope, receive, send)
            return
        origin = headers.get("origin")
        if (origin is not None and _origin(origin) != current_origin) or headers.get(
            "sec-fetch-site"
        ) in ("cross-site", "same-site"):
            await JSONResponse(
                {"detail": "Cross-origin API requests are not allowed"}, status_code=403
            )(scope, receive, send)
            return
        is_upload = (
            scope["method"] == "POST"
            and scope["path"] == "/api/jobs"
            and headers.get("content-type", "").lower().startswith("multipart/form-data")
        )
        limit = MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD_BYTES if is_upload else MAX_JSON_BYTES
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                size = int(content_length)
            except ValueError:
                size = -1
            if size < 0 or size > limit:
                status = 413 if size > limit else 400
                await JSONResponse(
                    {"detail": "Invalid or oversized request body"}, status_code=status
                )(scope, receive, send)
                return
        received_bytes = 0

        async def bounded_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > limit:
                    raise HTTPException(status_code=413, detail="Request body exceeds size limit")
            return message

        await self.app(scope, bounded_receive, send)
