from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.models import Job
from app.pipeline.analyze import _load_audio_ffmpeg

logger = logging.getLogger("stemdeck.chords")

_PITCHES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_TPB = 480
_CHORD_STEM_WEIGHTS = {
    "piano": 0.9,
    "guitar": 0.85,
    "other": 0.65,
}
_BASS_ROOT_BOOST = 0.22


@dataclass(frozen=True)
class ChordSegment:
    label: str
    start: float
    end: float
    root: int | None
    intervals: tuple[int, ...]
    confidence: float
    start_beat: int | None = None
    end_beat: int | None = None


@dataclass(frozen=True)
class _ChromaSource:
    name: str
    weight: float
    chroma: np.ndarray
    frame_times: np.ndarray


_CHORD_TEMPLATES: tuple[tuple[str, tuple[int, ...], tuple[float, ...]], ...] = (
    ("", (0, 4, 7), (1.0, 0.82, 0.72)),
    ("m", (0, 3, 7), (1.0, 0.82, 0.72)),
    ("7", (0, 4, 7, 10), (1.0, 0.78, 0.68, 0.42)),
    ("maj7", (0, 4, 7, 11), (1.0, 0.78, 0.68, 0.42)),
    ("m7", (0, 3, 7, 10), (1.0, 0.78, 0.68, 0.42)),
    ("sus2", (0, 2, 7), (1.0, 0.66, 0.72)),
    ("sus4", (0, 5, 7), (1.0, 0.66, 0.72)),
    ("dim", (0, 3, 6), (1.0, 0.72, 0.62)),
)


def _varlen(value: int) -> bytes:
    value = max(0, int(value))
    parts = [value & 0x7F]
    value >>= 7
    while value:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(parts))


def _meta(delta: int, meta_type: int, payload: bytes) -> bytes:
    return _varlen(delta) + bytes((0xFF, meta_type)) + _varlen(len(payload)) + payload


def _midi_event(delta: int, status: int, note: int, velocity: int) -> bytes:
    return _varlen(delta) + bytes((status, note, velocity))


def _score_chord(chroma: np.ndarray) -> tuple[str, int | None, tuple[int, ...], float]:
    total = float(np.sum(chroma))
    if total <= 1e-8:
        return "N.C.", None, (), 0.0
    chroma = chroma.astype(np.float32, copy=False) / total

    best: tuple[float, str, int, tuple[int, ...]] | None = None
    for root in range(12):
        for suffix, intervals, weights in _CHORD_TEMPLATES:
            template = np.zeros(12, dtype=np.float32)
            for interval, weight in zip(intervals, weights, strict=False):
                template[(root + interval) % 12] = weight
            template_sum = float(np.sum(template))
            template /= template_sum
            support = float(np.dot(chroma, template))
            off_energy = float(np.sum(chroma[template <= 0.001]))
            root_energy = float(chroma[root])
            score = support + (0.18 * root_energy) - (0.10 * off_energy)
            label = f"{_PITCHES[root]}{suffix}"
            if best is None or score > best[0]:
                best = (score, label, root, intervals)

    if best is None or best[0] < 0.055:
        return "N.C.", None, (), 0.0
    confidence = max(0.0, min(1.0, best[0] / 0.16))
    return best[1], best[2], best[3], confidence


def _merge_segments(segments: list[ChordSegment]) -> list[ChordSegment]:
    merged: list[ChordSegment] = []
    for seg in segments:
        if merged and merged[-1].label == seg.label:
            prev = merged[-1]
            merged[-1] = ChordSegment(
                prev.label,
                prev.start,
                seg.end,
                prev.root,
                prev.intervals,
                max(prev.confidence, seg.confidence),
                prev.start_beat,
                seg.end_beat,
            )
        else:
            merged.append(seg)
    return merged


def _available_chord_source_paths(
    source: Path, stems_dir: Path | None
) -> tuple[list[tuple[str, Path, float]], Path | None]:
    paths: list[tuple[str, Path, float]] = [("original", source, 1.0)]
    bass_path: Path | None = None
    if stems_dir is None:
        return paths, bass_path
    for name, weight in _CHORD_STEM_WEIGHTS.items():
        path = stems_dir / f"{name}.wav"
        if path.is_file():
            paths.append((name, path, weight))
    candidate_bass = stems_dir / "bass.wav"
    if candidate_bass.is_file():
        bass_path = candidate_bass
    return paths, bass_path


