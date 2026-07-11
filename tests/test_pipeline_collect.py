from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.core.models import Job
from app.pipeline.collect import (
    _PEAK_POINTS,
    PhaseRepairResult,
    _blend_bass_dropout_repair,
    _blend_phase_residual,
    _write_bass_residual_candidate,
    collect,
    compute_stem_peaks,
    denoise_stem_outputs,
    gate_stem_outputs,
    make_selected_mix,
    repair_bass_dropouts,
    repair_phase_coherence,
    restore_demucs_gain,
    stabilize_stem_outputs,
)


def _write_wav(path: Path, samples: list[float], sample_rate: int = 44100) -> None:
    """Write a mono 16-bit PCM WAV file."""
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        data = struct.pack(f"<{len(samples)}h", *[int(s * 32767) for s in samples])
        wf.writeframes(data)


def test_produces_peaks_json(tmp_path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()

    # 1-second sine wave at 440 Hz
    sr = 44100
    t = np.linspace(0, 1, sr, endpoint=False)
    samples = (np.sin(2 * np.pi * 440 * t) * 0.5).tolist()
    _write_wav(stems_dir / "vocals.wav", samples, sr)

    compute_stem_peaks(stems_dir, ["vocals"])

    peaks_path = stems_dir / "peaks.json"
    assert peaks_path.is_file()
    data = json.loads(peaks_path.read_text())
    assert "vocals" in data
    pts = data["vocals"]
    assert len(pts) <= _PEAK_POINTS
    assert len(pts) > 0
    # each point is [min, max] with min <= 0 <= max (sine wave)
    for mn, mx in pts:
        assert mn <= mx
        assert -1.0 <= mn <= 1.0
        assert -1.0 <= mx <= 1.0


def test_multiple_stems(tmp_path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    for name in ("vocals", "drums", "bass"):
        _write_wav(stems_dir / f"{name}.wav", [0.1, -0.1, 0.2, -0.2])

    compute_stem_peaks(stems_dir, ["vocals", "drums", "bass"])

    data = json.loads((stems_dir / "peaks.json").read_text())
    assert set(data.keys()) == {"vocals", "drums", "bass"}


def test_peaks_include_tail_and_both_channels(tmp_path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    frames = _PEAK_POINTS + 1
    stereo = np.zeros((frames, 2), dtype=np.float32)
    stereo[-1, 1] = 0.9
    sf.write(stems_dir / "other.wav", stereo, 44100, subtype="FLOAT")

    compute_stem_peaks(stems_dir, ["other"])

    points = json.loads((stems_dir / "peaks.json").read_text())["other"]
    assert len(points) <= _PEAK_POINTS
    assert points[-1][1] == pytest.approx(0.9)


def test_skips_missing_wav(tmp_path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    _write_wav(stems_dir / "drums.wav", [0.1, -0.1])
    # "vocals.wav" intentionally absent

    compute_stem_peaks(stems_dir, ["vocals", "drums"])

    data = json.loads((stems_dir / "peaks.json").read_text())
    assert "drums" in data
    assert "vocals" not in data


def test_no_output_when_all_stems_missing(tmp_path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()

    compute_stem_peaks(stems_dir, ["vocals", "drums"])

    assert not (stems_dir / "peaks.json").exists()


def test_collect_reports_post_demucs_progress(tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    stems_root = tmp_path / "demucs-out"
    stems_root.mkdir(parents=True)
    for name in ("vocals", "drums", "bass"):
        (stems_root / f"{name}.wav").write_bytes(b"wav")
    job = Job(id="abcdefabcdef", progress=0.82)

    found = collect(job, stems_root, job_dir)

    assert found == ["vocals", "drums", "bass"]
    assert job.progress > 0.82
    assert job.stage_message == "Cleaning separation workspace..."


def test_writes_atomically(tmp_path):
    """No partial peaks.json.tmp should survive a successful run."""
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    _write_wav(stems_dir / "vocals.wav", [0.1, -0.1, 0.3])

    compute_stem_peaks(stems_dir, ["vocals"])

    assert (stems_dir / "peaks.json").is_file()
    assert not (stems_dir / "peaks.json.tmp").exists()


def test_non_fatal_on_corrupt_wav(tmp_path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    (stems_dir / "vocals.wav").write_bytes(b"not a wav file at all")
    _write_wav(stems_dir / "drums.wav", [0.1, -0.1])

    # Should not raise; drums should still be computed
    compute_stem_peaks(stems_dir, ["vocals", "drums"])

    data = json.loads((stems_dir / "peaks.json").read_text())
    assert "drums" in data
    assert "vocals" not in data


def test_restore_demucs_gain_applies_inverse_pregain(tmp_path, monkeypatch):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    stem = stems_dir / "vocals.wav"
    stem.write_bytes(b"wav")
    calls = []

    def fake_run_ffmpeg(job, cmd):
        calls.append((job, cmd))
        Path(cmd[-1]).write_bytes(b"boosted")
        return True

    import app.pipeline.collect as collect_mod

    monkeypatch.setattr(collect_mod, "_run_ffmpeg", fake_run_ffmpeg)
    job = Job(id="abcdefabcdef", quality_preset="high")

    restore_demucs_gain(job, stems_dir, ["vocals"])

    assert stem.read_bytes() == b"boosted"
    _, cmd = calls[0]
    assert cmd[cmd.index("-filter:a") : cmd.index("-filter:a") + 2] == [
        "-filter:a",
        "volume=6dB",
    ]
    assert cmd[cmd.index("-c:a") : cmd.index("-c:a") + 2] == ["-c:a", "pcm_f32le"]


def test_restore_demucs_gain_uses_actual_adaptive_gain(tmp_path, monkeypatch):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    stem = stems_dir / "bass.wav"
    stem.write_bytes(b"wav")
    calls = []

    def fake_run_ffmpeg(job, cmd):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"boosted")
        return True

    import app.pipeline.collect as collect_mod

    monkeypatch.setattr(collect_mod, "_run_ffmpeg", fake_run_ffmpeg)
    job = Job(id="abcdefabcdef", quality_preset="high", demucs_gain_db=-11.0)

    restore_demucs_gain(job, stems_dir, ["bass"])

    assert stem.read_bytes() == b"boosted"
    cmd = calls[0]
    assert cmd[cmd.index("-filter:a") : cmd.index("-filter:a") + 2] == [
        "-filter:a",
        "volume=11dB",
    ]


def test_restore_demucs_gain_noops_without_pregain(tmp_path, monkeypatch):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    (stems_dir / "vocals.wav").write_bytes(b"wav")

    import app.pipeline.collect as collect_mod

    monkeypatch.setattr(
        collect_mod,
        "_run_ffmpeg",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected ffmpeg")),
    )

    restore_demucs_gain(Job(id="abcdefabcdef"), stems_dir, ["vocals"])


def test_high_quality_mix_uses_float32_wav_codec(tmp_path, monkeypatch):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    for name in ("vocals", "drums"):
        (stems_dir / f"{name}.wav").write_bytes(b"wav")
    calls = []

    def fake_run_ffmpeg(job, cmd):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"mix")
        return True

    import app.pipeline.collect as collect_mod

    monkeypatch.setattr(collect_mod, "_run_ffmpeg", fake_run_ffmpeg)
    job = Job(id="abcdefabcdef", quality_preset="high", selected_stems=["vocals", "drums"])

    out = make_selected_mix(job, stems_dir, ["vocals", "drums"])

    assert out == stems_dir / "mix.wav"
    cmd = calls[0]
    assert cmd[cmd.index("-c:a") : cmd.index("-c:a") + 2] == ["-c:a", "pcm_f32le"]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "alimiter=limit=0.98" in graph
    assert "level=0" in graph
    assert "latency=1" in graph


def test_denoise_stem_outputs_replaces_all_stems_after_success(tmp_path, monkeypatch):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    for name in ("vocals", "drums"):
        (stems_dir / f"{name}.wav").write_bytes(f"old-{name}".encode())
    calls = []

    def fake_run_ffmpeg(job, cmd):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"clean")
        return True

    import app.pipeline.collect as collect_mod

    monkeypatch.setattr(collect_mod, "_run_ffmpeg", fake_run_ffmpeg)
    job = Job(id="abcdefabcdef", quality_preset="high", stem_denoise_preset="light")

    assert denoise_stem_outputs(job, stems_dir, ["vocals", "drums"])

    assert (stems_dir / "vocals.wav").read_bytes() == b"clean"
    assert (stems_dir / "drums.wav").read_bytes() == b"clean"
    assert job.progress >= 0.95
    assert len(calls) == 2
    first = calls[0]
    assert "afftdn=" in first[first.index("-filter:a") + 1]
    assert first[first.index("-c:a") : first.index("-c:a") + 2] == ["-c:a", "pcm_f32le"]
    assert not list(stems_dir.glob("*.denoise.wav"))


def test_denoise_stem_outputs_preserves_originals_on_failure(tmp_path, monkeypatch):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    vocals = stems_dir / "vocals.wav"
    drums = stems_dir / "drums.wav"
    vocals.write_bytes(b"old-vocals")
    drums.write_bytes(b"old-drums")

    def fake_run_ffmpeg(job, cmd):
        Path(cmd[-1]).write_bytes(b"partial")
        return False

    import app.pipeline.collect as collect_mod

    monkeypatch.setattr(collect_mod, "_run_ffmpeg", fake_run_ffmpeg)
    job = Job(id="abcdefabcdef", stem_denoise_preset="strong")

    assert not denoise_stem_outputs(job, stems_dir, ["vocals", "drums"])

    assert vocals.read_bytes() == b"old-vocals"
    assert drums.read_bytes() == b"old-drums"
    assert not list(stems_dir.glob("*.denoise.wav"))


def test_denoise_stem_outputs_noops_when_off(tmp_path, monkeypatch):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    (stems_dir / "vocals.wav").write_bytes(b"wav")

    import app.pipeline.collect as collect_mod

    monkeypatch.setattr(
        collect_mod,
        "_run_ffmpeg",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected denoise")),
    )

    assert not denoise_stem_outputs(Job(id="abcdefabcdef"), stems_dir, ["vocals"])


def test_gate_stem_outputs_mutes_near_silent_regions_without_shortening(tmp_path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    sr = 44100
    t = np.arange(sr // 2, dtype=np.float32) / sr
    quiet = np.full((sr // 2, 2), 1e-5, dtype=np.float32)
    tone = (np.sin(2 * np.pi * 220 * t) * 0.2).astype(np.float32)
    tone = np.column_stack((tone, tone))
    samples = np.vstack((quiet, tone, quiet))
    sf.write(stems_dir / "vocals.wav", samples, sr, subtype="FLOAT")

    job = Job(id="abcdefabcdef", quality_preset="high")

    assert gate_stem_outputs(job, stems_dir, ["vocals"])

    processed, out_sr = sf.read(stems_dir / "vocals.wav", dtype="float32", always_2d=True)
    assert out_sr == sr
    assert len(processed) == len(samples)
    assert float(np.max(np.abs(processed[: sr // 4]), initial=0.0)) < 1e-6
    assert float(np.mean(np.abs(processed[sr // 2 : sr]))) > 0.05
    assert job.stem_gate_threshold_db == -54.0
    assert job.progress >= 0.96


def test_gate_stem_outputs_noops_when_disabled(tmp_path, monkeypatch):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    samples = np.full((1024, 2), 1e-5, dtype=np.float32)
    sf.write(stems_dir / "vocals.wav", samples, 44100, subtype="FLOAT")
    monkeypatch.setenv("STEMDECK_STEM_GATE", "0")

    assert not gate_stem_outputs(Job(id="abcdefabcdef", quality_preset="high"), stems_dir, ["vocals"])

    processed, _ = sf.read(stems_dir / "vocals.wav", dtype="float32", always_2d=True)
    np.testing.assert_allclose(processed, samples, atol=1e-7)


def test_bass_residual_candidate_subtracts_non_bass_stems(tmp_path, monkeypatch):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    for name in ("bass", "drums", "vocals"):
        (stems_dir / f"{name}.wav").write_bytes(b"wav")
    calls = []

    def fake_run_ffmpeg(job, cmd):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"residual")
        return True

    import app.pipeline.collect as collect_mod

    monkeypatch.setattr(collect_mod, "_run_ffmpeg", fake_run_ffmpeg)
    out = stems_dir / "bass.residual.wav"

    assert _write_bass_residual_candidate(
        Job(id="abcdefabcdef", quality_preset="high"),
        source,
        stems_dir,
        ["bass", "drums", "vocals"],
        out,
    )

    cmd = calls[0]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[1:a]volume=-1.0[neg1]" in graph
    assert "[2:a]volume=-1.0[neg2]" in graph
    assert "amix=inputs=3:normalize=0" in graph
    assert "lowpass=f=" in graph
    assert str(stems_dir / "bass.wav") not in cmd
    assert out.read_bytes() == b"residual"


def test_blend_bass_dropout_repair_fills_only_missing_section(tmp_path):
    sr = 8000
    t = np.arange(sr, dtype=np.float32) / sr
    residual = (np.sin(2 * np.pi * 90 * t) * 0.34).astype(np.float32)
    bass = residual.copy()
    bass[int(0.4 * sr) : int(0.55 * sr)] = 0.0

    bass_path = tmp_path / "bass.wav"
    residual_path = tmp_path / "residual.wav"
    repaired_path = tmp_path / "bass.repaired.wav"
    sf.write(bass_path, bass, sr, subtype="FLOAT")
    sf.write(residual_path, residual, sr, subtype="FLOAT")

    changed = _blend_bass_dropout_repair(
        bass_path,
        residual_path,
        repaired_path,
        max_blend=0.8,
        trigger_ratio=1.2,
        subtype="FLOAT",
    )

    assert changed
    repaired, _ = sf.read(repaired_path, dtype="float32")
    dropout = slice(int(0.43 * sr), int(0.52 * sr))
    stable = slice(int(0.1 * sr), int(0.25 * sr))
    assert float(np.mean(np.abs(repaired[dropout]))) > float(np.mean(np.abs(bass[dropout]))) + 0.05
    assert float(np.max(np.abs(repaired[stable] - bass[stable]))) < 0.01


def test_repair_bass_dropouts_noops_for_standard_preset(tmp_path, monkeypatch):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    (stems_dir / "bass.wav").write_bytes(b"wav")

    import app.pipeline.collect as collect_mod

    monkeypatch.setattr(
        collect_mod,
        "_write_bass_residual_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected repair")),
    )

    assert not repair_bass_dropouts(
        Job(id="abcdefabcdef", quality_preset="standard"),
        tmp_path / "source.wav",
        stems_dir,
        ["bass", "drums"],
    )


def test_blend_phase_residual_reduces_stem_sum_error(tmp_path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    sr = 8000
    t = np.arange(sr, dtype=np.float32) / sr
    vocals = (np.sin(2 * np.pi * 220 * t) * 0.22).astype(np.float32)
    drums = (np.sin(2 * np.pi * 880 * t) * 0.12).astype(np.float32)
    missing_phase = (np.sin(2 * np.pi * 330 * t + 0.9) * 0.06).astype(np.float32)
    source = vocals + drums + missing_phase

    reference_path = tmp_path / "source.phase.wav"
    sf.write(reference_path, source, sr, subtype="FLOAT")
    sf.write(stems_dir / "vocals.wav", vocals, sr, subtype="FLOAT")
    sf.write(stems_dir / "drums.wav", drums, sr, subtype="FLOAT")
    out_vocals = stems_dir / "vocals.phase.wav"
    out_drums = stems_dir / "drums.phase.wav"

    result = _blend_phase_residual(
        reference_path,
        stems_dir,
        ["vocals", "drums"],
        [out_vocals, out_drums],
        max_blend=1.0,
        floor_db=-90.0,
        subtype="FLOAT",
    )

    assert result.changed
    assert result.residual_ratio is not None
    repaired_vocals, _ = sf.read(out_vocals, dtype="float32")
    repaired_drums, _ = sf.read(out_drums, dtype="float32")
    before = source - (vocals + drums)
    after = source - (repaired_vocals + repaired_drums)
    assert float(np.mean(after * after)) < float(np.mean(before * before)) * 0.08
    assert result.residual_ratio < 0.08


def test_repair_phase_coherence_noops_for_standard_preset(tmp_path, monkeypatch):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    (stems_dir / "vocals.wav").write_bytes(b"wav")
    (stems_dir / "drums.wav").write_bytes(b"wav")

    import app.pipeline.collect as collect_mod

    monkeypatch.setattr(
        collect_mod,
        "_write_phase_reference",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected repair")),
    )

    assert not repair_phase_coherence(
        Job(id="abcdefabcdef", quality_preset="standard"),
        tmp_path / "source.wav",
        tmp_path,
        stems_dir,
        ["vocals", "drums"],
    )


def test_repair_phase_coherence_replaces_changed_stems(tmp_path, monkeypatch):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    for name in ("vocals", "drums"):
        (stems_dir / f"{name}.wav").write_bytes(b"old")

    import app.pipeline.collect as collect_mod

    def fake_write_reference(job, src, out):
        assert src == source
        out.write_bytes(b"reference")
        return True

    def fake_blend(reference, stem_dir, names, out_paths, **kwargs):
        assert reference.read_bytes() == b"reference"
        assert names == ["vocals", "drums"]
        assert kwargs["max_blend"] == 0.65
        for idx, out in enumerate(out_paths):
            out.write_bytes(f"new-{idx}".encode())
        return PhaseRepairResult(True, 0.42)

    monkeypatch.setattr(collect_mod, "_write_phase_reference", fake_write_reference)
    monkeypatch.setattr(collect_mod, "_blend_phase_residual", fake_blend)

    job = Job(id="abcdefabcdef", quality_preset="high")

    assert repair_phase_coherence(
        job,
        source,
        tmp_path,
        stems_dir,
        ["vocals", "drums"],
    )
    assert (stems_dir / "vocals.wav").read_bytes() == b"new-0"
    assert (stems_dir / "drums.wav").read_bytes() == b"new-1"
    assert job.phase_repair_applied is True
    assert job.phase_repair_residual_ratio == 0.42
    assert not (tmp_path / "source.phase.wav").exists()


def test_stabilize_stem_outputs_removes_dc_and_limits_float_peak(tmp_path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    sr = 44100
    t = np.linspace(0, 0.1, int(sr * 0.1), endpoint=False, dtype=np.float32)
    hot = (np.sin(2 * np.pi * 90 * t) * 1.2 + 0.04).astype(np.float32)
    sf.write(stems_dir / "bass.wav", hot, sr, subtype="FLOAT")

    stabilize_stem_outputs(Job(id="abcdefabcdef", quality_preset="high"), stems_dir, ["bass"])

    data, _ = sf.read(stems_dir / "bass.wav", dtype="float32", always_2d=True)
    assert abs(float(np.mean(data[:, 0]))) < 1e-3
    assert float(np.max(np.abs(data))) <= 0.981


def test_stabilize_stem_outputs_noops_for_standard_preset(tmp_path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    path = stems_dir / "bass.wav"
    sf.write(path, np.array([0.25, -0.25], dtype=np.float32), 44100, subtype="FLOAT")
    before = path.read_bytes()

    stabilize_stem_outputs(Job(id="abcdefabcdef", quality_preset="standard"), stems_dir, ["bass"])

    assert path.read_bytes() == before
