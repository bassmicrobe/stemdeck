from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.models import Job
from app.core.registry import _jobs
from app.core.registry import persist as persist_registry
from app.core.registry import restore as restore_registry


@pytest.fixture(autouse=True)
def _isolate_registry():
    _jobs.clear()
    yield
    _jobs.clear()


def test_persist_and_restore_terminal_job(tmp_path: Path):
    job = Job(
        id="abcdefabcdef",
        status="done",
        progress=1.0,
        stage_message="Done",
        title="Saved song",
        stems=[{"name": "vocals", "url": "/api/jobs/abcdefabcdef/stems/vocals.wav"}],
        selected_stems=["vocals"],
    )
    _jobs[job.id] = job

    persist_registry(tmp_path)
    _jobs.clear()
    restore_registry(tmp_path)

    restored = _jobs[job.id]
    assert restored.status == "done"
    assert restored.title == "Saved song"
    assert restored.stems == job.stems
    assert restored.cancel_requested is False


def test_persist_excludes_transient_queue_fields(tmp_path: Path):
    job = Job(
        id="abcdefabcdea",
        status="done",
        title="Saved song",
        queue_position=1,
        queue_size=2,
    )
    _jobs[job.id] = job

    persist_registry(tmp_path)

    data = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert "queue_position" not in data["jobs"][0]
    assert "queue_size" not in data["jobs"][0]


def test_persist_and_restore_failed_job_logs(tmp_path: Path):
    job = Job(
        id="abcdefabcde1",
        status="error",
        title="Failed song",
        error="Processing failed",
        logs=[
            {
                "id": 1,
                "timestamp": 1_700_000_000.0,
                "job_id": "abcdefabcde1",
                "level": "error",
                "message": "demucs failed",
                "stage": "error",
                "progress_percent": 82,
            }
        ],
    )
    _jobs[job.id] = job

    persist_registry(tmp_path)
    _jobs.clear()
    restore_registry(tmp_path)

    restored = _jobs[job.id]
    assert restored.status == "error"
    assert restored.logs[0]["message"] == "demucs failed"


def test_persist_failure_is_non_fatal(tmp_path: Path, monkeypatch):
    import app.core.registry as registry

    job = Job(id="abcdefabcde2", status="done", title="Saved song")
    _jobs[job.id] = job
    monkeypatch.setattr(
        registry,
        "atomic_write_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    persist_registry(tmp_path)


def test_restore_recovers_orphan_done_job_from_stems(tmp_path: Path):
    job_dir = tmp_path / "abcdefabcdee"
    stems_dir = job_dir / "stems"
    stems_dir.mkdir(parents=True)
    (stems_dir / "vocals.wav").write_bytes(b"RIFF")
    (stems_dir / "drums.wav").write_bytes(b"RIFF")
    (stems_dir / "chords.mid").write_bytes(b"MThd")
    (job_dir / "metadata.json").write_text(
        json.dumps(
            {
                "title": "Test Song",
                "bass_repair_applied": True,
                "phase_repair_applied": True,
                "phase_repair_residual_ratio": 0.37,
                "stem_denoise_preset": "light",
                "stem_denoise_applied": True,
                "stem_gate_applied": True,
                "stem_gate_threshold_db": -54.0,
                "beat_times": [0.5, 1.0, 1.5],
                "chord_progression": [
                    {"label": "C", "start": 0.5, "end": 1.5, "confidence": 0.9}
                ],
                "selected_stems": ["vocals"],
                "source_url": "local:Test Song.wav",
                "processing_started_at": 1_699_999_876.6,
                "completed_at": 1_700_000_000.0,
                "processing_elapsed_seconds": 123.4,
            }
        ),
        encoding="utf-8",
    )

    restore_registry(tmp_path)

    restored = _jobs["abcdefabcdee"]
    assert restored.status == "done"
    assert restored.progress == 1.0
    assert restored.title == "Test Song"
    assert {stem["name"] for stem in restored.stems} == {"vocals", "drums"}
    assert restored.bass_repair_applied is True
    assert restored.phase_repair_applied is True
    assert restored.phase_repair_residual_ratio == 0.37
    assert restored.stem_denoise_preset == "light"
    assert restored.stem_denoise_applied is True
    assert restored.stem_gate_applied is True
    assert restored.stem_gate_threshold_db == -54.0
    assert restored.beat_times == [0.5, 1.0, 1.5]
    assert restored.chord_progression == [
        {"label": "C", "start": 0.5, "end": 1.5, "confidence": 0.9}
    ]
    assert restored.chord_midi_url == "/api/jobs/abcdefabcdee/chords.mid"
    assert restored.selected_stems == ["vocals"]
    assert restored.source_url == "local:Test Song.wav"
    assert restored.processing_started_at == 1_699_999_876.6
    assert restored.completed_at == 1_700_000_000.0
    assert restored.processing_elapsed_seconds == 123.4


def test_restore_skips_orphan_without_metadata(tmp_path: Path):
    stems_dir = tmp_path / "abcdefabcde0" / "stems"
    stems_dir.mkdir(parents=True)
    (stems_dir / "vocals.wav").write_bytes(b"RIFF")

    restore_registry(tmp_path)

    assert "abcdefabcde0" not in _jobs


def test_restored_job_serves_stems(tmp_path: Path, monkeypatch):
    stems_dir = tmp_path / "abcdefabcded" / "stems"
    stems_dir.mkdir(parents=True)
    (stems_dir / "vocals.wav").write_bytes(b"RIFF1234")
    data = {
        "version": 1,
        "jobs": [
            Job(
                id="abcdefabcded",
                status="done",
                title="Test Song",
                stems=[{"name": "vocals", "url": "/api/jobs/abcdefabcded/stems/vocals.wav"}],
                selected_stems=["vocals"],
            ).to_record()
        ],
    }
    (tmp_path / "registry.json").write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr("app.api.stems.JOBS_DIR", tmp_path)
    restore_registry(tmp_path)

    from app.main import app

    with TestClient(app) as client:
        state = client.get("/api/jobs/abcdefabcded")
        assert state.status_code == 200
        assert state.json()["status"] == "done"
        stem = client.get("/api/jobs/abcdefabcded/stems/vocals.wav")
        assert stem.status_code == 200
        assert stem.content == b"RIFF1234"


def test_delete_updates_persisted_registry(tmp_path: Path, monkeypatch):
    job = Job(id="abcdefabcdec", status="done")
    _jobs[job.id] = job
    job_dir = tmp_path / job.id
    job_dir.mkdir(parents=True)
    persist_registry(tmp_path)

    monkeypatch.setattr("app.api.jobs.JOBS_DIR", tmp_path)

    from app.main import app

    with TestClient(app) as client:
        response = client.delete(f"/api/jobs/{job.id}")

    assert response.status_code == 200
    assert not job_dir.exists()
    data = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert data["jobs"] == []