def _load_chroma_source(
    path: Path,
    *,
    name: str,
    weight: float,
    end_time: float,
    harmonic: bool,
) -> _ChromaSource | None:
    loaded = _load_audio_ffmpeg(path, sr=22050, duration=min(180.0, end_time + 1.0))
    if loaded is None:
        return None

    import librosa

    y, sr = loaded
    if float(np.sqrt(np.mean(np.square(y)))) < 1e-6:
        return None
    hop_length = 512
    y_chroma = librosa.effects.harmonic(y) if harmonic else y
    chroma = librosa.feature.chroma_cqt(y=y_chroma, sr=sr, hop_length=hop_length)
    frame_times = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=sr, hop_length=hop_length)
    return _ChromaSource(name=name, weight=weight, chroma=chroma, frame_times=frame_times)


def _mean_normalized_chroma(source: _ChromaSource, start: float, end: float) -> np.ndarray | None:
    mask = (source.frame_times >= start) & (source.frame_times < end)
    if not np.any(mask):
        return None
    vector = np.asarray(source.chroma[:, mask].mean(axis=1), dtype=np.float32)
    total = float(np.sum(vector))
    if total <= 1e-8:
        return None
    return vector / total


def _combine_segment_chroma(
    chord_sources: list[_ChromaSource],
    bass_source: _ChromaSource | None,
    start: float,
    end: float,
) -> np.ndarray | None:
    combined = np.zeros(12, dtype=np.float32)
    total_weight = 0.0
    for source in chord_sources:
        vector = _mean_normalized_chroma(source, start, end)
        if vector is None:
            continue
        weight = source.weight
        if source.name != "original":
            _, _, _, confidence = _score_chord(vector)
            if confidence < 0.25:
                continue
            weight *= 0.35 + (0.65 * confidence)
        combined += vector * weight
        total_weight += weight

    if total_weight <= 0:
        return None
    combined /= total_weight

    if bass_source is not None:
        bass_vector = _mean_normalized_chroma(bass_source, start, end)
        if bass_vector is not None:
            root = int(np.argmax(bass_vector))
            ordered = np.sort(bass_vector)
            strongest = float(ordered[-1])
            runner_up = float(ordered[-2]) if len(ordered) > 1 else 0.0
            if strongest >= 0.16 and strongest >= runner_up * 1.12:
                clarity = min(1.0, max(0.0, (strongest - runner_up) * 4.0))
                combined[root] += _BASS_ROOT_BOOST * clarity
                combined /= float(np.sum(combined))

    return combined


def detect_chord_segments(
    source: Path,
    beat_times: list[float] | None,
    *,
    duration_sec: float | None = None,
    beats_per_chord: int = 4,
    stems_dir: Path | None = None,
) -> list[ChordSegment]:
    """Estimate sustained chord labels between detected beats.

    This is intentionally a lightweight guide-track generator, not a full
    transcription engine. We average chroma between beats, classify simple
    chord templates, then merge adjacent identical labels into long "white"
    chords.
    """
    if not beat_times or len(beat_times) < 2:
        return []
    end_time = min(float(duration_sec or beat_times[-1]), beat_times[-1])
    if end_time <= 0:
        return []
    chord_paths, bass_path = _available_chord_source_paths(source, stems_dir)
    chord_sources = [
        loaded
        for name, path, weight in chord_paths
        if (loaded := _load_chroma_source(path, name=name, weight=weight, end_time=end_time, harmonic=True))
        is not None
    ]
    if not chord_sources:
        return []
    bass_source = (
        _load_chroma_source(bass_path, name="bass", weight=1.0, end_time=end_time, harmonic=False)
        if bass_path is not None
        else None
    )

    beat_bounds = [float(t) for t in beat_times if 0 <= float(t) <= end_time]
    if len(beat_bounds) < 2:
        return []
    stride = max(1, int(beats_per_chord))
    bounds = [(t, idx) for idx, t in enumerate(beat_bounds) if idx % stride == 0]
    last_beat_index = len(beat_bounds) - 1
    if beat_bounds[-1] > bounds[-1][0]:
        bounds.append((beat_bounds[-1], last_beat_index))
    if duration_sec and duration_sec > bounds[-1][0] + 0.2:
        tail = bounds[-1][0] - bounds[-2][0] if len(bounds) > 1 else 60.0 / 120.0
        bounds.append((min(float(duration_sec), bounds[-1][0] + tail), last_beat_index + stride))

    segments: list[ChordSegment] = []
    for (start, start_beat), (end, end_beat) in zip(bounds, bounds[1:], strict=False):
        if end <= start:
            continue
        vector = _combine_segment_chroma(chord_sources, bass_source, start, end)
        if vector is None:
            continue
        label, root, intervals, confidence = _score_chord(vector)
        segments.append(
            ChordSegment(
                label=label,
                start=round(start, 3),
                end=round(end, 3),
                root=root,
                intervals=intervals,
                confidence=round(confidence, 3),
                start_beat=start_beat,
                end_beat=end_beat,
            )
        )
    return _merge_segments(segments)


