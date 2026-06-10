from __future__ import annotations

from pathlib import Path


def test_build_demucs_command_includes_quality_flags(monkeypatch, tmp_path: Path):
    import app.pipeline.separate as separate

    monkeypatch.setattr(separate, "DEMUCS_MODEL", "htdemucs_ft")
    monkeypatch.setattr(separate, "DEMUCS_DEVICE", "cpu")
    monkeypatch.setattr(separate, "DEMUCS_SHIFTS", 4)
    monkeypatch.setattr(separate, "DEMUCS_OVERLAP", 0.25)
    monkeypatch.setattr(separate, "DEMUCS_SEGMENT", 7.8)
    monkeypatch.setattr(separate, "DEMUCS_FLOAT32", True)
    monkeypatch.setattr(separate, "DEMUCS_CLIP_MODE", "rescale")

    source = tmp_path / "source.demucs.wav"
    cmd = separate.build_demucs_command(source, tmp_path)

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
    import app.pipeline.separate as separate

    monkeypatch.setattr(separate, "DEMUCS_SHIFTS", 0)
    monkeypatch.setattr(separate, "DEMUCS_OVERLAP", 0.0)
    monkeypatch.setattr(separate, "DEMUCS_SEGMENT", 0.0)
    monkeypatch.setattr(separate, "DEMUCS_FLOAT32", False)
    monkeypatch.setattr(separate, "DEMUCS_CLIP_MODE", None)

    cmd = separate.build_demucs_command(tmp_path / "source.wav", tmp_path)

    assert "--shifts" not in cmd
    assert "--overlap" not in cmd
    assert "--segment" not in cmd
    assert "--float32" not in cmd
    assert "--clip-mode" not in cmd
