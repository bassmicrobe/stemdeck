from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.models import Job
from app.pipeline.analyze import _load_audio_ffmpeg

logger = logging.getLogger("stemdeck.chords")

_PITCHES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_TPB = 480
_CHORD_STEM_WEIGHTS = {
    "piano": 1.25,
    "guitar": 1.10,
    "other": 0.55,
}
_BASS_ROOT_BOOST = 0.22
_BASS_BLOCK_ROOT_BOOST = 0.36


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


@dataclass(frozen=True)
class _ChordCandidate:
    label: str
    root: int | None
    intervals: tuple[int, ...]
    score: float
    confidence: float


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
_MAJOR_KEY_PROFILE = np.asarray(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    dtype=np.float32,
)
_MINOR_KEY_PROFILE = np.asarray(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
    dtype=np.float32,
)
_DIATONIC_MAJOR = {0, 2, 4, 5, 7, 9, 11}
_DIATONIC_MINOR = {0, 2, 3, 5, 7, 8, 10}


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


def _midi_text(value: str | None, fallback: str, limit: int) -> bytes:
    """Return DAW-friendly MIDI meta text.

    Standard MIDI files do not define a reliable Unicode encoding for text meta
    events. Some DAWs interpret UTF-8 bytes as a local 8-bit codepage, which
    makes Japanese and other multibyte titles appear as mojibake. Keep metadata
    ASCII-only so track names never display as broken text.
    """
    text = unicodedata.normalize("NFKD", (value or "").strip())
    text = text.encode("ascii", "ignore").decode("ascii")
    text = " ".join(text.split())
    if not text:
        text = fallback
    return text.encode("ascii")[:limit]


def _normalize_chroma(chroma: np.ndarray) -> np.ndarray | None:
    total = float(np.sum(chroma))
    if total <= 1e-8:
        return None
    return chroma.astype(np.float32, copy=False) / total


def _template_vector(root: int, intervals: tuple[int, ...], weights: tuple[float, ...]) -> np.ndarray:
    template = np.zeros(12, dtype=np.float32)
    for interval, weight in zip(intervals, weights, strict=False):
        template[(root + interval) % 12] = weight
    template_sum = float(np.sum(template))
    if template_sum > 0:
        template /= template_sum
    return template


def _estimate_key_context(vectors: list[np.ndarray]) -> tuple[int, str] | None:
    usable = [_normalize_chroma(vector) for vector in vectors]
    usable = [vector for vector in usable if vector is not None]
    if not usable:
        return None
    chroma = np.mean(np.stack(usable), axis=0)
    chroma = chroma / float(np.sum(chroma))
    major = _MAJOR_KEY_PROFILE / float(np.sum(_MAJOR_KEY_PROFILE))
    minor = _MINOR_KEY_PROFILE / float(np.sum(_MINOR_KEY_PROFILE))

    scores: list[tuple[float, int, str]] = []
    for root in range(12):
        scores.append((float(np.dot(chroma, np.roll(major, root))), root, "major"))
        scores.append((float(np.dot(chroma, np.roll(minor, root))), root, "minor"))
    scores.sort(reverse=True)
    if len(scores) > 1 and scores[0][0] - scores[1][0] < 0.0015:
        return None
    return scores[0][1], scores[0][2]


def _key_bias(root: int, intervals: tuple[int, ...], key_context: tuple[int, str] | None) -> float:
    if key_context is None:
        return 0.0
    tonic, mode = key_context
    scale = _DIATONIC_MAJOR if mode == "major" else _DIATONIC_MINOR
    tones = {((root + interval) - tonic) % 12 for interval in intervals}
    if not tones:
        return 0.0
    affinity = sum(1 for tone in tones if tone in scale) / len(tones)
    bias = (affinity - 0.5) * 0.028
    root_degree = (root - tonic) % 12
    if root_degree == 0:
        bias += 0.008
    elif root_degree in {5, 7}:
        bias += 0.005
    if affinity < 0.6:
        bias -= 0.010
    return bias


