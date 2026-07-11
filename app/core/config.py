import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _env_choice(name: str, default: str | None, choices: set[str]) -> str | None:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw if raw in choices else default


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default


def _detect_device() -> str:
    """Pick best available Torch device for Demucs. Override via
    STEMDECK_DEMUCS_DEVICE env var ('cuda' | 'mps' | 'cpu'). Apple Silicon
    silently falls back to CPU otherwise -- demucs's CLI default is
    "cuda if available else cpu" and macOS has no CUDA, leaving the
    integrated GPU idle and processing 3-5x slower than necessary."""
    forced = os.environ.get("STEMDECK_DEMUCS_DEVICE", "").strip().lower()
    if forced in ("cuda", "mps", "cpu"):
        return forced
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _system_memory_gb() -> float | None:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None
    return (page_size * page_count) / (1024**3)


def _detect_pipeline_concurrency(device: str) -> int:
    """Choose a safe local pipeline parallelism level.

    Demucs is the dominant memory/compute consumer. GPU backends are kept at
    one job by default because concurrent model runs can exhaust unified/VRAM
    memory quickly; roomy CPU-only machines can run two jobs in parallel.
    """
    raw = os.environ.get("STEMDECK_PIPELINE_CONCURRENCY", "").strip().lower()
    if raw and raw != "auto":
        try:
            return max(1, min(4, int(raw)))
        except ValueError:
            pass

    if device in {"cuda", "mps"}:
        return 1

    cpu_count = os.cpu_count() or 1
    memory_gb = _system_memory_gb()
    if cpu_count >= 12 and (memory_gb is None or memory_gb >= 24):
        return 2
    return 1


