from __future__ import annotations

import csv
import logging
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import numpy as np

from app.core.config import BACKGROUND_CPU_THREADS
from app.core.models import Job, JobCancelled
from app.pipeline.analyze import _load_audio_ffmpeg
from app.pipeline.midi_analysis import analyze_chord_midi, write_midi_analysis

logger = logging.getLogger("stemdeck.chords")

_PITCHES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_TPB = 480
_CHORD_STEM_WEIGHTS = {
    "piano": 1.25,
    "guitar": 1.10,
    # Recent source-separation ACR evaluation found that emphasizing the
    # accompaniment stem improves chord recall. Reliability weighting below
    # prevents a loud single-note riff from dominating the estimate.
    "other": 0.95,
}
CHORD_MIDI_STYLES = ("auto", "triads", "sevenths")
CHORD_MIDI_GRIDS = ("beat", "bar")
_BASS_ROOT_BOOST = 0.16
_BASS_BLOCK_ROOT_BOOST = 0.24
_MIN_STABLE_CHORD_CONFIDENCE = 0.56
_CHROMA_CHUNK_SEC = 180.0


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
_PITCH_TO_INDEX = {pitch: idx for idx, pitch in enumerate(_PITCHES)}
_FLAT_TO_SHARP = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
_QUALITY_ALIASES = {
    "maj": "",
    "major": "",
    "min": "m",
    "minor": "m",
    "min7": "m7",
    "min6": "m6",
    "maj6": "6",
    "min9": "m9",
    "minmaj7": "mMaj7",
    "min-maj7": "mMaj7",
}


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


def normalize_chord_midi_style(value: str | None) -> str:
    style = (value or "auto").strip().lower()
    return style if style in CHORD_MIDI_STYLES else "auto"


def normalize_chord_midi_grid(value: str | None) -> str:
    grid = (value or "beat").strip().lower()
    return grid if grid in CHORD_MIDI_GRIDS else "beat"


def _canonical_chord_label(label: str | None) -> str:
    """Normalize JAMS/MIREX and display labels to LayerLab's compact form."""
    text = (label or "").strip().replace("♯", "#").replace("♭", "b")
    if not text or text.upper() in {"N", "N.C.", "NC", "X"}:
        return "N.C."
    if ":" in text:
        root_text, quality_text = text.split(":", 1)
    else:
        root_text = next(
            (pitch for pitch in sorted((*_PITCHES, *_FLAT_TO_SHARP), key=len, reverse=True) if text.startswith(pitch)),
            "",
        )
        quality_text = text[len(root_text) :]
    root = _FLAT_TO_SHARP.get(root_text, root_text)
    if root not in _PITCH_TO_INDEX:
        return "N.C."
    quality, slash, bass = quality_text.partition("/")
    quality = _QUALITY_ALIASES.get(quality.lower(), quality)
    suffix = f"/{_FLAT_TO_SHARP.get(bass, bass)}" if slash and bass in (*_PITCHES, *_FLAT_TO_SHARP) else ""
    return f"{root}{quality}{suffix}"


def _split_chord_label(label: str | None) -> tuple[int | None, str]:
    text = _canonical_chord_label(label)
    if text == "N.C.":
        return None, ""
    for pitch in sorted(_PITCH_TO_INDEX, key=len, reverse=True):
        if text.startswith(pitch):
            return _PITCH_TO_INDEX[pitch], text[len(pitch) :]
    return None, ""


