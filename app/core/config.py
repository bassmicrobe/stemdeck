import os
import re
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


ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = ROOT / "static"
STEM_NAMES: tuple[str, ...] = ("vocals", "drums", "bass", "guitar", "piano", "other")
JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")

SUPPORTED_QUALITY_PRESETS = frozenset(("standard", "high", "max"))


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


def normalize_quality_preset(value: str | None) -> str:
    preset = (value or "").strip().lower()
    return preset if preset in SUPPORTED_QUALITY_PRESETS else "standard"


QUALITY_PRESET = _env_choice(
    "STEMDECK_QUALITY_PRESET", "standard", set(SUPPORTED_QUALITY_PRESETS)
) or "standard"
_QUALITY_DEFAULTS = {
    "standard": {
        "model": "htdemucs_6s",
        "shifts": 0,
        "pre_gain_db": 0.0,
        "float32": False,
        "clip_mode": None,
    },
    # Slower, cleaner 4-stem separation. htdemucs_ft is Demucs' fine-tuned
    # model; shifts averages repeated runs and helps reduce random artifacts.
    "high": {
        "model": "htdemucs_ft",
        "shifts": 4,
        "pre_gain_db": -6.0,
        "float32": True,
        "clip_mode": "rescale",
    },
    "max": {
        "model": "htdemucs_ft",
        "shifts": 10,
        "pre_gain_db": -6.0,
        "float32": True,
        "clip_mode": "rescale",
    },
}


def demucs_settings_for_preset(preset: str | None) -> DemucsSettings:
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
        overlap=max(0.0, _env_float("STEMDECK_DEMUCS_OVERLAP", 0.0)),
        segment=max(0.0, _env_float("STEMDECK_DEMUCS_SEGMENT", 0.0)),
    )


_demucs_settings = demucs_settings_for_preset(QUALITY_PRESET)

# Runtime knobs -- env-backed so Docker / desktop packaging / local dev can
# tune without a code edit. STEMDECK_DATA_DIR is the portable app root for
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
DEMUCS_SHIFTS = _demucs_settings.shifts
DEMUCS_PRE_GAIN_DB = _demucs_settings.pre_gain_db
DEMUCS_FLOAT32 = _demucs_settings.float32
DEMUCS_CLIP_MODE = _demucs_settings.clip_mode
DEMUCS_OVERLAP = _demucs_settings.overlap
DEMUCS_SEGMENT = _demucs_settings.segment
MAX_DURATION_SEC = max(60, _env_int("STEMDECK_MAX_DURATION_SEC", 1200))  # 20 min default
JOB_TTL_SECONDS = max(300, _env_int("STEMDECK_JOB_TTL_SECONDS", 24 * 3600))  # 24 h default
MAX_PENDING_JOBS = max(1, min(50, _env_int("STEMDECK_MAX_PENDING_JOBS", 3)))
TIMEOUT_FFMPEG = _env_int("STEMDECK_TIMEOUT_FFMPEG", 300)
TIMEOUT_ANALYZE = _env_int("STEMDECK_TIMEOUT_ANALYZE", 120)
TIMEOUT_DEMUCS_STALL = _env_int("STEMDECK_TIMEOUT_DEMUCS_STALL", 1800)


def ffmpeg_executable() -> str:
    """Return the preferred FFmpeg executable.

    In portable mode, setup places FFmpeg under DATA_DIR/ffmpeg. Prefer that
    binary when present; otherwise fall back to PATH so local dev and Docker
    keep working exactly as before.
    """
    return str(FFMPEG_BIN) if FFMPEG_BIN.is_file() else "ffmpeg"


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
