from __future__ import annotations

import importlib.util
import json
import logging
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from app.core.files import atomic_write_text

logger = logging.getLogger("stemdeck.midi_analysis")


def music21_available() -> bool:
    return importlib.util.find_spec("music21") is not None


def _music21_key(label: str | None):
    from music21 import key

    parts = (label or "").split()
    if not parts:
        return None
    mode = "minor" if len(parts) > 1 and parts[1].lower().startswith("min") else "major"
    try:
        return key.Key(parts[0], mode)
    except Exception:
        return None


def analyze_chord_midi(
    path: Path,
    segments: list[object],
    *,
    source_key: str | None,
    bpm: int | None,
) -> dict[str, object] | None:
    """Validate and harmonically summarize a generated MIDI file with music21."""
    if not music21_available() or not path.is_file():
        return None
    try:
        from music21 import chord, converter, roman

        score = converter.parse(path)
        notes = list(score.recurse().notes)
        pitches = [pitch for event in notes for pitch in getattr(event, "pitches", ())]
        detected_key = score.analyze("key") if notes else None
        context_key = _music21_key(source_key) or detected_key

        roman_progression: list[dict[str, object]] = []
        confidences: list[float] = []
        for segment in segments:
            root = getattr(segment, "root", None)
            intervals = tuple(getattr(segment, "intervals", ()))
            confidence = float(getattr(segment, "confidence", 0.0))
            confidences.append(confidence)
            figure = None
            if context_key is not None and root is not None and intervals:
                midi_root = 48 + int(root)
                event = chord.Chord([midi_root + int(interval) for interval in intervals])
                try:
                    figure = roman.romanNumeralFromChord(event, context_key).figure
                except Exception:
                    figure = None
            roman_progression.append(
                {
                    "label": str(getattr(segment, "label", "N.C.")),
                    "roman": figure,
                    "start_beat": getattr(segment, "start_beat", None),
                    "end_beat": getattr(segment, "end_beat", None),
                    "confidence": round(confidence, 3),
                }
            )

        try:
            engine_version = version("music21")
        except PackageNotFoundError:
            engine_version = "unknown"

        return {
            "engine": "music21",
            "engine_version": engine_version,
            "tempo_bpm": bpm,
            "source_key": source_key,
            "detected_midi_key": (
                f"{detected_key.tonic.name} {detected_key.mode}" if detected_key is not None else None
            ),
            "note_event_count": len(notes),
            "pitch_min": min(pitches).nameWithOctave if pitches else None,
            "pitch_max": max(pitches).nameWithOctave if pitches else None,
            "duration_quarter_length": round(float(score.duration.quarterLength), 3),
            "segment_count": len(roman_progression),
            "average_confidence": (
                round(sum(confidences) / len(confidences), 3) if confidences else None
            ),
            "roman_progression": roman_progression,
        }
    except Exception:
        logger.warning("music21 MIDI analysis failed for %s", path, exc_info=True)
        return None


def write_midi_analysis(path: Path, analysis: dict[str, object]) -> None:
    atomic_write_text(path, json.dumps(analysis, ensure_ascii=False, indent=2) + "\n")