def _rank_chord_candidates(
    chroma: np.ndarray,
    *,
    key_context: tuple[int, str] | None = None,
    limit: int = 8,
) -> list[_ChordCandidate]:
    normalized = _normalize_chroma(chroma)
    if normalized is None:
        return [_ChordCandidate("N.C.", None, (), 0.0, 0.0)]

    scored: list[tuple[float, str, int, tuple[int, ...]]] = []
    for root in range(12):
        for suffix, intervals, weights in _CHORD_TEMPLATES:
            template = _template_vector(root, intervals, weights)
            support = float(np.dot(normalized, template))
            off_energy = float(np.sum(normalized[template <= 0.001]))
            root_energy = float(normalized[root])
            fifth_energy = float(normalized[(root + 7) % 12])
            score = (
                support
                + (0.20 * root_energy)
                + (0.035 * fifth_energy)
                - (0.12 * off_energy)
                + _key_bias(root, intervals, key_context)
            )
            scored.append((score, f"{_PITCHES[root]}{suffix}", root, intervals))

    scored.sort(reverse=True)
    candidates: list[_ChordCandidate] = []
    for idx, (score, label, root, intervals) in enumerate(scored[: max(1, limit)]):
        next_score = scored[idx + 1][0] if idx + 1 < len(scored) else score - 0.04
        margin = max(0.0, score - next_score)
        confidence = max(0.0, min(1.0, (score / 0.17) + (margin * 1.8)))
        candidates.append(
            _ChordCandidate(
                label=label,
                root=root,
                intervals=intervals,
                score=score,
                confidence=confidence,
            )
        )

    if not candidates or candidates[0].score < 0.055:
        return [_ChordCandidate("N.C.", None, (), 0.0, 0.0)]
    return candidates


def _score_chord(chroma: np.ndarray) -> tuple[str, int | None, tuple[int, ...], float]:
    candidates = _rank_chord_candidates(chroma, limit=1)
    best = candidates[0]
    if best.root is None:
        return "N.C.", None, (), 0.0
    return best.label, best.root, best.intervals, best.confidence


def _transition_bonus(prev: _ChordCandidate, current: _ChordCandidate) -> float:
    if prev.label == current.label:
        return 0.020
    if prev.root is None or current.root is None:
        return -0.012
    if prev.root == current.root:
        return 0.006
    motion = (current.root - prev.root) % 12
    if motion in {5, 7}:
        return 0.004
    penalty = -0.010
    if current.confidence < 0.68:
        penalty -= 0.010
    return penalty


def _select_chord_sequence(vectors: list[np.ndarray]) -> list[_ChordCandidate]:
    if not vectors:
        return []
    key_context = _estimate_key_context(vectors)
    ranked = [_rank_chord_candidates(vector, key_context=key_context, limit=8) for vector in vectors]
    if len(ranked) == 1:
        return [ranked[0][0]]

    dp: list[list[float]] = []
    back: list[list[int]] = []
    dp.append([candidate.score for candidate in ranked[0]])
    back.append([-1 for _ in ranked[0]])
    for idx in range(1, len(ranked)):
        row: list[float] = []
        back_row: list[int] = []
        for current in ranked[idx]:
            best_score = -1e9
            best_prev = 0
            for prev_idx, prev in enumerate(ranked[idx - 1]):
                score = dp[idx - 1][prev_idx] + current.score + _transition_bonus(prev, current)
                if score > best_score:
                    best_score = score
                    best_prev = prev_idx
            row.append(best_score)
            back_row.append(best_prev)
        dp.append(row)
        back.append(back_row)

    cursor = int(np.argmax(dp[-1]))
    selected: list[_ChordCandidate] = []
    for idx in range(len(ranked) - 1, -1, -1):
        selected.append(ranked[idx][cursor])
        cursor = back[idx][cursor]
        if cursor < 0 and idx > 0:
            cursor = 0
    selected.reverse()
    return selected


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
    paths: list[tuple[str, Path, float]] = [("original", source, 0.85)]
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
    if chroma.shape[1] >= 3:
        chroma = (np.roll(chroma, 1, axis=1) + (2.0 * chroma) + np.roll(chroma, -1, axis=1)) / 4.0
        chroma[:, 0] = chroma[:, 1]
        chroma[:, -1] = chroma[:, -2]
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


