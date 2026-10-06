from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.models import Job
from app.core.registry import _jobs
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.jobs.JOBS_DIR", tmp_path)
    _jobs.clear()
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client
    _jobs.clear()


def test_rebinding_host_cannot_read_local_library(client):
    response = client.get("/api/jobs", headers={"host": "attacker.example"})
    assert response.status_code == 400


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://attacker.example"},
        {"origin": "null"},
        {"origin": "http://127.0.0.1:9999"},
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site"},
    ],
)
def test_external_page_cannot_cancel_job(client, headers):
    job = Job(id="abcdefabcdef", status="queued")
    _jobs[job.id] = job
    response = client.post(f"/api/jobs/{job.id}/cancel", headers=headers)
    assert response.status_code == 403
    assert not job.cancel_requested
    assert job.status == "queued"


def test_same_origin_and_native_clients_can_read_library(client):
    assert client.get("/api/jobs", headers={"origin": "http://127.0.0.1"}).status_code == 200
    assert client.get("/health").status_code == 200


def test_chunked_json_limit_leaves_no_registered_job(client, tmp_path):
    response = client.post(
        "/api/jobs",
        content=iter([b" " * (1024 * 1024 + 1)]),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert not _jobs
    assert not list(tmp_path.iterdir())


def test_security_headers_and_private_api_cache(client):
    response = client.get("/api/jobs")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert response.headers.get("cache-control") == "no-store"


def test_unsupported_media_authority_leaves_no_job_or_files(client, tmp_path):
    response = client.post(
        "/api/jobs", json={"url": "https://user:password@soundcloud.com:8080/artist/track"}
    )
    assert response.status_code == 422
    assert "password" not in response.text
    assert not _jobs
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_chunked_upload_limit_closes_spooled_files(monkeypatch):
    from starlette import formparsers

    from app.core import security

    opened_files = []
    original_file = formparsers.SpooledTemporaryFile

    def record_file(*args, **kwargs):
        file = original_file(*args, **kwargs)
        opened_files.append(file)
        return file

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", record_file)
    monkeypatch.setattr(formparsers.MultiPartParser, "spool_max_size", 16)
    monkeypatch.setattr(security, "MAX_UPLOAD_BYTES", 256)
    monkeypatch.setattr(security, "_MULTIPART_OVERHEAD_BYTES", 0)
    endpoint_called = False
    upload_app = FastAPI()

    @upload_app.post("/api/jobs")
    async def upload(request: Request):
        nonlocal endpoint_called
        async with request.form() as form:
            endpoint_called = True
            return {"files": len(form)}

    chunks = iter(
        [
            b'--audit\r\nContent-Disposition: form-data; name="file"; filename="test.wav"'
            b"\r\nContent-Type: audio/wav\r\n\r\n" + b"a" * 32,
            b"a" * 256 + b"\r\n--audit--\r\n",
        ]
    )
    responses = []

    async def receive():
        return {"type": "http.request", "body": next(chunks), "more_body": True}

    async def send(message):
        responses.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/jobs",
        "query_string": b"",
        "headers": [
            (b"host", b"127.0.0.1"),
            (b"content-type", b"multipart/form-data; boundary=audit"),
        ],
    }
    await security.RequestGuard(upload_app)(scope, receive, send)

    assert responses[0]["status"] == 413
    assert not endpoint_called
    assert opened_files
    assert all(file.closed for file in opened_files)
