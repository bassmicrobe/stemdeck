from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import numpy as np

from app.core.models import Job
from app.pipeline.collect import (
    _PEAK_POINTS,
    compute_stem_peaks,
    make_selected_mix,
    restore_demucs_gain,
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