def _segment_from_metadata(item: dict) -> ChordSegment | None:
    if not isinstance(item, dict):
        return None
    label = _canonical_chord_label(str(item.get("label") or "N.C."))
    root, suffix = _split_chord_label(label)
    intervals = _intervals_for_suffix(suffix) if root is not None else ()
    try:
        start = float(item.get("start", 0.0))
        end = float(item.get("end", start))
        confidence = float(item.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    start_beat = _optional_int(item.get("start_beat"))
    end_beat = _optional_int(item.get("end_beat"))
    if end <= start and (start_beat is None or end_beat is None or end_beat <= start_beat):
        return None
    return ChordSegment(label, start, end, root, intervals, confidence, start_beat, end_beat)


def _optional_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _intervals_for_suffix(suffix: str) -> tuple[int, ...]:
    suffix = suffix.split("/", 1)[0]
    if suffix == "m":
        return (0, 3, 7)
    if suffix == "7":
        return (0, 4, 7, 10)
    if suffix == "maj7":
        return (0, 4, 7, 11)
    if suffix == "m7":
        return (0, 3, 7, 10)
    if suffix == "sus2":
        return (0, 2, 7)
    if suffix == "sus4":
        return (0, 5, 7)
    if suffix == "dim":
        return (0, 3, 6)
    if suffix == "dim7":
        return (0, 3, 6, 9)
    if suffix == "hdim7":
        return (0, 3, 6, 10)
    if suffix == "aug":
        return (0, 4, 8)
    if suffix == "6":
        return (0, 4, 7, 9)
    if suffix == "m6":
        return (0, 3, 7, 9)
    if suffix == "9":
        return (0, 4, 7, 10, 14)
    if suffix == "maj9":
        return (0, 4, 7, 11, 14)
    if suffix == "m9":
        return (0, 3, 7, 10, 14)
    if suffix == "mMaj7":
        return (0, 3, 7, 11)
    return (0, 4, 7)


def chord_segments_from_metadata(progression: object) -> list[ChordSegment]:
    if not isinstance(progression, list):
        return []
    segments = [_segment_from_metadata(item) for item in progression if isinstance(item, dict)]
    return [segment for segment in segments if segment is not None]


def _simple_chord_for_style(seg: ChordSegment, style: str) -> ChordSegment:
    if seg.root is None or not seg.intervals:
        return seg
    root_name = _PITCHES[seg.root]
    _, suffix = _split_chord_label(seg.label)
    is_minor = suffix in {"m", "m7", "dim"}
    is_seventh = suffix in {"7", "maj7", "m7"}
    if style == "auto":
        return seg
    if style == "sevenths" and is_seventh:
        return seg
    label = f"{root_name}m" if is_minor else root_name
    intervals = (0, 3, 7) if is_minor else (0, 4, 7)
    return ChordSegment(
        label,
        seg.start,
        seg.end,
        seg.root,
        intervals,
        seg.confidence,
        seg.start_beat,
        seg.end_beat,
    )


def _quantize_segments_to_bars(segments: list[ChordSegment], beats_per_bar: int = 4) -> list[ChordSegment]:
    usable = [seg for seg in segments if seg.start_beat is not None and seg.end_beat is not None]
    if not usable:
        return segments
    first_beat = min(int(seg.start_beat) for seg in usable)
    last_beat = max(int(seg.end_beat) for seg in usable)
    bar_start = (first_beat // beats_per_bar) * beats_per_bar
    quantized: list[ChordSegment] = []

    def time_for_beat(beat: int) -> float:
        for segment in usable:
            seg_start = int(segment.start_beat)
            seg_end = int(segment.end_beat)
            if seg_start <= beat <= seg_end and seg_end > seg_start:
                ratio = (beat - seg_start) / (seg_end - seg_start)
                return segment.start + ((segment.end - segment.start) * ratio)
        if beat <= first_beat:
            return min(segment.start for segment in usable)
        return max(segment.end for segment in usable)

    for start in range(bar_start, last_beat, beats_per_bar):
        end = start + beats_per_bar
        overlaps: list[tuple[float, ChordSegment]] = []
        for seg in usable:
            overlap = max(0, min(end, int(seg.end_beat)) - max(start, int(seg.start_beat)))
            if overlap > 0:
                overlaps.append((float(overlap), seg))
        if not overlaps:
            continue
        _, picked = max(overlaps, key=lambda item: (item[0], item[1].confidence))
        quantized_end = min(end, last_beat)
        quantized.append(
            ChordSegment(
                picked.label,
                time_for_beat(start),
                time_for_beat(quantized_end),
                picked.root,
                picked.intervals,
                picked.confidence,
                start,
                quantized_end,
            )
        )
    return _merge_segments(quantized)


def prepare_chord_midi_segments(
    segments: list[ChordSegment],
    *,
    style: str = "auto",
    grid: str = "beat",
) -> list[ChordSegment]:
    normalized_style = normalize_chord_midi_style(style)
    normalized_grid = normalize_chord_midi_grid(grid)
    transformed = [_simple_chord_for_style(seg, normalized_style) for seg in segments]
    transformed = _smooth_short_segments(_merge_segments(transformed))
    if normalized_grid == "bar":
        transformed = _quantize_segments_to_bars(transformed)
    return _merge_segments(transformed)


def _normalize_chroma_frames(chroma: np.ndarray) -> np.ndarray:
    matrix = np.asarray(chroma, dtype=np.float32)
    totals = matrix.sum(axis=0, keepdims=True)
    return np.divide(matrix, totals, out=np.zeros_like(matrix), where=totals > 1e-8)


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
    affinity = _key_affinity(root, intervals, key_context)
    if affinity is None:
        return 0.0
    tonic, _ = key_context or (0, "major")
    bias = (affinity - 0.5) * 0.038
    root_degree = (root - tonic) % 12
    if root_degree == 0:
        bias += 0.008
    elif root_degree in {5, 7}:
        bias += 0.005
    if affinity < 0.75:
        bias -= 0.020
    return bias


def _key_affinity(
    root: int, intervals: tuple[int, ...], key_context: tuple[int, str] | None
) -> float | None:
    if key_context is None:
        return None
    tonic, mode = key_context
    scale = _DIATONIC_MAJOR if mode == "major" else _DIATONIC_MINOR
    tones = {((root + interval) - tonic) % 12 for interval in intervals}
    if not tones:
        return None
    return sum(1 for tone in tones if tone in scale) / len(tones)


def _parse_key_context(label: str | None) -> tuple[int, str] | None:
    if not label:
        return None
    parts = label.strip().replace("major", "maj").replace("minor", "min").split()
    if len(parts) < 2:
        return None
    root = parts[0].replace("♯", "#").replace("♭", "b")
    flats = {
        "Db": "C#",
        "Eb": "D#",
        "Gb": "F#",
        "Ab": "G#",
        "Bb": "A#",
    }
    root = flats.get(root, root)
    mode = "minor" if parts[1].lower().startswith("min") else "major"
    if root not in _PITCH_TO_INDEX:
        return None
    return _PITCH_TO_INDEX[root], mode


def _rank_chord_candidates(
    chroma: np.ndarray,
    *,
    key_context: tuple[int, str] | None = None,
    limit: int = 8,
) -> list[_ChordCandidate]:
    normalized = _normalize_chroma(chroma)
    if normalized is None:
        return [_ChordCandidate("N.C.", None, (), 0.0, 0.0)]
    entropy = -float(np.sum(normalized * np.log2(np.maximum(normalized, 1e-8)))) / np.log2(12)
    ordered_energy = np.sort(normalized)
    strongest = float(ordered_energy[-1])
    second = float(ordered_energy[-2]) if len(ordered_energy) > 1 else 0.0
    if strongest < 0.115 and entropy > 0.92:
        return [_ChordCandidate("N.C.", None, (), 0.0, 0.0)]

    scored: list[tuple[float, str, int, tuple[int, ...]]] = []
    for root in range(12):
        for suffix, intervals, weights in _CHORD_TEMPLATES:
            template = _template_vector(root, intervals, weights)
            support = float(np.dot(normalized, template))
            off_energy = float(np.sum(normalized[template <= 0.001]))
            root_energy = float(normalized[root])
            fifth_energy = float(normalized[(root + 7) % 12])
            third_energy = float(
                max(normalized[(root + 3) % 12], normalized[(root + 4) % 12])
            )
            score = (
                support
                + (0.20 * root_energy)
                + (0.035 * fifth_energy)
                - (0.12 * off_energy)
                + _key_bias(root, intervals, key_context)
            )
            if suffix in {"7", "maj7", "m7"}:
                seventh_interval = 11 if suffix == "maj7" else 10
                seventh_energy = float(normalized[(root + seventh_interval) % 12])
                score -= 0.012 if suffix == "7" else 0.018
                if seventh_energy < 0.055:
                    score -= 0.045
                else:
                    score += min(0.014, seventh_energy * 0.12)
            elif suffix in {"sus2", "sus4"}:
                score -= 0.038
                sus_interval = 2 if suffix == "sus2" else 5
                sus_energy = float(normalized[(root + sus_interval) % 12])
                if sus_energy < third_energy * 1.18:
                    score -= 0.040
            elif suffix == "dim":
                score -= 0.045
                if float(normalized[(root + 6) % 12]) < 0.070:
                    score -= 0.040
            affinity = _key_affinity(root, intervals, key_context)
            if affinity is not None:
                if suffix in {"dim", "sus2", "sus4"} and affinity < 0.99:
                    score -= 0.040
                elif suffix in {"7", "maj7", "m7"} and affinity < 0.75:
                    score -= 0.020
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

    if not candidates or candidates[0].score < 0.062:
        return [_ChordCandidate("N.C.", None, (), 0.0, 0.0)]
    if entropy > 0.88 and candidates[0].confidence < 0.58 and strongest < second * 1.18:
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
        return 0.026
    if prev.root is None or current.root is None:
        return -0.012
    if prev.root == current.root:
        return 0.004
    motion = (current.root - prev.root) % 12
    if motion in {5, 7}:
        return 0.004
    penalty = -0.010
    if current.confidence < 0.72:
        penalty -= 0.016
    return penalty


def _select_chord_sequence(
    vectors: list[np.ndarray], key_context: tuple[int, str] | None = None
) -> list[_ChordCandidate]:
    if not vectors:
        return []
    inferred_key = _estimate_key_context(vectors)
    if key_context is None or (
        inferred_key is not None and inferred_key != key_context and len(vectors) >= 8
    ):
        key_context = inferred_key
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
    paths: list[tuple[str, Path, float]]
    bass_path: Path | None = None
    if stems_dir is None:
        return [("original", source, 1.0)], bass_path
    harmonic_paths: list[tuple[str, Path, float]] = []
    for name, weight in _CHORD_STEM_WEIGHTS.items():
        path = stems_dir / f"{name}.wav"
        if path.is_file():
            harmonic_paths.append((name, path, weight))
    # Once harmonic stems exist, the full mix becomes a fallback reference only:
    # vocals, drums, and effects often pollute chroma enough to hurt chord calls.
    paths = [("original", source, 0.35 if harmonic_paths else 1.0), *harmonic_paths]
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
    bass_focus: bool = False,
    job: Job | None = None,
) -> _ChromaSource | None:
    import librosa

    chroma_chunks: list[np.ndarray] = []
    time_chunks: list[np.ndarray] = []
    hop_length = 512
    start = 0.0
    total = max(0.0, end_time + 1.0)
    while start < total:
        if job is not None and job.cancel_requested:
            raise JobCancelled()
        length = min(_CHROMA_CHUNK_SEC, total - start)
        loaded = _load_audio_ffmpeg(
            path,
            sr=22050,
            duration=length,
            start=start,
            job=job,
        )
        if loaded is None:
            start += length
            continue

        y, sr = loaded
        if float(np.sqrt(np.mean(np.square(y)))) < 1e-6:
            start += length
            continue
        y_chroma = librosa.effects.harmonic(y) if harmonic else y
        n_octaves = 4 if bass_focus else 7
        try:
            tuning = float(librosa.estimate_tuning(y=y_chroma, sr=sr))
        except Exception:
            tuning = 0.0
        bins_per_octave = 36
        cqt_spectrum = np.abs(
            librosa.cqt(
                y_chroma,
                sr=sr,
                hop_length=hop_length,
                n_bins=n_octaves * bins_per_octave,
                bins_per_octave=bins_per_octave,
                tuning=tuning,
            )
        )
        chroma = librosa.feature.chroma_cqt(
            C=cqt_spectrum,
            sr=sr,
            hop_length=hop_length,
            n_octaves=n_octaves,
            bins_per_octave=bins_per_octave,
            tuning=tuning,
        )
        try:
            cens = librosa.feature.chroma_cens(
                C=cqt_spectrum,
                sr=sr,
                hop_length=hop_length,
                n_octaves=n_octaves,
                bins_per_octave=bins_per_octave,
                tuning=tuning,
            )
        except Exception as exc:
            logger.debug("chroma_cens unavailable for %s: %s", path, exc)
        else:
            frame_count = min(chroma.shape[1], cens.shape[1])
            if frame_count > 0:
                cqt = _normalize_chroma_frames(chroma[:, :frame_count])
                cens = _normalize_chroma_frames(cens[:, :frame_count])
                chroma = (0.68 * cqt) + (0.32 * cens)
        if chroma.shape[1] >= 3:
            chroma = (
                np.roll(chroma, 1, axis=1) + (2.0 * chroma) + np.roll(chroma, -1, axis=1)
            ) / 4.0
            chroma[:, 0] = chroma[:, 1]
            chroma[:, -1] = chroma[:, -2]
        frame_times = (
            librosa.frames_to_time(
                np.arange(chroma.shape[1]),
                sr=sr,
                hop_length=hop_length,
            )
            + start
        )
        # CQT padding can emit a frame a few milliseconds past the decoded
        # window. Trim it so concatenated chunk times remain sorted.
        keep = frame_times < (start + length + 1e-6)
        if np.any(keep):
            chroma_chunks.append(np.asarray(chroma[:, keep], dtype=np.float32))
            time_chunks.append(np.asarray(frame_times[keep], dtype=np.float64))
        start += length

    if not chroma_chunks:
        return None
    return _ChromaSource(
        name=name,
        weight=weight,
        chroma=np.concatenate(chroma_chunks, axis=1),
        frame_times=np.concatenate(time_chunks),
    )


def _load_chroma_sources(
    specs: list[tuple[str, Path, float, bool, bool]],
    *,
    end_time: float,
    job: Job | None,
) -> list[_ChromaSource | None]:
    """Load independent harmonic sources in parallel with bounded memory use."""

    def load(spec: tuple[str, Path, float, bool, bool]) -> _ChromaSource | None:
        name, path, weight, harmonic, bass_focus = spec
        return _load_chroma_source(
            path,
            name=name,
            weight=weight,
            end_time=end_time,
            harmonic=harmonic,
            bass_focus=bass_focus,
            job=job,
        )

    worker_count = max(1, min(2, BACKGROUND_CPU_THREADS, len(specs)))
    if worker_count == 1:
        return [load(spec) for spec in specs]
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="layerlab-chroma",
    ) as executor:
        futures = [executor.submit(load, spec) for spec in specs]
        return [future.result() for future in futures]


