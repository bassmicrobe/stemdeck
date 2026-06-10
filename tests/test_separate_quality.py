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
    )

    source = tmp_path / "source.demucs.wav"
    cmd = separate.build_demucs_command(source, tmp_path, settings)

    assert cmd[:6] == [separate.sys.executable, "-m", "demucs", "-n", "htdemucs_ft", "-d"]
    assert "cpu" in cmd
    assert cmd[cmd.index("--shifts") : cmd.index("--shifts") + 2] == ["--shifts", "4"]
    assert cmd[cmd.index("--overlap") : cmd.index("--overlap") + 2] == ["--overlap", "0.25"]
    assert cmd[cmd.index("--segment") : cmd.index("--segment") + 2] == ["--segment", "7.8"]
    assert "--float32" in cmd
    assert cmd[cmd.index("--clip-mode") : cmd.index("--clip-mode") + 2] == [
        "--clip-mode",
        "rescale",
    ]
    assert cmd[-2:] == ["-o", str(tmp_path), str(source)][-2:]


def test_build_demucs_command_omits_standard_quality_flags(monkeypatch, tmp_path: Path):
    settings = DemucsSettings(
        quality_preset="standard",
        model="htdemucs_6s",
        shifts=0,
        pre_gain_db=0.0,
        float32=False,
        clip_mode=None,
        overlap=0.0,
        segment=0.0,
    )

    cmd = separate.build_demucs_command(tmp_path / "source.wav", tmp_path, settings)

    assert "--shifts" not in cmd
    assert "--overlap" not in cmd
    assert "--segment" not in cmd
    assert "--float32" not in cmd
    assert "--clip-mode" not in cmd
