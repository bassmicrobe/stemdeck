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


def _set(job: Job, **fields: object) -> None:
    """Mutate Job fields. SSE polling picks up the change automatically."""
    old_status = job.status
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
            job.progress_started_at = time.time()
    if "status" in fields and job.status != old_status:
        job.status_started_at = time.time()


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
    mix_url: str | None = None  # populated when a strict subset was selected
    source_url: str | None = None  # original URL or "local:<filename>" for file uploads
    demucs_gain_db: float | None = None  # reversible gain applied to the Demucs working copy
    bass_repair_applied: bool = False
    phase_repair_applied: bool = False
    phase_repair_residual_ratio: float | None = None
    stem_denoise_applied: bool = False
    queue_position: int | None = None  # 1-based waiting position while status == queued
    queue_size: int = 0  # current number of queued jobs, for UI context
    error: str | None = None
    # Set by POST /api/jobs/{id}/cancel; consumed by pipeline stages.
    # Not surfaced via to_state() -- it's internal control state.
    cancel_requested: bool = False
    # Wall-clock timestamps for metadata-based sweep -- more predictable
    # than directory mtime, which can be touched by unrelated FS events.
    created_at: float = field(default_factory=time.time)
    status_started_at: float = field(default_factory=time.time)
    progress_started_at: float | None = None

    def elapsed_seconds(self) -> float:
        return max(0.0, time.time() - self.status_started_at)

    def total_elapsed_seconds(self) -> float:
        started_at = self.progress_started_at or self.created_at
        return max(0.0, time.time() - started_at)

    def eta_seconds(self) -> float | None:
        if self.status in ("done", "error", "cancelled"):
            return None
        progress = max(0.0, min(1.0, float(self.progress or 0.0)))
        if progress < 0.01 or progress >= 0.995:
            return None
        elapsed = max(0.0, time.time() - (self.progress_started_at or self.status_started_at))
        if elapsed < 2.0:
            return None
        return max(0.0, (elapsed / progress) - elapsed)

    def to_state(self) -> dict[str, Any]:
        eta = self.eta_seconds()
        return {
            "job_id": self.id,
            "status": self.status,
            "progress": self.progress,
            "progress_percent": round(max(0.0, min(1.0, float(self.progress or 0.0))) * 100),
            "stage": self.stage_message,
            "elapsed_seconds": round(self.elapsed_seconds(), 1),
            "total_elapsed_seconds": round(self.total_elapsed_seconds(), 1),
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
            "stem_presence": self.stem_presence,
            "sections": self.sections,
            "tags": self.tags,
            "stems": self.stems,
            "selected_stems": self.selected_stems,
            "quality_preset": self.quality_preset,
            "stem_denoise_preset": self.stem_denoise_preset,
            "mix_url": self.mix_url,
            "source_url": self.source_url,
            "bass_repair_applied": self.bass_repair_applied,
            "phase_repair_applied": self.phase_repair_applied,
            "phase_repair_residual_ratio": self.phase_repair_residual_ratio,
            "stem_denoise_applied": self.stem_denoise_applied,
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
        job.cancel_requested = False
        return job


_TRANSIENT_FIELDS = frozenset(("cancel_requested", "queue_position", "queue_size"))
_JOB_FIELDS = frozenset(f.name for f in dataclasses.fields(Job) if f.name not in _TRANSIENT_FIELDS)