def _detect_background_cpu_threads() -> int:
    """Keep background extraction from starving the UI event loop/WebView.

    This only changes CPU scheduling/worker count. It does not alter Demucs
    model weights or audio math, so output quality is unchanged.
    """
    raw = os.environ.get("STEMDECK_BACKGROUND_CPU_THREADS", "").strip().lower()
    if raw and raw != "auto":
        try:
            return max(1, min(16, int(raw)))
        except ValueError:
            pass
    cpu_count = os.cpu_count() or 1
    return max(1, min(4, cpu_count // 2 or 1))


def _detect_demucs_jobs(device: str) -> int:
    """Choose Demucs chunk workers without multiplying Torch threads.

    Demucs' ``-j`` workers each hold a full chunk and can multiply memory use.
    Apple Accelerate performs better with one inference stream, while large
    Linux/Windows CPU hosts can benefit from two chunk workers.
    """
    raw = os.environ.get("STEMDECK_DEMUCS_JOBS", "").strip().lower()
    if raw and raw != "auto":
        try:
            return max(0, min(4, int(raw)))
        except ValueError:
            pass
    if device != "cpu" or sys.platform == "darwin":
        return 0
    if _detect_pipeline_concurrency(device) > 1:
        return 0
    cpu_count = os.cpu_count() or 1
    memory_gb = _system_memory_gb()
    if cpu_count >= 16 and (memory_gb is None or memory_gb >= 32):
        return 2
    return 0


ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = ROOT / "static"
STEM_NAMES: tuple[str, ...] = ("vocals", "drums", "bass", "guitar", "piano", "other")
JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")

SUPPORTED_QUALITY_PRESETS = frozenset(("standard", "high", "max", "ultra"))
SUPPORTED_STEM_DENOISE_PRESETS = frozenset(("off", "light", "strong"))
SUPPORTED_DEMUCS_DEVICE_CHOICES = frozenset(("auto", "cpu", "mps", "cuda"))
SUPPORTED_BEAT_TRACKERS = frozenset(("auto", "beat_this", "librosa"))


@dataclass(frozen=True)
class DemucsSettings:
    quality_preset: str
    model: str
    shifts: int
    pre_gain_db: float
    float32: bool
    clip_mode: str | None
    overlap: float
    segment: float
    jobs: int = 0


def normalize_quality_preset(value: str | None) -> str:
    preset = (value or "").strip().lower()
    return preset if preset in SUPPORTED_QUALITY_PRESETS else "standard"


def normalize_stem_denoise_preset(value: str | None) -> str:
    preset = (value or "").strip().lower()
    return preset if preset in SUPPORTED_STEM_DENOISE_PRESETS else "off"


def normalize_demucs_device_choice(value: str | None) -> str:
    choice = (value or "").strip().lower()
    return choice if choice in SUPPORTED_DEMUCS_DEVICE_CHOICES else "auto"


def available_demucs_devices() -> tuple[str, ...]:
    """Return Torch devices this backend can actually run right now."""
    devices: list[str] = ["cpu"]
    try:
        import torch

        if torch.cuda.is_available():
            devices.append("cuda")
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            devices.append("mps")
    except ImportError:
        pass
    return tuple(devices)


def demucs_device_choice_available(value: str | None) -> bool:
    choice = normalize_demucs_device_choice(value)
    return choice == "auto" or choice in available_demucs_devices()


def resolve_demucs_device_choice(value: str | None) -> str:
    choice = normalize_demucs_device_choice(value)
    if choice == "auto":
        return globals().get("DEMUCS_DEVICE") or _detect_device()
    return choice


def stem_names_for_quality_preset(preset: str | None) -> tuple[str, ...]:
    quality_preset = normalize_quality_preset(preset)
    if quality_preset in ("high", "max", "ultra"):
        return ("vocals", "drums", "bass", "other")
    return STEM_NAMES


QUALITY_PRESET = _env_choice(
    "STEMDECK_QUALITY_PRESET", "standard", set(SUPPORTED_QUALITY_PRESETS)
) or "standard"
BEAT_TRACKER = _env_choice(
    "STEMDECK_BEAT_TRACKER", "auto", set(SUPPORTED_BEAT_TRACKERS)
) or "auto"
BEAT_THIS_MODEL = os.environ.get("STEMDECK_BEAT_THIS_MODEL", "").strip()
_QUALITY_DEFAULTS = {
    "standard": {
        "model": "htdemucs_6s",
        "shifts": 1,
        "pre_gain_db": 0.0,
        "float32": False,
        "clip_mode": None,
        "overlap": 0.20,
        "segment": 0.0,
    },
    # htdemucs_ft is a four-model bag, so every shift costs four inference
    # passes. Keep the interactive presets conservative: shift averaging has
    # sharply diminishing returns beyond the first few runs.
    "high": {
        "model": "htdemucs_ft",
        "shifts": 1,
        "pre_gain_db": -6.0,
        "float32": True,
        "clip_mode": "rescale",
        "overlap": 0.20,
        "segment": 0.0,
    },
    "max": {
        "model": "htdemucs_ft",
        "shifts": 2,
        "pre_gain_db": -6.0,
        "float32": True,
        "clip_mode": "rescale",
        "overlap": 0.25,
        "segment": 0.0,
    },
    # Highest practical local preset. Four shifts across the four-model bag
    # still performs 16 inference passes; the former 16-shift default required
    # 64 passes and commonly took over an hour for a five-minute track.
    "ultra": {
        "model": "htdemucs_ft",
        "shifts": 4,
        "pre_gain_db": -8.0,
        "float32": True,
        "clip_mode": "rescale",
        "overlap": 0.25,
        "segment": 0.0,
    },
}


def demucs_settings_for_preset(
    preset: str | None,
    *,
    device: str | None = None,
) -> DemucsSettings:
    quality_preset = normalize_quality_preset(preset)
    quality = _QUALITY_DEFAULTS[quality_preset]
    model = os.environ.get("STEMDECK_DEMUCS_MODEL", str(quality["model"])).strip() or str(
        quality["model"]
    )
    return DemucsSettings(
        quality_preset=quality_preset,
        model=model,
        shifts=max(0, _env_int("STEMDECK_DEMUCS_SHIFTS", int(quality["shifts"]))),
        pre_gain_db=_env_float("STEMDECK_DEMUCS_PRE_GAIN_DB", float(quality["pre_gain_db"])),
        float32=_env_bool("STEMDECK_DEMUCS_FLOAT32", bool(quality["float32"])),
        clip_mode=_env_choice(
            "STEMDECK_DEMUCS_CLIP_MODE", quality["clip_mode"], {"rescale", "clamp", "none"}
        ),
        overlap=max(0.0, _env_float("STEMDECK_DEMUCS_OVERLAP", float(quality["overlap"]))),
        segment=max(0.0, _env_float("STEMDECK_DEMUCS_SEGMENT", float(quality["segment"]))),
        jobs=_detect_demucs_jobs(device or _detect_device()),
    )


def wav_codec_for_quality_preset(preset: str | None) -> str:
    settings = demucs_settings_for_preset(preset)
    return "pcm_f32le" if settings.float32 else "pcm_s16le"


_STEM_DENOISE_FILTERS = {
    # Conservative broadband denoise. Safe for most stems, less likely to
    # introduce watery FFT artifacts than heavy reduction.
    "light": "afftdn=nr=8:nf=-55:rf=-45:tn=1:gs=8",
    # Stronger cleanup for obviously noisy material; users can opt in when the
    # artifact tradeoff is acceptable.
    "strong": "afftdn=nr=14:nf=-50:rf=-38:tn=1:gs=12",
}


def stem_denoise_filter_for_preset(value: str | None) -> str | None:
    return _STEM_DENOISE_FILTERS.get(normalize_stem_denoise_preset(value))


def bass_repair_enabled_for_preset(preset: str | None) -> bool:
    """Enable conservative bass dropout repair for quality-first presets.

    The env var is intentionally global so packaged builds can force the
    behavior without changing per-job API shape.
    """
    default = normalize_quality_preset(preset) in ("high", "max", "ultra")
    return _env_bool("STEMDECK_BASS_REPAIR", default)


def phase_repair_enabled_for_preset(preset: str | None) -> bool:
    """Enable stem-sum residual repair for quality-first presets.

    Demucs can leave tiny phase/residual errors between the sum of the stems
    and the original mix. The repair pass is slower, so keep it on the
    quality-first path unless explicitly overridden.
    """
    default = normalize_quality_preset(preset) in ("high", "max", "ultra")
    return _env_bool("STEMDECK_PHASE_REPAIR", default)


_PHASE_REPAIR_DEFAULT_MAX_BLEND = {
    "standard": 0.42,
    "high": 0.65,
    "max": 0.90,
    "ultra": 0.95,
}


def _clamp_phase_repair_blend(value: float) -> float:
    return min(1.0, max(0.0, value))


def phase_repair_max_blend_for_preset(preset: str | None) -> float:
    """Return residual blend strength for stem-sum coherence repair.

    High stays moderately conservative to protect isolation. Max prioritizes
    source reconstruction and can leave more bleed in isolated stems.
    """
    quality_preset = normalize_quality_preset(preset)
    default = _PHASE_REPAIR_DEFAULT_MAX_BLEND[quality_preset]
    return _clamp_phase_repair_blend(_env_float("STEMDECK_PHASE_REPAIR_MAX_BLEND", default))


def stem_gate_enabled_for_preset(_preset: str | None) -> bool:
    """Mute only near-silent stem bleed while preserving timeline alignment."""
    return _env_bool("STEMDECK_STEM_GATE", True)


_demucs_settings = demucs_settings_for_preset(QUALITY_PRESET)

# Runtime knobs -- env-backed so Docker / desktop packaging / local dev can
# tune without a code edit. STEMDECK_DATA_DIR is the legacy portable app root for
# mutable runtime data; when unset, dev behavior remains the repo-local jobs/
# folder.
PORTABLE_DATA_DIR_ENABLED = bool(os.environ.get("STEMDECK_DATA_DIR", "").strip())
DATA_DIR = _env_path("STEMDECK_DATA_DIR", ROOT)
JOBS_DIR = _env_path(
    "STEMDECK_JOBS_DIR",
    (DATA_DIR / "jobs") if PORTABLE_DATA_DIR_ENABLED else (ROOT / "jobs"),
)
CACHE_DIR = _env_path("STEMDECK_CACHE_DIR", DATA_DIR / "cache")
DOWNLOADS_DIR = _env_path("STEMDECK_DOWNLOADS_DIR", DATA_DIR / "downloads")
MODELS_DIR = _env_path("STEMDECK_MODELS_DIR", DATA_DIR / "models")
LOGS_DIR = _env_path("STEMDECK_LOGS_DIR", DATA_DIR / "logs")
FFMPEG_DIR = _env_path("STEMDECK_FFMPEG_DIR", DATA_DIR / "ffmpeg")
FFMPEG_BIN = _env_path(
    "STEMDECK_FFMPEG",
    FFMPEG_DIR / ("ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"),
)
FFPROBE_BIN = _env_path(
    "STEMDECK_FFPROBE",
    FFMPEG_DIR / ("ffprobe.exe" if sys.platform.startswith("win") else "ffprobe"),
)
DEMUCS_MODEL = _demucs_settings.model
DEMUCS_DEVICE = _detect_device()
PIPELINE_CONCURRENCY = _detect_pipeline_concurrency(DEMUCS_DEVICE)
DEMUCS_JOBS = _detect_demucs_jobs(DEMUCS_DEVICE)
DEMUCS_PERSISTENT_WORKER = _env_bool("LAYERLAB_DEMUCS_PERSISTENT_WORKER", True)
BACKGROUND_PROCESS_PRIORITY = _env_bool("STEMDECK_BACKGROUND_PROCESS_PRIORITY", True)
BACKGROUND_PROCESS_NICE = max(0, min(19, _env_int("STEMDECK_BACKGROUND_PROCESS_NICE", 8)))
BACKGROUND_CPU_THREADS = _detect_background_cpu_threads()
PCM_WORKER_ENABLED = _env_bool("LAYERLAB_PCM_WORKER_ENABLED", True)
DEMUCS_SHIFTS = _demucs_settings.shifts
DEMUCS_PRE_GAIN_DB = _demucs_settings.pre_gain_db
DEMUCS_FLOAT32 = _demucs_settings.float32
DEMUCS_CLIP_MODE = _demucs_settings.clip_mode
DEMUCS_OVERLAP = _demucs_settings.overlap
DEMUCS_SEGMENT = _demucs_settings.segment
BASS_REPAIR_LOW_PASS_HZ = max(40.0, _env_float("STEMDECK_BASS_REPAIR_LOW_PASS_HZ", 180.0))
BASS_REPAIR_TRIGGER_RATIO = max(1.05, _env_float("STEMDECK_BASS_REPAIR_TRIGGER_RATIO", 1.9))
BASS_REPAIR_MAX_BLEND = min(1.0, max(0.0, _env_float("STEMDECK_BASS_REPAIR_MAX_BLEND", 0.65)))
BASS_REPAIR_SHORT_GAP_MS = max(8, _env_int("STEMDECK_BASS_REPAIR_SHORT_GAP_MS", 220))
BASS_REPAIR_SHORT_GAP_RATIO = max(
    1.05, _env_float("STEMDECK_BASS_REPAIR_SHORT_GAP_RATIO", 2.4)
)
PHASE_REPAIR_MAX_BLEND = phase_repair_max_blend_for_preset(QUALITY_PRESET)
PHASE_REPAIR_FLOOR_DB = min(-24.0, max(-96.0, _env_float("STEMDECK_PHASE_REPAIR_FLOOR_DB", -58.0)))
STEM_POST_LIMITER_PEAK = min(0.999, max(0.5, _env_float("STEMDECK_STEM_POST_LIMITER_PEAK", 0.98)))
MIX_LIMITER_ATTACK_MS = min(
    80.0,
    max(0.1, _env_float("STEMDECK_MIX_LIMITER_ATTACK_MS", 5.0)),
)
MIX_LIMITER_RELEASE_MS = min(
    8000.0,
    max(1.0, _env_float("STEMDECK_MIX_LIMITER_RELEASE_MS", 50.0)),
)
STEM_GATE_THRESHOLD_DB = min(-24.0, max(-96.0, _env_float("STEMDECK_STEM_GATE_THRESHOLD_DB", -54.0)))
STEM_GATE_WINDOW_MS = max(5, _env_int("STEMDECK_STEM_GATE_WINDOW_MS", 20))
STEM_GATE_HOLD_MS = max(0, _env_int("STEMDECK_STEM_GATE_HOLD_MS", 90))
STEM_GATE_ATTACK_MS = max(1, _env_int("STEMDECK_STEM_GATE_ATTACK_MS", 8))
STEM_GATE_RELEASE_MS = max(1, _env_int("STEMDECK_STEM_GATE_RELEASE_MS", 85))
STEM_PREPROCESS_TARGET_I = _env_float("STEMDECK_PREPROCESS_TARGET_I", -18.0)
STEM_PREPROCESS_TRUE_PEAK = min(
    -0.1, max(-6.0, _env_float("STEMDECK_PREPROCESS_TRUE_PEAK", -1.5))
)
MAX_DURATION_SEC = max(60, _env_int("STEMDECK_MAX_DURATION_SEC", 1200))  # 20 min default
JOB_TTL_SECONDS = max(300, _env_int("STEMDECK_JOB_TTL_SECONDS", 24 * 3600))  # 24 h default
MAX_PENDING_JOBS = max(1, min(50, _env_int("STEMDECK_MAX_PENDING_JOBS", 3)))
TIMEOUT_FFMPEG = _env_int("STEMDECK_TIMEOUT_FFMPEG", 300)
TIMEOUT_ANALYZE = _env_int("STEMDECK_TIMEOUT_ANALYZE", 120)
PCM_WORKER_TIMEOUT = max(10, _env_int("LAYERLAB_PCM_WORKER_TIMEOUT", 600))
TIMEOUT_DEMUCS_STALL = _env_int("STEMDECK_TIMEOUT_DEMUCS_STALL", 1800)
TIMEOUT_DEMUCS_TOTAL = max(0, _env_int("STEMDECK_TIMEOUT_DEMUCS_TOTAL", 12 * 3600))


def output_limiter_filter() -> str:
    """Return a transparent-until-needed limiter for rendered stem mixes."""
    return (
        f"alimiter=limit={STEM_POST_LIMITER_PEAK:g}:"
        f"attack={MIX_LIMITER_ATTACK_MS:g}:release={MIX_LIMITER_RELEASE_MS:g}:"
        "level=0:latency=1"
    )


def _imageio_ffmpeg_executable() -> str | None:
    try:
        import imageio_ffmpeg

        path = Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return None
    return str(path) if path.is_file() else None


def ffmpeg_executable() -> str:
    """Return the preferred FFmpeg executable.

    In portable mode, setup places FFmpeg under DATA_DIR/ffmpeg. Prefer that
    binary when present; otherwise fall back to PATH so local dev and Docker
    keep working exactly as before.
    """
    if FFMPEG_BIN.is_file():
        return str(FFMPEG_BIN)
    if path := shutil.which("ffmpeg"):
        return path
    if path := _imageio_ffmpeg_executable():
        return path
    return "ffmpeg"


def ffmpeg_available() -> bool:
    return Path(ffmpeg_executable()).is_file() or shutil.which(ffmpeg_executable()) is not None


def ffprobe_executable() -> str:
    """Return the preferred ffprobe executable (same bundled dir as ffmpeg)."""
    return str(FFPROBE_BIN) if FFPROBE_BIN.is_file() else "ffprobe"


def configure_portable_environment() -> None:
    """Keep generated caches inside the portable data folder when requested.

    This is intentionally best-effort. It only sets variables that are still
    unset, so explicit caller/env choices win.
    """
    if FFMPEG_DIR.is_dir():
        path = os.environ.get("PATH", "")
        ffmpeg_path = str(FFMPEG_DIR)
        if ffmpeg_path not in path.split(os.pathsep):
            os.environ["PATH"] = ffmpeg_path + (os.pathsep + path if path else "")

    if PORTABLE_DATA_DIR_ENABLED:
        os.environ.setdefault("XDG_CACHE_HOME", str(CACHE_DIR))
        os.environ.setdefault("TORCH_HOME", str(MODELS_DIR / "torch"))


def ensure_runtime_dirs() -> None:
    paths = (
        (JOBS_DIR, CACHE_DIR, DOWNLOADS_DIR, MODELS_DIR, LOGS_DIR)
        if PORTABLE_DATA_DIR_ENABLED
        else (JOBS_DIR,)
    )
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
