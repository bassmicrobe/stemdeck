from __future__ import annotations

from pathlib import Path

import app.pipeline.separate as separate
from app.core.config import DemucsSettings


def test_build_demucs_command_includes_quality_flags(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(separate, "DEMUCS_DEVICE", "cpu")
    settings = DemucsSettings(
        quality_preset="high",
        model="htdemucs_ft",
        shifts=4,
        pre_gain_db=-6.0,
        float32=True,
        clip_mode="rescale",
        overlap=0.25,
        segment=7.8,
        jobs=2,
    )

    source = tmp_path / "source.demucs.wav"
    cmd = separate.build_demucs_command(source, tmp_path, settings, device="cpu")

    assert cmd[:6] == [separate.sys.executable, "-m", "demucs", "-n", "htdemucs_ft", "-d"]
    assert "cpu" in cmd
    assert cmd[cmd.index("--shifts") : cmd.index("--shifts") + 2] == ["--shifts", "4"]
    assert cmd[cmd.index("--overlap") : cmd.index("--overlap") + 2] == ["--overlap", "0.25"]
    assert cmd[cmd.index("--segment") : cmd.index("--segment") + 2] == ["--segment", "7"]
    assert cmd[cmd.index("-j") : cmd.index("-j") + 2] == ["-j", "2"]
    assert "--float32" in cmd
    assert cmd[cmd.index("--clip-mode") : cmd.index("--clip-mode") + 2] == [
        "--clip-mode",
        "rescale",
    ]
    assert cmd[-2:] == ["-o", str(tmp_path), str(source)][-2:]


def test_build_demucs_command_explicitly_overrides_demucs_defaults(monkeypatch, tmp_path: Path):
    settings = DemucsSettings(
        quality_preset="standard",
        model="htdemucs_6s",
        shifts=0,
        pre_gain_db=0.0,
        float32=False,
        clip_mode=None,
        overlap=0.1,
        segment=0.0,
        jobs=0,
    )

    cmd = separate.build_demucs_command(tmp_path / "source.wav", tmp_path, settings)

    assert cmd[cmd.index("--shifts") : cmd.index("--shifts") + 2] == ["--shifts", "0"]
    assert cmd[cmd.index("--overlap") : cmd.index("--overlap") + 2] == [
        "--overlap",
        "0.1",
    ]
    assert "--segment" not in cmd
    assert "-j" not in cmd
    assert "--float32" not in cmd
    assert "--clip-mode" not in cmd


def test_demucs_progress_combines_shift_passes():
    progress = separate.DemucsProgress(4)

    fraction, label = progress.update(100)
    assert fraction == 0.25
    assert label == "Separating shift 1/4 · 100%"

    fraction, label = progress.update(5)
    assert fraction == 0.2625
    assert label == "Separating shift 2/4 · 5%"

    progress.update(100)
    fraction, label = progress.update(10)
    assert fraction == 0.525
    assert label == "Separating shift 3/4 · 10%"


def test_demucs_progress_ignores_model_download_percentages():
    progress = separate.DemucsProgress(4)

    fraction, label = progress.update(100, "100% 80.0MiB/80.0MiB 12.0MiB/s")
    assert fraction == 0.0
    assert label == "Preparing separation model..."

    fraction, label = progress.update(5, "5%| | 15/300 [00:01<00:20]")
    assert fraction == 0.0125
    assert label == "Separating shift 1/4 · 5%"


def test_single_pass_demucs_progress_is_unchanged():
    progress = separate.DemucsProgress(0)

    fraction, label = progress.update(57)

    assert fraction == 0.57
    assert label == "Separating 57%"


def test_bag_progress_combines_models_and_shifts():
    progress = separate.DemucsProgress(shifts=2, model_count=4)

    progress.update(100)
    progress.update(0)
    progress.update(100)
    fraction, label = progress.update(0)

    assert fraction == 0.25
    assert label == "Separating model 2/4 · shift 1/2 · 0%"


def test_demucs_model_count_reads_packaged_bag():
    assert separate.demucs_model_count("htdemucs_ft") == 4
    assert separate.demucs_model_count("htdemucs_6s") == 1
    assert separate.demucs_model_count("not-a-real-model") == 1
