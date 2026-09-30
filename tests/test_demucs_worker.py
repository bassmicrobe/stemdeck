from __future__ import annotations

import queue
import time
from collections import deque
from pathlib import Path

import pytest
import soundfile as sf
import torch

import app.pipeline.separate as separate_mod
from app.core.models import Job


def test_separate_prefers_persistent_worker(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")
    expected = tmp_path / "htdemucs_6s" / "source"
    expected.mkdir(parents=True)
    calls = []

    def fake_worker(job, source_path, job_dir, settings, device):
        calls.append((job, source_path, job_dir, settings.model, device))
        return expected

    monkeypatch.setattr(separate_mod, "DEMUCS_PERSISTENT_WORKER", True)
    monkeypatch.setattr(separate_mod, "separate_with_worker", fake_worker)
    monkeypatch.setattr(
        separate_mod,
        "_separate_cli",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("CLI should not run")),
    )
    job = Job(id="abcdefabcdef", demucs_device_resolved="cpu")

    assert separate_mod.separate(job, source, tmp_path) == expected
    assert calls[0][1:] == (source, tmp_path, "htdemucs_6s", "cpu")
    assert job.demucs_engine == "persistent-worker"


def test_separate_falls_back_to_cli_only_when_worker_is_unavailable(monkeypatch, tmp_path: Path):
    from app.pipeline.demucs_pool import WorkerUnavailable

    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")
    expected = tmp_path / "cli-output"
    expected.mkdir()
    monkeypatch.setattr(separate_mod, "DEMUCS_PERSISTENT_WORKER", True)
    monkeypatch.setattr(
        separate_mod,
        "separate_with_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(WorkerUnavailable("no protocol")),
    )
    monkeypatch.setattr(separate_mod, "_separate_cli", lambda *_args, **_kwargs: expected)
    job = Job(id="abcdefabcdef", demucs_device_resolved="cpu")

    assert separate_mod.separate(job, source, tmp_path) == expected
    assert job.demucs_engine == "cli-fallback"


def test_separate_does_not_repeat_inference_after_worker_job_error(monkeypatch, tmp_path: Path):
    from app.pipeline.demucs_pool import WorkerJobError

    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")
    monkeypatch.setattr(separate_mod, "DEMUCS_PERSISTENT_WORKER", True)
    monkeypatch.setattr(
        separate_mod,
        "separate_with_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(WorkerJobError("out of memory")),
    )
    monkeypatch.setattr(
        separate_mod,
        "_separate_cli",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not rerun")),
    )

    with pytest.raises(WorkerJobError, match="out of memory"):
        separate_mod.separate(Job(id="abcdefabcdef"), source, tmp_path)


def test_progress_reporter_emits_monotonic_combined_passes():
    from app.pipeline.demucs_worker import ProgressReporter

    events = []
    reporter = ProgressReporter("job-1", shifts=2, model_count=2, emitter=events.append)

    list(reporter.wrap(["a", "b"]))
    list(reporter.wrap(["c", "d"]))

    fractions = [event["fraction"] for event in events]
    assert fractions == sorted(fractions)
    assert fractions[-1] == pytest.approx(0.5)
    assert events[-1]["passIndex"] == 2
    assert events[-1]["passTotal"] == 4


def test_worker_compatibility_rejects_unpinned_demucs(monkeypatch):
    from app.pipeline import demucs_worker

    monkeypatch.setattr(demucs_worker, "package_version", lambda _name: "4.1.0")

    with pytest.raises(RuntimeError, match="requires Demucs 4.0.1"):
        demucs_worker.validate_demucs_compatibility()


def test_heartbeat_emits_until_context_exits():
    from app.pipeline.demucs_worker import inference_heartbeat

    events = []
    with inference_heartbeat("job-1", events.append, interval=0.01):
        time.sleep(0.035)

    heartbeat_count = sum(event.get("event") == "heartbeat" for event in events)
    assert heartbeat_count >= 2
    stopped_at = len(events)
    time.sleep(0.025)
    assert len(events) == stopped_at


