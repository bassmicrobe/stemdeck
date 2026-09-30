from __future__ import annotations

import importlib
import os
from pathlib import Path


def test_data_dir_moves_default_runtime_dirs(monkeypatch, tmp_path: Path):
    import app.core.config as config

    original = config
    data_dir = tmp_path / "portable-data"
    monkeypatch.setenv("STEMDECK_DATA_DIR", str(data_dir))
    monkeypatch.delenv("STEMDECK_JOBS_DIR", raising=False)
    monkeypatch.delenv("STEMDECK_CACHE_DIR", raising=False)
    monkeypatch.delenv("STEMDECK_DOWNLOADS_DIR", raising=False)
    monkeypatch.delenv("STEMDECK_MODELS_DIR", raising=False)
    monkeypatch.delenv("STEMDECK_LOGS_DIR", raising=False)
    try:
        reloaded = importlib.reload(config)
        assert data_dir.resolve() == reloaded.DATA_DIR
        assert data_dir.resolve() / "jobs" == reloaded.JOBS_DIR
        assert data_dir.resolve() / "cache" == reloaded.CACHE_DIR
        assert data_dir.resolve() / "downloads" == reloaded.DOWNLOADS_DIR
        assert data_dir.resolve() / "models" == reloaded.MODELS_DIR
        assert data_dir.resolve() / "logs" == reloaded.LOGS_DIR
    finally:
        monkeypatch.delenv("STEMDECK_DATA_DIR", raising=False)
        importlib.reload(original)


def test_jobs_dir_override_wins_over_data_dir(monkeypatch, tmp_path: Path):
    import app.core.config as config

    original = config
    data_dir = tmp_path / "portable-data"
    jobs_dir = tmp_path / "custom-jobs"
    monkeypatch.setenv("STEMDECK_DATA_DIR", str(data_dir))
    monkeypatch.setenv("STEMDECK_JOBS_DIR", str(jobs_dir))
    try:
        reloaded = importlib.reload(config)
        assert data_dir.resolve() == reloaded.DATA_DIR
        assert jobs_dir.resolve() == reloaded.JOBS_DIR
    finally:
        monkeypatch.delenv("STEMDECK_DATA_DIR", raising=False)
        monkeypatch.delenv("STEMDECK_JOBS_DIR", raising=False)
        importlib.reload(original)


def test_ffmpeg_executable_prefers_portable_binary(monkeypatch, tmp_path: Path):
    import app.core.config as config

    original = config
    ffmpeg = tmp_path / "ffmpeg" / "ffmpeg"
    ffmpeg.parent.mkdir()
    ffmpeg.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("STEMDECK_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STEMDECK_FFMPEG", str(ffmpeg))
    try:
        reloaded = importlib.reload(config)
        assert reloaded.ffmpeg_executable() == str(ffmpeg.resolve())
    finally:
        monkeypatch.delenv("STEMDECK_DATA_DIR", raising=False)
        monkeypatch.delenv("STEMDECK_FFMPEG", raising=False)
        importlib.reload(original)


def test_ffmpeg_executable_uses_imageio_fallback(monkeypatch, tmp_path: Path):
    import app.core.config as config

    ffmpeg = tmp_path / "imageio-ffmpeg"
    ffmpeg.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(config.shutil, "which", lambda _name: None)
    monkeypatch.setattr(config, "_imageio_ffmpeg_executable", lambda: str(ffmpeg))

    assert config.ffmpeg_executable() == str(ffmpeg)
    assert config.ffmpeg_available() is True