def _mean_normalized_chroma(source: _ChromaSource, start: float, end: float) -> np.ndarray | None:
    left = int(np.searchsorted(source.frame_times, start, side="left"))
    right = int(np.searchsorted(source.frame_times, end, side="left"))
    if right <= left:
        return None
    frames = np.asarray(source.chroma[:, left:right], dtype=np.float32)
    frame_sums = frames.sum(axis=0, keepdims=True)
    good = frame_sums[0] > 1e-8
    if not np.any(good):
        return None
    normalized_frames = frames[:, good] / frame_sums[:, good]
    mean = normalized_frames.mean(axis=1)
    upper = np.quantile(normalized_frames, 0.72, axis=1)
    median = np.median(normalized_frames, axis=1)
    # Mean catches arpeggios, upper quantile catches chord tones that appear
    # strongly on part of the bar, median suppresses one-frame melody spikes.
    vector = np.asarray((0.56 * mean) + (0.30 * upper) + (0.14 * median), dtype=np.float32)
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
        weight *= 0.25 + (0.75 * _source_chord_reliability(vector))
        combined += vector * weight
        total_weight += weight

    if total_weight <= 0:
        return None
    combined /= total_weight
    return combined


def _source_chord_reliability(chroma: np.ndarray) -> float:
    """Estimate whether a source contains a chord rather than one melody note."""
    normalized = _normalize_chroma(chroma)
    if normalized is None:
        return 0.0
    ordered = np.sort(normalized)
    secondary_energy = float(ordered[-2] + ordered[-3])
    return min(1.0, max(0.0, (secondary_energy - 0.08) / 0.55))


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
    harmony_support = float(combined[root])
    # Passing bass notes are common. Use bass as a strong hint only when the
    # harmonic chroma also contains at least some evidence for that pitch.
    support_scale = min(1.0, 0.30 + (harmony_support * 5.0))
    combined[root] += boost * clarity * support_scale
    total = float(np.sum(combined))
    return combined / total if total > 0 else combined