def _combine_harmony_chroma(
    chord_sources: list[_ChromaSource], start: float, end: float
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
    return combined


def _bass_root_hint(
    bass_source: _ChromaSource | None, start: float, end: float
) -> tuple[int, float] | None:
    if bass_source is None:
        return None
    bass_vector = _mean_normalized_chroma(bass_source, start, end)
    if bass_vector is None:
        return None
    root = int(np.argmax(bass_vector))
    ordered = np.sort(bass_vector)
    strongest = float(ordered[-1])
    runner_up = float(ordered[-2]) if len(ordered) > 1 else 0.0
    if strongest < 0.16 or strongest < runner_up * 1.12:
        return None
    clarity = min(1.0, max(0.0, (strongest - runner_up) * 4.0))
    return root, clarity


def _apply_bass_root_hint(
    vector: np.ndarray, hint: tuple[int, float] | None, *, boost: float = _BASS_ROOT_BOOST
) -> np.ndarray:
    combined = np.asarray(vector, dtype=np.float32).copy()
    if hint is None:
        return combined
    root, clarity = hint
    combined[root] += boost * clarity
    total = float(np.sum(combined))
    return combined / total if total > 0 else combined


def _combine_segment_chroma(
    chord_sources: list[_ChromaSource],
    bass_source: _ChromaSource | None,
    start: float,
    end: float,
) -> np.ndarray | None:
    combined = _combine_harmony_chroma(chord_sources, start, end)
    if combined is None:
        return None
    return _apply_bass_root_hint(combined, _bass_root_hint(bass_source, start, end))


def _combine_beatwise_chroma(
    chord_sources: list[_ChromaSource],
    bass_source: _ChromaSource | None,
    beat_bounds: list[float],
    start_beat: int,
    end_beat: int,
) -> np.ndarray | None:
    start_idx = max(0, min(int(start_beat), len(beat_bounds) - 1))
    end_idx = max(start_idx + 1, min(int(end_beat), len(beat_bounds) - 1))
    start = beat_bounds[start_idx]
    end = beat_bounds[end_idx]
    whole = _combine_harmony_chroma(chord_sources, start, end)

    weighted: list[np.ndarray] = []
    weights: list[float] = []
    bass_votes = np.zeros(12, dtype=np.float32)
    for idx in range(start_idx, end_idx):
        beat_start = beat_bounds[idx]
        beat_end = beat_bounds[idx + 1]
        if beat_end <= beat_start:
            continue
        beat_vector = _combine_harmony_chroma(chord_sources, beat_start, beat_end)
        if beat_vector is None:
            continue
        hint = _bass_root_hint(bass_source, beat_start, beat_end)
        beat_with_bass = _apply_bass_root_hint(beat_vector, hint, boost=_BASS_ROOT_BOOST * 1.15)
        _, _, _, confidence = _score_chord(beat_with_bass)
        duration_weight = max(0.05, beat_end - beat_start)
        weight = duration_weight * (0.70 + confidence)
        weighted.append(beat_with_bass * weight)
        weights.append(weight)
        if hint is not None:
            root, clarity = hint
            bass_votes[root] += clarity * duration_weight

    if whole is not None:
        weighted.append(whole * 1.35)
        weights.append(1.35)

    if not weighted or not weights:
        return None
    combined = np.sum(np.stack(weighted), axis=0) / float(np.sum(weights))
    if float(np.sum(bass_votes)) > 0:
        root = int(np.argmax(bass_votes))
        ordered = np.sort(bass_votes)
        strongest = float(ordered[-1])
        runner_up = float(ordered[-2]) if len(ordered) > 1 else 0.0
        if strongest >= max(0.22, runner_up * 1.15):
            clarity = min(1.0, max(0.0, strongest / max(1e-6, float(np.sum(bass_votes)))))
            combined = _apply_bass_root_hint(
                combined,
                (root, clarity),
                boost=_BASS_BLOCK_ROOT_BOOST,
            )
    total = float(np.sum(combined))
    return combined / total if total > 0 else combined


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

    spans: list[tuple[float, int, float, int, np.ndarray]] = []
    for (start, start_beat), (end, end_beat) in zip(bounds, bounds[1:], strict=False):
        if end <= start:
            continue
        vector = _combine_beatwise_chroma(
            chord_sources,
            bass_source,
            beat_bounds,
            start_beat,
            end_beat,
        )
        if vector is None:
            vector = _combine_segment_chroma(chord_sources, bass_source, start, end)
        if vector is None:
            continue
        spans.append((start, start_beat, end, end_beat, vector))

    selected = _select_chord_sequence([vector for *_, vector in spans])
    segments: list[ChordSegment] = []
    for (start, start_beat, end, end_beat, _), chord in zip(spans, selected, strict=False):
        segments.append(
            ChordSegment(
                label=chord.label,
                start=round(start, 3),
                end=round(end, 3),
                root=chord.root,
                intervals=chord.intervals,
                confidence=round(chord.confidence, 3),
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
    conductor_events += _meta(0, 0x03, _midi_text("LayerLab Tempo", "LayerLab Tempo", 64))
    conductor_events += _meta(0, 0x51, tempo_us.to_bytes(3, "big"))
    conductor_events += _meta(0, 0x58, bytes((4, 2, 24, 8)))  # 4/4, 24 MIDI clocks/click.
    conductor_events += _meta(0, 0x01, _midi_text(f"BPM {tempo_bpm}", f"BPM {tempo_bpm}", 32))
    conductor_events += _meta(0, 0x2F, b"")

    events = bytearray()
    events += _meta(0, 0x03, _midi_text(title, "LayerLab Chord Progression", 120))
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
        label = _midi_text(seg.label, "N.C.", 48)
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