def test_worker_pool_keeps_gpu_capacity_at_one(monkeypatch):
    from app.pipeline import demucs_pool

    monkeypatch.setattr(demucs_pool, "PIPELINE_CONCURRENCY", 4)
    pool = demucs_pool.DemucsWorkerPool()

    assert pool._capacity_for("cpu") == 4
    assert pool._capacity_for("mps") == 1
    assert pool._capacity_for("cuda") == 1
    pool.shutdown()


def test_worker_pool_reaps_expired_idle_worker(monkeypatch):
    from app.pipeline import demucs_pool

    class FakeProcess:
        def poll(self):
            return None

    class FakeWorker:
        busy = False
        last_used = 10.0
        proc = FakeProcess()

        def __init__(self):
            self.shutdown_called = False

        def shutdown(self):
            self.shutdown_called = True

    worker = FakeWorker()
    monkeypatch.setattr(demucs_pool, "DEMUCS_WORKER_IDLE_TTL", 30)
    pool = demucs_pool.DemucsWorkerPool(start_reaper=False)
    pool._workers.append(worker)

    assert pool.reap_idle_workers(now=41.0) == 1
    assert worker.shutdown_called is True
    assert pool.status()["workers"] == 0
    pool.shutdown()


def test_worker_pool_reaps_idle_worker_under_memory_pressure(monkeypatch):
    from app.pipeline import demucs_pool

    class FakeProcess:
        def poll(self):
            return None

    class FakeWorker:
        busy = False
        last_used = 40.0
        proc = FakeProcess()

        def __init__(self):
            self.shutdown_called = False

        def shutdown(self):
            self.shutdown_called = True

    worker = FakeWorker()
    monkeypatch.setattr(demucs_pool, "DEMUCS_WORKER_IDLE_TTL", 600)
    monkeypatch.setattr(demucs_pool, "DEMUCS_WORKER_MIN_FREE_MEMORY_GB", 4.0)
    monkeypatch.setattr(demucs_pool, "available_memory_gb", lambda: 1.0)
    pool = demucs_pool.DemucsWorkerPool(start_reaper=False)
    pool._workers.append(worker)

    assert pool.reap_idle_workers(now=41.0) == 1
    assert worker.shutdown_called is True
    pool.shutdown()


def test_worker_process_death_during_inference_is_not_retryable():
    from app.pipeline.demucs_pool import PersistentDemucsWorker, WorkerJobError

    class DeadProcess:
        returncode = 9

        def poll(self):
            return 9

    worker = PersistentDemucsWorker.__new__(PersistentDemucsWorker)
    worker.proc = DeadProcess()
    worker.messages = queue.Queue()
    worker.tail = deque(["worker killed"], maxlen=40)
    worker.last_activity = time.monotonic()

    with pytest.raises(WorkerJobError, match="exited with 9"):
        worker._next_event(
            Job(id="abcdefabcdef"),
            time.monotonic(),
            inference_started=True,
        )


def test_worker_pool_switches_models_by_releasing_idle_worker(monkeypatch):
    from app.core.registry import remove_proc
    from app.pipeline import demucs_pool

    class FakeProcess:
        returncode = None
        pid = 12345

        def poll(self):
            return self.returncode

    class FakeWorker:
        def __init__(self, model, device):
            self.model = model
            self.device = device
            self.busy = True
            self.ready = True
            self.proc = FakeProcess()
            self.last_used = time.monotonic()
            self.shutdown_called = False

        def ensure_ready(self, _job):
            return None

        def shutdown(self):
            self.shutdown_called = True
            self.proc.returncode = 0

        def terminate(self, *, force=False):
            del force
            self.proc.returncode = 0

    old = FakeWorker("old-model", "cpu")
    old.busy = False
    monkeypatch.setattr(demucs_pool, "PIPELINE_CONCURRENCY", 1)
    monkeypatch.setattr(demucs_pool, "PersistentDemucsWorker", FakeWorker)
    pool = demucs_pool.DemucsWorkerPool(start_reaper=False)
    pool._workers.append(old)
    job = Job(id="abcdefabcdef")

    worker = pool.acquire(job, "new-model", "cpu")

    assert old.shutdown_called is True
    assert worker.model == "new-model"
    remove_proc(job.id, worker.proc)
    pool.release(worker, healthy=True)
    pool.shutdown()