def _candidate_from_segment(seg: ChordSegment) -> _ChordCandidate:
    return _ChordCandidate(seg.label, seg.root, seg.intervals, seg.confidence, seg.confidence)


def _replace_segment_chord(seg: ChordSegment, chord: _ChordCandidate) -> ChordSegment:
    return ChordSegment(
        chord.label,
        seg.start,
        seg.end,
        chord.root,
        chord.intervals,
        max(seg.confidence, chord.confidence),
        seg.start_beat,
        seg.end_beat,
    )


def _segment_beat_length(seg: ChordSegment) -> float:
    if seg.start_beat is not None and seg.end_beat is not None:
        return max(0.0, float(seg.end_beat - seg.start_beat))
    return max(0.0, seg.end - seg.start)


def _is_unstable_complex_label(label: str) -> bool:
    return label.endswith(("dim", "sus2", "sus4", "maj7"))


def _smooth_short_segments(segments: list[ChordSegment]) -> list[ChordSegment]:
    if len(segments) < 3:
        return segments
    smoothed = list(segments)
    for idx in range(1, len(smoothed) - 1):
        current = smoothed[idx]
        prev = smoothed[idx - 1]
        nxt = smoothed[idx + 1]
        if _segment_beat_length(current) > 1.01:
            continue
        if prev.label == nxt.label and current.confidence < 0.72:
            smoothed[idx] = _replace_segment_chord(current, _candidate_from_segment(prev))
            continue
        if _is_unstable_complex_label(current.label):
            neighbor = prev if prev.confidence >= nxt.confidence else nxt
            smoothed[idx] = _replace_segment_chord(current, _candidate_from_segment(neighbor))
            continue
        if current.confidence >= _MIN_STABLE_CHORD_CONFIDENCE:
            continue
        neighbor = prev if prev.confidence >= nxt.confidence else nxt
        smoothed[idx] = _replace_segment_chord(current, _candidate_from_segment(neighbor))
    return _merge_segments(smoothed)


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
    last_idx = len(beat_bounds) - 1
    start_idx = max(0, min(int(start_beat), last_idx))
    if start_idx >= last_idx:
        return None
    end_idx = max(start_idx + 1, min(int(end_beat), last_idx))
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
        # Trim a small boundary region so drum attacks and previous-chord tails
        # do not dominate short quarter-note windows.
        margin = min(0.06, (beat_end - beat_start) * 0.10)
        inner_start = beat_start + margin
        inner_end = max(inner_start, beat_end - margin)
        beat_vector = _combine_harmony_chroma(chord_sources, inner_start, inner_end)
        if beat_vector is None:
            continue
        hint = _bass_root_hint(bass_source, inner_start, inner_end)
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
    beats_per_chord: int = 1,
    stems_dir: Path | None = None,
    key_context: tuple[int, str] | None = None,
    job: Job | None = None,
) -> list[ChordSegment]:
    """Estimate sustained chord labels between detected quarter-note beats.

    This is intentionally a guide-track generator, not a full polyphonic
    transcription engine. We classify each beat window, smooth improbable
    one-beat flips, then merge adjacent identical labels into sustained
    chord blocks on the DAW grid.
    """
    if not beat_times or len(beat_times) < 2:
        return []
    end_time = min(float(duration_sec or beat_times[-1]), beat_times[-1])
    if end_time <= 0:
        return []
    chord_paths, bass_path = _available_chord_source_paths(source, stems_dir)
    load_specs = [
        (
            name,
            path,
            weight,
            # Dedicated piano/guitar stems are already strongly harmonic.
            # Avoiding another HPSS pass is faster and preserves attacks.
            name in {"original", "other"},
            False,
        )
        for name, path, weight in chord_paths
    ]
    if bass_path is not None:
        load_specs.append(("bass", bass_path, 1.0, False, True))
    loaded_sources = _load_chroma_sources(load_specs, end_time=end_time, job=job)
    chord_sources = [
        loaded for loaded in loaded_sources[: len(chord_paths)] if loaded is not None
    ]
    if not chord_sources:
        return []
    bass_source = loaded_sources[-1] if bass_path is not None else None

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
        if job is not None and job.cancel_requested:
            raise JobCancelled()
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

    selected = _select_chord_sequence([vector for *_, vector in spans], key_context=key_context)
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
    return _smooth_short_segments(_merge_segments(segments))


