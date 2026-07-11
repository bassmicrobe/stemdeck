from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any, Literal


class JobCancelled(Exception):
    """Raised inside a pipeline stage when the job's cancel flag is set."""


JobStatus = Literal[
    "queued", "downloading", "analyzing", "separating", "processing", "done", "error", "cancelled"
]

_STEM_ORDER = ("vocals", "drums", "bass", "guitar", "piano", "other")
_STEM_LABELS = {
    "vocals": "Vocals",
    "drums": "Drums",
    "bass": "Bass",
    "guitar": "Guitar",
    "piano": "Piano",
    "other": "Other",
}
_QUALITY_LABELS = {
    "standard": "Standard",
    "high": "High",
    "max": "Max",
    "ultra": "Ultra",
}
_DENOISE_LABELS = {
    "off": "Noise off",
    "light": "Light denoise",
    "strong": "Strong denoise",
}
_DEVICE_LABELS = {
    "auto": "Auto",
    "cpu": "CPU",
    "mps": "Apple GPU",
    "cuda": "NVIDIA CUDA",
}


def _set(job: Job, **fields: object) -> None:
    """Mutate Job fields. SSE polling picks up the change automatically."""
    old_status = job.status
    old_terminal = old_status in ("done", "error", "cancelled")
    now = time.time()
    for k, v in fields.items():
        if k == "stage":
            job.stage_message = v  # type: ignore[assignment]
        else:
            setattr(job, k, v)
    if "progress" in fields and job.progress_started_at is None:
        try:
            progress = float(fields["progress"] or 0.0)
        except (TypeError, ValueError):
            progress = 0.0
        if progress > 0.001 and job.status not in ("queued", "done", "error", "cancelled"):
            job.progress_started_at = now
    if "status" in fields and job.status != old_status:
        job.status_started_at = now
        if job.status not in ("queued", "done", "error", "cancelled"):
            job.processing_started_at = job.processing_started_at or now
        if job.status in ("done", "error", "cancelled") and not old_terminal:
            job.completed_at = job.status_started_at
            started_at = job.processing_started_at or job.progress_started_at
            if started_at is not None:
                job.processing_elapsed_seconds = max(0.0, job.completed_at - started_at)
            else:
                job.processing_elapsed_seconds = 0.0


