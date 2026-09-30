from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.models import Job
from app.pipeline.process import ProcessResult


def test_pcm_worker_executable_prefers_explicit_path(tmp_path: Path, monkeypatch):
    worker = tmp_path / "layerlab-pcm"
    worker.write_bytes(b"worker")
    worker.chmod(0o755)
    monkeypatch.setenv("LAYERLAB_PCM_WORKER", str(worker))

    from app.pipeline import pcm_worker

    assert pcm_worker.pcm_worker_executable() == worker.resolve()


def test_run_pcm_command_tracks_process_and_records_engine(tmp_path: Path, monkeypatch):
    from app.pipeline import pcm_worker

    worker = tmp_path / "layerlab-pcm"
    worker.write_bytes(b"worker")
    worker.chmod(0o755)
    captured: dict[str, object] = {}

    def fake_run(job, cmd, *, timeout, input_data, **_kwargs):
        captured.update(job=job, cmd=cmd, timeout=timeout, input_data=input_data)
        body = {
            "ok": True,
            "engine": "layerlab-rust-pcm-v1",
            "files": [
                {
                    "path": "/tmp/input.wav",
                    "output": "/tmp/output.wav",
                    "changed": True,
                }
            ],
        }
        return ProcessResult(0, json.dumps(body).encode(), b"")

    monkeypatch.setattr(pcm_worker, "PCM_WORKER_ENABLED", True)
    monkeypatch.setattr(pcm_worker, "pcm_worker_executable", lambda: worker)
    monkeypatch.setattr(pcm_worker, "run_tracked_process", fake_run)
    job = Job(id="abcdefabcdef")

    response = pcm_worker.run_pcm_command(
        job,
        {"operation": "gate", "files": []},
        timeout=12.0,
    )

    assert response is not None
    assert response.engine == "layerlab-rust-pcm-v1"
    assert response.files[0].changed is True
    assert job.pcm_engine == "layerlab-rust-pcm-v1"
    assert captured["cmd"] == [str(worker)]
    assert captured["timeout"] == 12.0
    assert json.loads(captured["input_data"].decode()) == {
        "operation": "gate",
        "files": [],
    }


def test_run_pcm_command_rejects_invalid_response_and_preserves_job(tmp_path: Path, monkeypatch):
    from app.pipeline import pcm_worker

    worker = tmp_path / "layerlab-pcm"
    worker.write_bytes(b"worker")
    worker.chmod(0o755)
    monkeypatch.setattr(pcm_worker, "PCM_WORKER_ENABLED", True)
    monkeypatch.setattr(pcm_worker, "pcm_worker_executable", lambda: worker)
    monkeypatch.setattr(
        pcm_worker,
        "run_tracked_process",
        lambda *_args, **_kwargs: ProcessResult(0, b'{"ok":true,"engine":""}', b""),
    )
    job = Job(id="abcdefabcdef")

    assert pcm_worker.run_pcm_command(job, {"operation": "ping"}) is None
    assert job.pcm_engine == ""


def test_run_pcm_command_falls_back_when_worker_is_missing(monkeypatch):
    from app.pipeline import pcm_worker

    monkeypatch.setattr(pcm_worker, "PCM_WORKER_ENABLED", True)
    monkeypatch.setattr(pcm_worker, "pcm_worker_executable", lambda: None)

    assert pcm_worker.run_pcm_command(Job(id="abcdefabcdef"), {"operation": "ping"}) is None


def test_pcm_response_rejects_non_finite_analysis_values():
    from app.pipeline import pcm_worker

    body = {
        "ok": True,
        "engine": "layerlab-rust-pcm-v1",
        "analyses": [
            {
                "path": "/tmp/input.wav",
                "sampleRate": 44100,
                "channels": 2,
                "durationSeconds": 1.0,
                "peak": float("nan"),
                "rms": 0.1,
                "waveformPeaks": [],
                "minMaxPeaks": [],
            }
        ],
    }

    with pytest.raises(ValueError, match="must be finite"):
        pcm_worker._parse_response(json.dumps(body).encode())