def test_separate_track_matches_demucs_cli_normalization_and_output_tree(
    monkeypatch,
    tmp_path: Path,
):
    from app.pipeline import demucs_worker

    class FakeModel:
        audio_channels = 2
        samplerate = 44100
        sources = ("vocals", "other")

    source = tmp_path / "source.demucs.wav"
    source.write_bytes(b"wav")
    waveform = torch.tensor([[1.0, 2.0, 3.0], [2.0, 3.0, 4.0]])
    captured = {}

    monkeypatch.setattr(demucs_worker, "load_track", lambda *_args: waveform.clone())

    def fake_apply(_model, mix, **kwargs):
        captured.update(mix=mix.clone(), kwargs=kwargs)
        return torch.zeros((1, 2, 2, 3), dtype=torch.float32)

    def fake_save(wav, path, **kwargs):
        captured.setdefault("saved", []).append((wav.clone(), Path(path), kwargs))
        Path(path).write_bytes(b"stem")

    monkeypatch.setattr(demucs_worker, "apply_model", fake_apply)
    monkeypatch.setattr(demucs_worker, "save_audio", fake_save)
    events = []
    request = {
        "requestId": "job-1",
        "source": str(source),
        "jobDir": str(tmp_path),
        "shifts": 2,
        "overlap": 0.25,
        "segment": 0.0,
        "jobs": 0,
        "float32": True,
        "clipMode": "rescale",
    }

    output = demucs_worker.separate_track(
        FakeModel(),
        "htdemucs_6s",
        "cpu",
        request,
        emitter=events.append,
    )

    assert output == tmp_path / "htdemucs_6s" / "source.demucs"
    assert (output / "vocals.wav").read_bytes() == b"stem"
    assert (output / "other.wav").read_bytes() == b"stem"
    assert torch.isfinite(captured["mix"]).all()
    assert captured["kwargs"]["shifts"] == 2
    assert captured["kwargs"]["overlap"] == 0.25
    assert captured["kwargs"]["device"] == "cpu"
    assert all(item[2]["as_float"] is True for item in captured["saved"])


def test_pool_reuses_same_process_for_two_tracks(tmp_path: Path):
    from app.core.config import DemucsSettings
    from app.core.registry import remove_proc
    from app.pipeline.demucs_pool import DemucsWorkerPool

    source = tmp_path / "source.wav"
    sf.write(source, torch.zeros((4410, 2)).numpy(), 44100, subtype="FLOAT")
    settings = DemucsSettings(
        quality_preset="standard",
        model="demucs_unittest",
        shifts=0,
        pre_gain_db=0.0,
        float32=True,
        clip_mode="rescale",
        overlap=0.2,
        segment=1.0,
        jobs=0,
    )
    pool = DemucsWorkerPool()
    first_job = Job(id="abcdefabcdef", demucs_device_resolved="cpu")
    first_worker = pool.acquire(first_job, settings.model, "cpu")
    first_pid = first_worker.proc.pid
    try:
        first_output = first_worker.separate(first_job, source, tmp_path / "first", settings)
    finally:
        remove_proc(first_job.id, first_worker.proc)
        pool.release(first_worker, healthy=first_worker.proc.poll() is None)

    second_job = Job(id="123456abcdef", demucs_device_resolved="cpu")
    second_worker = pool.acquire(second_job, settings.model, "cpu")
    try:
        second_output = second_worker.separate(second_job, source, tmp_path / "second", settings)
    finally:
        remove_proc(second_job.id, second_worker.proc)
        pool.release(second_worker, healthy=second_worker.proc.poll() is None)
        pool.shutdown()

    assert first_worker is second_worker
    assert second_worker.proc.pid == first_pid
    assert {path.name for path in first_output.glob("*.wav")} == {
        "drums.wav",
        "bass.wav",
        "other.wav",
        "vocals.wav",
    }
    assert {path.name for path in second_output.glob("*.wav")} == {
        "drums.wav",
        "bass.wav",
        "other.wav",
        "vocals.wav",
    }