@dataclass
class Job:
    id: str
    status: JobStatus = "queued"
    progress: float = 0.0
    stage_message: str = "Queued"
    title: str | None = None
    duration_sec: float | None = None
    thumbnail: str | None = None
    bpm: int | None = None
    key: str | None = None
    scale: str | None = None  # "Major" / "Natural Minor"
    key_confidence: int | None = None  # 0-100 percent
    lufs: float | None = None  # ITU-R BS.1770 integrated loudness (dB)
    peak_db: float | None = None  # sample peak in dBFS (close to true peak)
    dynamic_range: float | None = None  # peak_db - integrated LUFS (dB)
    tempo_stability: int | None = None  # 0-100, beat interval consistency
    beat_times: list[float] | None = None  # detected beat timestamps, seconds
    downbeat_times: list[float] | None = None  # detected bar/downbeat timestamps, seconds
    beat_tracker: str | None = None  # librosa or beat_this:<model>
    chord_progression: list[dict] | None = None  # estimated chord guide segments
    chord_midi_url: str | None = None
    midi_analysis: dict[str, Any] | None = None
    midi_analysis_url: str | None = None
    stem_presence: dict[str, int] | None = None  # per-stem RMS 0-100
    sections: list[dict] | None = None  # [{id, name, start, end, color}]
    tags: list[str] | None = None  # YouTube tags + categories, lowercased, max 8
    stems: list[dict[str, str]] = field(default_factory=list)
    # Subset of stems the user chose at submit. The pipeline produces all
    # 6 regardless (Demucs htdemucs_6s is fixed), but after collect we
    # mix down only the selected ones into mix.wav so the user can
    # download a single track containing just their chosen stems.
    selected_stems: list[str] = field(default_factory=list)
    quality_preset: str = "standard"
    stem_denoise_preset: str = "off"
    demucs_device: str = "auto"
    demucs_device_resolved: str = ""
    demucs_engine: str = ""
    pcm_engine: str = ""
    mix_url: str | None = None  # populated when a strict subset was selected
    source_url: str | None = None  # original URL or "local:<filename>" for file uploads
    demucs_gain_db: float | None = None  # reversible gain applied to the Demucs working copy
    bass_repair_applied: bool = False
    phase_repair_applied: bool = False
    phase_repair_residual_ratio: float | None = None
    stem_denoise_applied: bool = False
    stem_gate_applied: bool = False
    stem_gate_threshold_db: float | None = None
    queue_position: int | None = None  # 1-based waiting position while status == queued
    queue_size: int = 0  # current number of queued jobs, for UI context
    error: str | None = None
    logs: list[dict[str, Any]] = field(default_factory=list)
    # Set by POST /api/jobs/{id}/cancel; consumed by pipeline stages.
    # Not surfaced via to_state() -- it's internal control state.
    cancel_requested: bool = False
    # Wall-clock timestamps for metadata-based sweep -- more predictable
    # than directory mtime, which can be touched by unrelated FS events.
    created_at: float = field(default_factory=time.time)
    status_started_at: float = field(default_factory=time.time)
    progress_started_at: float | None = None
    processing_started_at: float | None = None
    completed_at: float | None = None
    processing_elapsed_seconds: float | None = None

    def elapsed_seconds(self) -> float:
        return max(0.0, time.time() - self.status_started_at)

    def total_elapsed_seconds(self) -> float:
        started_at = self.created_at
        ended_at = self.completed_at or time.time()
        return max(0.0, ended_at - started_at)

    def processing_seconds(self) -> float:
        if self.processing_elapsed_seconds is not None:
            return max(0.0, self.processing_elapsed_seconds)
        started_at = self.processing_started_at or self.progress_started_at
        if started_at is None:
            return 0.0
        ended_at = self.completed_at or time.time()
        return max(0.0, ended_at - started_at)

    def eta_seconds(self) -> float | None:
        if self.status in ("done", "error", "cancelled"):
            return None
        progress = max(0.0, min(1.0, float(self.progress or 0.0)))
        if progress < 0.01 or progress >= 0.995:
            return None
        elapsed = self.processing_seconds()
        if elapsed <= 0.0 and self.processing_started_at is None and self.progress_started_at is None:
            elapsed = max(0.0, time.time() - self.status_started_at)
        if elapsed < 2.0:
            return None
        return max(0.0, (elapsed / progress) - elapsed)

    def profile_stems(self) -> tuple[str, ...]:
        selected = set(self.selected_stems or [])
        if not selected:
            selected = {
                stem["name"]
                for stem in self.stems
                if isinstance(stem, dict) and stem.get("name") in _STEM_ORDER
            }
        ordered = tuple(name for name in _STEM_ORDER if name in selected)
        return ordered or _STEM_ORDER

    def profile_key(self) -> str:
        stems = ",".join(self.profile_stems())
        quality = (self.quality_preset or "standard").lower()
        denoise = (self.stem_denoise_preset or "off").lower()
        device = (self.demucs_device or "auto").lower()
        resolved = (self.demucs_device_resolved or "auto").lower()
        return f"quality={quality}|denoise={denoise}|device={device}:{resolved}|stems={stems}"

    def device_label(self) -> str:
        choice = (self.demucs_device or "auto").lower()
        resolved = (self.demucs_device_resolved or "").lower()
        choice_label = _DEVICE_LABELS.get(choice, choice.upper())
        resolved_label = _DEVICE_LABELS.get(resolved, resolved.upper()) if resolved else ""
        if choice == "auto" and resolved_label:
            return f"{choice_label} ({resolved_label})"
        return choice_label

    def profile_label(self) -> str:
        stems = self.profile_stems()
        quality = _QUALITY_LABELS.get((self.quality_preset or "standard").lower(), self.quality_preset)
        denoise = _DENOISE_LABELS.get(
            (self.stem_denoise_preset or "off").lower(),
            self.stem_denoise_preset or "Noise off",
        )
        if stems == _STEM_ORDER:
            stems_label = "All 6-stem"
        elif len(stems) == 4 and set(stems) == {"vocals", "drums", "bass", "other"}:
            stems_label = "4-stem"
        else:
            stems_label = "+".join(_STEM_LABELS.get(name, name.title()) for name in stems)
        return f"{quality} / {denoise} / {self.device_label()} / {stems_label}"

    def to_state(self) -> dict[str, Any]:
        eta = self.eta_seconds()
        total_elapsed = self.total_elapsed_seconds()
        processing_elapsed = self.processing_seconds()
        return {
            "job_id": self.id,
            "status": self.status,
            "progress": self.progress,
            "progress_percent": round(max(0.0, min(1.0, float(self.progress or 0.0))) * 100),
            "stage": self.stage_message,
            "elapsed_seconds": round(self.elapsed_seconds(), 1),
            "total_elapsed_seconds": round(total_elapsed, 1),
            "processing_elapsed_seconds": round(processing_elapsed, 1),
            "timer_started_at": self.created_at,
            "processing_started_at": self.processing_started_at,
            "completed_at": self.completed_at,
            "server_time": time.time(),
            "eta_seconds": None if eta is None else round(eta, 1),
            "title": self.title,
            "duration": self.duration_sec,
            "thumbnail": self.thumbnail,
            "bpm": self.bpm,
            "key": self.key,
            "scale": self.scale,
            "key_confidence": self.key_confidence,
            "lufs": self.lufs,
            "peak_db": self.peak_db,
            "dynamic_range": self.dynamic_range,
            "tempo_stability": self.tempo_stability,
            "beat_times": self.beat_times,
            "downbeat_times": self.downbeat_times,
            "beat_tracker": self.beat_tracker,
            "chord_progression": self.chord_progression,
            "chord_midi_url": self.chord_midi_url,
            "midi_analysis_url": self.midi_analysis_url,
            "stem_presence": self.stem_presence,
            "sections": self.sections,
            "tags": self.tags,
            "stems": self.stems,
            "selected_stems": self.selected_stems,
            "quality_preset": self.quality_preset,
            "stem_denoise_preset": self.stem_denoise_preset,
            "demucs_device": self.demucs_device,
            "demucs_device_resolved": self.demucs_device_resolved,
            "demucs_engine": self.demucs_engine,
            "pcm_engine": self.pcm_engine,
            "profile_key": self.profile_key(),
            "profile_label": self.profile_label(),
            "mix_url": self.mix_url,
            "source_url": self.source_url,
            "bass_repair_applied": self.bass_repair_applied,
            "phase_repair_applied": self.phase_repair_applied,
            "phase_repair_residual_ratio": self.phase_repair_residual_ratio,
            "stem_denoise_applied": self.stem_denoise_applied,
            "stem_gate_applied": self.stem_gate_applied,
            "stem_gate_threshold_db": self.stem_gate_threshold_db,
            "queue_position": self.queue_position,
            "queue_size": self.queue_size,
            "error": self.error,
            "created_at": self.created_at,
        }

    def to_record(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in _JOB_FIELDS}

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> Job:
        fields = {key: value for key, value in data.items() if key in _JOB_FIELDS}
        job_id = str(fields.pop("id", "")).strip()
        if not job_id:
            raise ValueError("job record missing id")
        job = cls(id=job_id)
        for key, value in fields.items():
            setattr(job, key, value)
        if not isinstance(job.logs, list):
            job.logs = []
        else:
            job.logs = [entry for entry in job.logs[-300:] if isinstance(entry, dict)]
        job.cancel_requested = False
        return job


_TRANSIENT_FIELDS = frozenset(("cancel_requested", "queue_position", "queue_size"))
_JOB_FIELDS = frozenset(f.name for f in dataclasses.fields(Job) if f.name not in _TRANSIENT_FIELDS)
