from __future__ import annotations

import io
import json
import subprocess
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import MAX_PENDING_JOBS
from app.core.models import Job
from app.core.registry import _jobs


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test gets a fresh in-memory registry."""
    _jobs.clear()
    yield
    _jobs.clear()


@pytest.fixture
def client():
    async def _noop_pipeline(job, url, jobs_dir):
        return None

    with patch("app.api.jobs.run_pipeline", _noop_pipeline):
        from app.main import app

        with TestClient(app) as c:
            yield c


@pytest.fixture
def upload_client(tmp_path, monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg, "JOBS_DIR", tmp_path)

    async def _noop_local(job, source_path, jobs_dir):
        return None

    async def _noop_youtube(job, url, jobs_dir):
        return None

    with (
        patch("app.api.jobs.run_local_pipeline", _noop_local),
        patch("app.api.jobs.run_pipeline", _noop_youtube),
        patch("app.api.jobs._probe_duration", return_value=60.0),
    ):
        from app.main import app

        with TestClient(app) as c:
            yield c


def test_post_rejects_invalid_url(client):
    r = client.post("/api/jobs", json={"url": "https://example.com/foo"})
    assert r.status_code == 422
    assert "unsupported host" in r.json()["detail"]


def test_post_rejects_empty_url(client):
    r = client.post("/api/jobs", json={"url": ""})
    assert r.status_code == 422


def test_post_accepts_youtube_url(client):
    r = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert r.status_code == 200
    assert "job_id" in r.json()
    assert len(r.json()["job_id"]) == 12


def test_post_accepts_quality_preset(client):
    r = client.post(
        "/api/jobs",
        json={"url": "https://youtu.be/dQw4w9WgXcQ", "quality_preset": "ultra"},
    )
    assert r.status_code == 200
    assert _jobs[r.json()["job_id"]].quality_preset == "ultra"


def test_post_accepts_stem_denoise_preset(client):
    r = client.post(
        "/api/jobs",
        json={"url": "https://youtu.be/dQw4w9WgXcQ", "stem_denoise": "strong"},
    )
    assert r.status_code == 200
    assert _jobs[r.json()["job_id"]].stem_denoise_preset == "strong"


def test_post_accepts_demucs_device_choice(client):
    r = client.post(
        "/api/jobs",
        json={"url": "https://youtu.be/dQw4w9WgXcQ", "demucs_device": "cpu"},
    )
    assert r.status_code == 200
    job = _jobs[r.json()["job_id"]]
    assert job.demucs_device == "cpu"
    assert job.demucs_device_resolved == "cpu"


def test_same_source_can_create_distinct_extraction_profiles(client):
    url = "https://youtu.be/dQw4w9WgXcQ"
    standard = client.post(
        "/api/jobs",
        json={
            "url": url,
            "quality_preset": "standard",
            "stem_denoise": "off",
            "demucs_device": "cpu",
            "stems": ["vocals", "drums", "bass", "other"],
        },
    )
    max_clean = client.post(
        "/api/jobs",
        json={
            "url": url,
            "quality_preset": "max",
            "stem_denoise": "strong",
            "demucs_device": "cpu",
            "stems": ["vocals", "bass"],
        },
    )

    assert standard.status_code == 200
    assert max_clean.status_code == 200
    first = _jobs[standard.json()["job_id"]]
    second = _jobs[max_clean.json()["job_id"]]

    assert first.id != second.id
    assert first.source_url == second.source_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert first.demucs_device == "cpu"
    assert first.demucs_device_resolved == "cpu"
    assert first.profile_key() == "quality=standard|denoise=off|device=cpu:cpu|stems=vocals,drums,bass,other"
    assert second.profile_key() == "quality=max|denoise=strong|device=cpu:cpu|stems=vocals,bass"


def test_post_invalid_stem_denoise_falls_back_to_off(client):
    r = client.post(
        "/api/jobs",
        json={"url": "https://youtu.be/dQw4w9WgXcQ", "stem_denoise": "destructive"},
    )
    assert r.status_code == 200
    assert _jobs[r.json()["job_id"]].stem_denoise_preset == "off"


def test_quality_preset_filters_unsupported_stems(client):
    r = client.post(
        "/api/jobs",
        json={
            "url": "https://youtu.be/dQw4w9WgXcQ",
            "quality_preset": "high",
            "stems": ["guitar", "piano"],
        },
    )
    assert r.status_code == 200
    assert _jobs[r.json()["job_id"]].selected_stems == ["vocals", "drums", "bass", "other"]


def test_probe_duration_falls_back_to_ffmpeg_when_ffprobe_missing(monkeypatch, tmp_path):
    import app.api.jobs as jobs

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"wav")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[0] == "ffprobe":
            raise FileNotFoundError("ffprobe")
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="Duration: 00:01:02.50, start: 0.000000, bitrate: 1411 kb/s\n",
        )

    monkeypatch.setattr(jobs, "ffprobe_executable", lambda: "ffprobe")
    monkeypatch.setattr(jobs, "ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(jobs.subprocess, "run", fake_run)

    assert jobs._probe_duration(audio) == 62.5
    assert calls[0][0][0] == "ffprobe"
    assert calls[1][0][:3] == ["ffmpeg", "-hide_banner", "-i"]


def test_get_unknown_job_returns_404(client):
    r = client.get("/api/jobs/000000000000")
    assert r.status_code == 404


def test_cancel_unknown_job_returns_404(client):
    r = client.post("/api/jobs/000000000000/cancel")
    assert r.status_code == 404


def test_delete_running_job_rejected(client):
    r = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    job_id = r.json()["job_id"]
    r = client.delete(f"/api/jobs/{job_id}")
    assert r.status_code == 409


def test_cancel_sets_flag_and_returns_state(client):
    r = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    job_id = r.json()["job_id"]
    r = client.post(f"/api/jobs/{job_id}/cancel")
    assert r.status_code == 200
    assert _jobs[job_id].cancel_requested is True


def test_cancel_after_done_is_idempotent(client):
    r = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    job_id = r.json()["job_id"]
    _jobs[job_id].status = "done"
    r = client.post(f"/api/jobs/{job_id}/cancel")
    assert r.status_code == 200
    assert _jobs[job_id].cancel_requested is False


# ─── Capacity (503) ───────────────────────────────────────────────────────────


def test_youtube_503_when_queue_full(client):
    for _ in range(MAX_PENDING_JOBS):
        r = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
        assert r.status_code == 200
    r = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert r.status_code == 503


def test_upload_503_when_queue_full(upload_client):
    for _ in range(MAX_PENDING_JOBS):
        r = upload_client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
        assert r.status_code == 200
    data = io.BytesIO(b"ID3" + b"\x00" * 128)
    r = upload_client.post(
        "/api/jobs",
        files={"file": ("track.mp3", data, "audio/mpeg")},
    )
    assert r.status_code == 503


def test_active_jobs_include_queue_positions(client):
    first = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    second = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"})

    assert first.status_code == 200
    assert second.status_code == 200

    r = client.get("/api/jobs/active")
    assert r.status_code == 200
    body = r.json()
    assert [item["job_id"] for item in body] == [first.json()["job_id"], second.json()["job_id"]]
    assert [item["queue_position"] for item in body] == [1, 2]
    assert [item["queue_size"] for item in body] == [2, 2]


def test_cancel_queued_job_opens_queue_slot(client):
    created = [
        client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"}).json()["job_id"]
        for _ in range(MAX_PENDING_JOBS)
    ]

    r = client.post(f"/api/jobs/{created[0]}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"

    replacement = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert replacement.status_code == 200


# ─── File upload ─────────────────────────────────────────────────────────────


def test_upload_rejects_unsupported_extension(upload_client):
    data = io.BytesIO(b"OGG data")
    r = upload_client.post(
        "/api/jobs",
        files={"file": ("track.ogg", data, "audio/ogg")},
    )
    assert r.status_code == 422
    assert "Unsupported file type" in r.json()["detail"]


def test_upload_rejects_empty_file(upload_client):
    r = upload_client.post(
        "/api/jobs",
        files={"file": ("track.wav", io.BytesIO(b""), "audio/wav")},
    )
    assert r.status_code == 422
    assert "empty" in r.json()["detail"].lower()


def test_upload_mp3_returns_job_id(upload_client):
    data = io.BytesIO(b"ID3" + b"\x00" * 128)
    r = upload_client.post(
        "/api/jobs",
        files={"file": ("my_track.mp3", data, "audio/mpeg")},
    )
    assert r.status_code == 200
    assert "job_id" in r.json()
    assert len(r.json()["job_id"]) == 12


def test_upload_accepts_quality_preset(upload_client):
    data = io.BytesIO(b"ID3" + b"\x00" * 128)
    r = upload_client.post(
        "/api/jobs",
        data={"quality_preset": "high"},
        files={"file": ("my_track.mp3", data, "audio/mpeg")},
    )
    assert r.status_code == 200
    assert _jobs[r.json()["job_id"]].quality_preset == "high"


def test_upload_accepts_stem_denoise_preset(upload_client):
    data = io.BytesIO(b"ID3" + b"\x00" * 128)
    r = upload_client.post(
        "/api/jobs",
        data={"stem_denoise": "light"},
        files={"file": ("my_track.mp3", data, "audio/mpeg")},
    )
    assert r.status_code == 200
    assert _jobs[r.json()["job_id"]].stem_denoise_preset == "light"


def test_upload_wav_returns_job_id(upload_client):
    data = io.BytesIO(b"RIFF" + b"\x00" * 128)
    r = upload_client.post(
        "/api/jobs",
        files={"file": ("my_track.wav", data, "audio/wav")},
    )
    assert r.status_code == 200
    assert "job_id" in r.json()


def test_upload_flac_returns_job_id(upload_client):
    data = io.BytesIO(b"fLaC" + b"\x00" * 128)
    r = upload_client.post(
        "/api/jobs",
        files={"file": ("my_track.flac", data, "audio/flac")},
    )
    assert r.status_code == 200
    assert "job_id" in r.json()


def test_upload_m4a_returns_job_id(upload_client):
    data = io.BytesIO(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 128)
    r = upload_client.post(
        "/api/jobs",
        files={"file": ("my_track.m4a", data, "audio/mp4")},
    )
    assert r.status_code == 200
    job = _jobs[r.json()["job_id"]]
    assert job.title == "my_track"
    assert job.source_url == "local:my_track"


# ─── Sections endpoint ────────────────────────────────────────────────────────


@pytest.fixture
def done_job(client, tmp_path, monkeypatch):
    import app.api.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "JOBS_DIR", tmp_path)
    job = Job(id="abcdefabcdef")
    job.status = "done"
    _jobs[job.id] = job
    job_dir = tmp_path / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job


def test_sections_happy_path(client, done_job, tmp_path):
    payload = {
        "sections": [{"id": "sec1", "name": "Verse", "start": 0.0, "end": 30.0, "color": "#ff0000"}]
    }
    r = client.patch(f"/api/jobs/{done_job.id}/sections", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == done_job.id
    assert len(body["sections"]) == 1
    assert body["sections"][0]["name"] == "Verse"
    # Verify written to disk
    meta_path = tmp_path / done_job.id / "metadata.json"
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text())
    assert meta["sections"][0]["id"] == "sec1"


def test_sections_unknown_job_returns_404(client):
    payload = {"sections": []}
    r = client.patch("/api/jobs/000000000000/sections", json=payload)
    assert r.status_code == 404


def test_sections_malformed_job_id_returns_404(client):
    # Job IDs must be 12 lowercase hex chars; anything else is rejected.
    r = client.patch("/api/jobs/BADID/sections", json={"sections": []})
    assert r.status_code == 404


def test_sections_invalid_color_returns_422(client, done_job):
    payload = {
        "sections": [
            {"id": "sec1", "name": "Intro", "start": 0.0, "end": 10.0, "color": "not-a-color"}
        ]
    }
    r = client.patch(f"/api/jobs/{done_job.id}/sections", json=payload)
    assert r.status_code == 422


def test_sections_invalid_id_returns_422(client, done_job):
    payload = {
        "sections": [{"id": "has space", "name": "x", "start": 0.0, "end": 5.0, "color": "#fff"}]
    }
    r = client.patch(f"/api/jobs/{done_job.id}/sections", json=payload)
    assert r.status_code == 422


# ─── SSE job_id validation ────────────────────────────────────────────────────


def test_sse_rejects_malformed_job_id(client):
    for bad_id in ("../etc", "ABC", "abcdefabcdef0"):
        r = client.get(f"/api/jobs/{bad_id}/events")
        assert r.status_code == 404, f"SSE should 404 for id {bad_id!r}"


def test_sse_503_when_connection_cap_reached(client):
    """#86/#88: SSE endpoint rejects with 503 when _MAX_SSE_CONNECTIONS is reached."""
    import app.api.events as events_mod

    original = events_mod._sse_active
    try:
        events_mod._sse_active = events_mod._MAX_SSE_CONNECTIONS
        job = Job(id="abcdefabcdef")
        job.status = "done"
        _jobs[job.id] = job
        r = client.get(f"/api/jobs/{job.id}/events")
        assert r.status_code == 503
    finally:
        events_mod._sse_active = original