def _note_numbers(root: int, intervals: tuple[int, ...]) -> list[int]:
    base = 48 + root  # C3 octave, safe for chord-pad playback.
    notes = [base + interval for interval in intervals]
    if notes:
        notes.append(base + 12)
    return sorted({max(0, min(127, n)) for n in notes})


def write_chord_midi(
    path: Path,
    segments: list[ChordSegment],
    *,
    bpm: int | None,
    title: str | None = None,
) -> None:
    tempo_bpm = max(30, min(240, int(bpm or 120)))
    tempo_us = int(round(60_000_000 / tempo_bpm))
    conductor_events = bytearray()
    conductor_events += _meta(0, 0x03, b"STEMDECK Tempo")
    conductor_events += _meta(0, 0x51, tempo_us.to_bytes(3, "big"))
    conductor_events += _meta(0, 0x58, bytes((4, 2, 24, 8)))  # 4/4, 24 MIDI clocks/click.
    conductor_events += _meta(0, 0x01, f"BPM {tempo_bpm}".encode("ascii"))
    conductor_events += _meta(0, 0x2F, b"")

    events = bytearray()
    events += _meta(0, 0x03, (title or "STEMDECK Chord Progression").encode("utf-8")[:120])
    events += _varlen(0) + bytes((0xC0, 0))  # Acoustic Grand Piano

    cursor_ticks = 0
    for seg in segments:
        if seg.start_beat is not None and seg.end_beat is not None:
            start_tick = max(0, int(seg.start_beat) * _TPB)
            end_tick = max(start_tick + 1, int(seg.end_beat) * _TPB)
        else:
            start_tick = int(round(seg.start * tempo_bpm / 60.0 * _TPB))
            end_tick = max(start_tick + 1, int(round(seg.end * tempo_bpm / 60.0 * _TPB)))
        delta = max(0, start_tick - cursor_ticks)
        label = seg.label.encode("utf-8")[:48]
        events += _meta(delta, 0x01, label)
        cursor_ticks = start_tick
        if seg.root is not None and seg.intervals:
            notes = _note_numbers(seg.root, seg.intervals)
            delta = 0
            for note in notes:
                events += _midi_event(delta, 0x90, note, 62)
                delta = 0
            duration = max(1, end_tick - start_tick)
            for idx, note in enumerate(notes):
                events += _midi_event(duration if idx == 0 else 0, 0x80, note, 0)
            cursor_ticks = end_tick
        else:
            events += _meta(max(1, end_tick - cursor_ticks), 0x01, b"")
            cursor_ticks = end_tick
    events += _meta(0, 0x2F, b"")

    header = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big")
    header += (2).to_bytes(2, "big") + _TPB.to_bytes(2, "big")
    conductor = b"MTrk" + len(conductor_events).to_bytes(4, "big") + bytes(conductor_events)
    track = b"MTrk" + len(events).to_bytes(4, "big") + bytes(events)
    path.write_bytes(header + conductor + track)


def generate_chord_midi(
    job: Job, source: Path, job_dir: Path, *, stems_dir: Path | None = None
) -> Path | None:
    try:
        segments = detect_chord_segments(
            source,
            job.beat_times,
            duration_sec=job.duration_sec,
            stems_dir=stems_dir or (job_dir / "stems"),
        )
        if not segments:
            return None
        out = job_dir / "stems" / "chords.mid"
        out.parent.mkdir(exist_ok=True)
        write_chord_midi(out, segments, bpm=job.bpm, title=job.title)
        job.chord_progression = [
            {
                "label": seg.label,
                "start": seg.start,
                "end": seg.end,
                "start_beat": seg.start_beat,
                "end_beat": seg.end_beat,
                "confidence": seg.confidence,
            }
            for seg in segments
        ]
        job.chord_midi_url = f"/api/jobs/{job.id}/chords.mid"
        logger.info("chord MIDI generated for job %s (%s segments)", job.id, len(segments))
        return out
    except Exception:
        logger.warning("chord MIDI generation skipped for job %s", job.id, exc_info=True)
        return None