def test_configure_portable_environment_leaves_dev_cache_env_alone(monkeypatch):
    import app.core.config as config

    original = config
    monkeypatch.delenv("STEMDECK_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("TORCH_HOME", raising=False)
    try:
        reloaded = importlib.reload(config)
        reloaded.configure_portable_environment()
        assert "XDG_CACHE_HOME" not in os.environ
        assert "TORCH_HOME" not in os.environ
    finally:
        monkeypatch.delenv("STEMDECK_DATA_DIR", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.delenv("TORCH_HOME", raising=False)
        importlib.reload(original)


def test_high_quality_preset_sets_slower_demucs_defaults(monkeypatch):
    import app.core.config as config

    original = config
    monkeypatch.setenv("STEMDECK_QUALITY_PRESET", "high")
    monkeypatch.delenv("STEMDECK_DEMUCS_MODEL", raising=False)
    monkeypatch.delenv("STEMDECK_DEMUCS_SHIFTS", raising=False)
    monkeypatch.delenv("STEMDECK_DEMUCS_PRE_GAIN_DB", raising=False)
    try:
        reloaded = importlib.reload(config)
        assert reloaded.QUALITY_PRESET == "high"
        assert reloaded.DEMUCS_MODEL == "htdemucs_ft"
        assert reloaded.DEMUCS_SHIFTS == 1
        assert reloaded.DEMUCS_PRE_GAIN_DB == -6.0
        assert reloaded.DEMUCS_FLOAT32 is True
        assert reloaded.DEMUCS_OVERLAP == 0.2
    finally:
        monkeypatch.delenv("STEMDECK_QUALITY_PRESET", raising=False)
        importlib.reload(original)


def test_ultra_quality_preset_sets_slowest_demucs_defaults(monkeypatch):
    import app.core.config as config

    original = config
    monkeypatch.setenv("STEMDECK_QUALITY_PRESET", "ultra")
    monkeypatch.delenv("STEMDECK_DEMUCS_MODEL", raising=False)
    monkeypatch.delenv("STEMDECK_DEMUCS_SHIFTS", raising=False)
    monkeypatch.delenv("STEMDECK_DEMUCS_PRE_GAIN_DB", raising=False)
    monkeypatch.delenv("STEMDECK_DEMUCS_OVERLAP", raising=False)
    try:
        reloaded = importlib.reload(config)
        assert reloaded.QUALITY_PRESET == "ultra"
        assert reloaded.DEMUCS_MODEL == "htdemucs_ft"
        assert reloaded.DEMUCS_SHIFTS == 4
        assert reloaded.DEMUCS_PRE_GAIN_DB == -8.0
        assert reloaded.DEMUCS_FLOAT32 is True
        assert reloaded.DEMUCS_CLIP_MODE == "rescale"
        assert reloaded.DEMUCS_OVERLAP == 0.25
    finally:
        monkeypatch.delenv("STEMDECK_QUALITY_PRESET", raising=False)
        importlib.reload(original)


def test_explicit_demucs_options_win_over_quality_preset(monkeypatch):
    import app.core.config as config

    original = config
    monkeypatch.setenv("STEMDECK_QUALITY_PRESET", "max")
    monkeypatch.setenv("STEMDECK_DEMUCS_MODEL", "htdemucs_6s")
    monkeypatch.setenv("STEMDECK_DEMUCS_SHIFTS", "2")
    monkeypatch.setenv("STEMDECK_DEMUCS_PRE_GAIN_DB", "-3")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.QUALITY_PRESET == "max"
        assert reloaded.DEMUCS_MODEL == "htdemucs_6s"
        assert reloaded.DEMUCS_SHIFTS == 2
        assert reloaded.DEMUCS_PRE_GAIN_DB == -3.0
    finally:
        monkeypatch.delenv("STEMDECK_QUALITY_PRESET", raising=False)
        monkeypatch.delenv("STEMDECK_DEMUCS_MODEL", raising=False)
        monkeypatch.delenv("STEMDECK_DEMUCS_SHIFTS", raising=False)
        monkeypatch.delenv("STEMDECK_DEMUCS_PRE_GAIN_DB", raising=False)
        importlib.reload(original)


def test_pipeline_concurrency_auto_is_conservative(monkeypatch):
    import app.core.config as config

    monkeypatch.delenv("STEMDECK_PIPELINE_CONCURRENCY", raising=False)
    monkeypatch.setattr(config.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(config, "_system_memory_gb", lambda: 32.0)

    assert config._detect_pipeline_concurrency("mps") == 1
    assert config._detect_pipeline_concurrency("cuda") == 1
    assert config._detect_pipeline_concurrency("cpu") == 2

    monkeypatch.setattr(config, "_system_memory_gb", lambda: 16.0)
    assert config._detect_pipeline_concurrency("cpu") == 1


def test_pipeline_concurrency_env_override(monkeypatch):
    import app.core.config as config

    monkeypatch.setenv("STEMDECK_PIPELINE_CONCURRENCY", "3")
    assert config._detect_pipeline_concurrency("mps") == 3


def test_demucs_cpu_jobs_avoid_oversubscription(monkeypatch):
    import app.core.config as config

    monkeypatch.delenv("STEMDECK_DEMUCS_JOBS", raising=False)
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(config.os, "cpu_count", lambda: 16)
    monkeypatch.setattr(config, "_system_memory_gb", lambda: 64.0)
    assert config._detect_demucs_jobs("cpu") == 0

    monkeypatch.setattr(config.sys, "platform", "linux")
    assert config._detect_demucs_jobs("mps") == 0
    assert config._detect_demucs_jobs("cuda") == 0
    assert config._detect_demucs_jobs("cpu") == 0

    monkeypatch.setenv("STEMDECK_PIPELINE_CONCURRENCY", "1")
    assert config._detect_demucs_jobs("cpu") == 2

    monkeypatch.setenv("STEMDECK_DEMUCS_JOBS", "3")
    assert config._detect_demucs_jobs("cpu") == 3


def test_demucs_settings_use_per_job_device_for_worker_count(monkeypatch):
    import app.core.config as config

    monkeypatch.setattr(config, "_detect_device", lambda: "mps")
    monkeypatch.setattr(
        config,
        "_detect_demucs_jobs",
        lambda device: 2 if device == "cpu" else 0,
    )

    assert config.demucs_settings_for_preset("standard").jobs == 0
    assert config.demucs_settings_for_preset("standard", device="cpu").jobs == 2


def test_demucs_device_choice_normalization_and_resolution(monkeypatch):
    import app.core.config as config

    monkeypatch.setattr(config, "DEMUCS_DEVICE", "mps", raising=False)
    assert config.normalize_demucs_device_choice("CPU") == "cpu"
    assert config.normalize_demucs_device_choice("wat") == "auto"
    assert config.resolve_demucs_device_choice("auto") == "mps"
    assert config.resolve_demucs_device_choice("cpu") == "cpu"

    monkeypatch.setenv("STEMDECK_PIPELINE_CONCURRENCY", "0")
    assert config._detect_pipeline_concurrency("cpu") == 1

    monkeypatch.setenv("STEMDECK_PIPELINE_CONCURRENCY", "99")
    assert config._detect_pipeline_concurrency("cpu") == 4


def test_high_quality_preset_supports_four_stems():
    from app.core.config import (
        bass_repair_enabled_for_preset,
        normalize_stem_denoise_preset,
        phase_repair_enabled_for_preset,
        phase_repair_max_blend_for_preset,
        stem_denoise_filter_for_preset,
        stem_gate_enabled_for_preset,
        stem_names_for_quality_preset,
        wav_codec_for_quality_preset,
    )

    assert stem_names_for_quality_preset("standard") == (
        "vocals",
        "drums",
        "bass",
        "guitar",
        "piano",
        "other",
    )
    assert stem_names_for_quality_preset("high") == ("vocals", "drums", "bass", "other")
    assert stem_names_for_quality_preset("max") == ("vocals", "drums", "bass", "other")
    assert stem_names_for_quality_preset("ultra") == ("vocals", "drums", "bass", "other")
    assert wav_codec_for_quality_preset("standard") == "pcm_s16le"
    assert wav_codec_for_quality_preset("high") == "pcm_f32le"
    assert wav_codec_for_quality_preset("max") == "pcm_f32le"
    assert wav_codec_for_quality_preset("ultra") == "pcm_f32le"
    assert bass_repair_enabled_for_preset("standard") is False
    assert bass_repair_enabled_for_preset("high") is True
    assert bass_repair_enabled_for_preset("max") is True
    assert bass_repair_enabled_for_preset("ultra") is True
    assert phase_repair_enabled_for_preset("standard") is False
    assert phase_repair_enabled_for_preset("high") is True
    assert phase_repair_enabled_for_preset("max") is True
    assert phase_repair_enabled_for_preset("ultra") is True
    assert stem_gate_enabled_for_preset("standard") is True
    assert stem_gate_enabled_for_preset("high") is True
    assert stem_gate_enabled_for_preset("max") is True
    assert stem_gate_enabled_for_preset("ultra") is True
    assert phase_repair_max_blend_for_preset("standard") == 0.42
    assert phase_repair_max_blend_for_preset("high") == 0.65
    assert phase_repair_max_blend_for_preset("max") == 0.9
    assert phase_repair_max_blend_for_preset("ultra") == 0.95
    assert normalize_stem_denoise_preset("light") == "light"
    assert normalize_stem_denoise_preset("STRONG") == "strong"
    assert normalize_stem_denoise_preset("watery") == "off"
    assert stem_denoise_filter_for_preset("off") is None
    assert "afftdn=" in (stem_denoise_filter_for_preset("light") or "")


def test_bass_repair_env_override(monkeypatch):
    from app.core.config import bass_repair_enabled_for_preset

    monkeypatch.setenv("STEMDECK_BASS_REPAIR", "0")
    assert bass_repair_enabled_for_preset("high") is False
    monkeypatch.setenv("STEMDECK_BASS_REPAIR", "1")
    assert bass_repair_enabled_for_preset("standard") is True


def test_phase_repair_env_override(monkeypatch):
    from app.core.config import phase_repair_enabled_for_preset, phase_repair_max_blend_for_preset

    monkeypatch.setenv("STEMDECK_PHASE_REPAIR", "0")
    assert phase_repair_enabled_for_preset("high") is False
    monkeypatch.setenv("STEMDECK_PHASE_REPAIR", "1")
    assert phase_repair_enabled_for_preset("standard") is True
    monkeypatch.setenv("STEMDECK_PHASE_REPAIR_MAX_BLEND", "0.8")
    assert phase_repair_max_blend_for_preset("high") == 0.8
    monkeypatch.setenv("STEMDECK_PHASE_REPAIR_MAX_BLEND", "2")
    assert phase_repair_max_blend_for_preset("max") == 1.0


def test_stem_gate_env_override(monkeypatch):
    from app.core.config import stem_gate_enabled_for_preset

    monkeypatch.setenv("STEMDECK_STEM_GATE", "0")
    assert stem_gate_enabled_for_preset("high") is False
    monkeypatch.setenv("STEMDECK_STEM_GATE", "1")
    assert stem_gate_enabled_for_preset("standard") is True