def _clean_beat_times(beat_times: list[float] | None) -> list[float]:
    cleaned: list[float] = []
    for raw in beat_times or []:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(value) or value < 0 or (cleaned and value - cleaned[-1] < 0.08):
            continue
        cleaned.append(value)
    return cleaned


def _beat_offset_ticks(beat_times: list[float] | None, tempo_bpm: int) -> int:
    beats = _clean_beat_times(beat_times)
    if not beats:
        return 0
    return max(0, round(beats[0] * tempo_bpm / 60.0 * _TPB))


def _tempo_map_events(
    beat_times: list[float] | None,
    tempo_bpm: int,
) -> list[tuple[int, int]]:
    """Build quarter-note tempo events anchored to detected audio beats."""
    base_tempo_us = int(round(60_000_000 / tempo_bpm))
    events: dict[int, int] = {0: base_tempo_us}
    beats = _clean_beat_times(beat_times)
    if len(beats) < 2:
        return sorted(events.items())

    intervals = np.diff(np.asarray(beats, dtype=np.float64))
    offset = _beat_offset_ticks(beats, tempo_bpm)
    for idx, raw_interval in enumerate(intervals):
        interval = float(raw_interval)
        tempo_us = max(1, min(0xFFFFFF, int(round(interval * 1_000_000))))
        events[offset + (idx * _TPB)] = tempo_us
    return sorted(events.items())


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
    markers: bool = False,
    beat_times: list[float] | None = None,
) -> None:
    tempo_bpm = max(30, min(240, int(bpm or 120)))
    beat_offset_ticks = _beat_offset_ticks(beat_times, tempo_bpm)
    conductor_events = bytearray()
    conductor_events += _meta(0, 0x03, _midi_text("LayerLab Tempo", "LayerLab Tempo", 64))
    conductor_events += _meta(0, 0x58, bytes((4, 2, 24, 8)))  # 4/4, 24 MIDI clocks/click.
    conductor_events += _meta(0, 0x01, _midi_text(f"BPM {tempo_bpm}", f"BPM {tempo_bpm}", 32))
    tempo_cursor = 0
    for tick, event_tempo_us in _tempo_map_events(beat_times, tempo_bpm):
        conductor_events += _meta(
            max(0, tick - tempo_cursor),
            0x51,
            event_tempo_us.to_bytes(3, "big"),
        )
        tempo_cursor = tick
    conductor_events += _meta(0, 0x2F, b"")

    events = bytearray()
    events += _meta(0, 0x03, _midi_text(title, "LayerLab Chord Progression", 120))
    events += _varlen(0) + bytes((0xC0, 0))  # Acoustic Grand Piano

    cursor_ticks = 0
    for seg in segments:
        if seg.start_beat is not None and seg.end_beat is not None:
            start_tick = max(0, beat_offset_ticks + (int(seg.start_beat) * _TPB))
            end_tick = max(start_tick + 1, beat_offset_ticks + (int(seg.end_beat) * _TPB))
        else:
            start_tick = int(round(seg.start * tempo_bpm / 60.0 * _TPB))
            end_tick = max(start_tick + 1, int(round(seg.end * tempo_bpm / 60.0 * _TPB)))
        delta = max(0, start_tick - cursor_ticks)
        label = _midi_text(seg.label, "N.C.", 48)
        events += _meta(delta, 0x06 if markers else 0x01, label)
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


def chord_segments_to_csv(segments: list[ChordSegment]) -> str:
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["label", "start_sec", "end_sec", "start_beat", "end_beat", "confidence"])
    for seg in segments:
        writer.writerow(
            [
                seg.label,
                f"{seg.start:.3f}",
                f"{seg.end:.3f}",
                "" if seg.start_beat is None else seg.start_beat,
                "" if seg.end_beat is None else seg.end_beat,
                f"{seg.confidence:.3f}",
            ]
        )
    return out.getvalue()


def generate_chord_midi(
    job: Job, source: Path, job_dir: Path, *, stems_dir: Path | None = None
) -> Path | None:
    try:
        segments = detect_chord_segments(
            source,
            job.beat_times,
            duration_sec=job.duration_sec,
            stems_dir=stems_dir or (job_dir / "stems"),
            key_context=_parse_key_context(job.key),
            job=job,
        )
        if not segments:
            return None
        out = job_dir / "stems" / "chords.mid"
        out.parent.mkdir(exist_ok=True)
        write_chord_midi(
            out,
            segments,
            bpm=job.bpm,
            title=job.title,
            beat_times=job.beat_times,
        )
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
        midi_analysis = analyze_chord_midi(
            out,
            segments,
            source_key=job.key,
            bpm=job.bpm,
        )
        if midi_analysis is not None:
            analysis_path = job_dir / "stems" / "midi-analysis.json"
            write_midi_analysis(analysis_path, midi_analysis)
            job.midi_analysis = midi_analysis
            job.midi_analysis_url = f"/api/jobs/{job.id}/midi-analysis.json"
        logger.info("chord MIDI generated for job %s (%s segments)", job.id, len(segments))
        return out
    except JobCancelled:
        raise
    except Exception:
        logger.warning("chord MIDI generation skipped for job %s", job.id, exc_info=True)
        return None
