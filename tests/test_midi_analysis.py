from __future__ import annotations

import json

from app.pipeline.chords import ChordSegment, write_chord_midi
from app.pipeline.midi_analysis import analyze_chord_midi, write_midi_analysis


def test_music21_analyzes_generated_chord_midi(tmp_path):
    midi_path = tmp_path / "chords.mid"
    segments = [
        ChordSegment("C", 0.0, 1.0, 0, (0, 4, 7), 0.9, start_beat=0, end_beat=4),
        ChordSegment("G7", 1.0, 2.0, 7, (0, 4, 7, 10), 0.8, start_beat=4, end_beat=8),
    ]
    write_chord_midi(midi_path, segments, bpm=120, title="Guide")

    analysis = analyze_chord_midi(midi_path, segments, source_key="C maj", bpm=120)

    assert analysis is not None
    assert analysis["engine"] == "music21"
    assert analysis["detected_midi_key"] == "C major"
    assert analysis["pitch_min"] == "C3"
    assert analysis["pitch_max"] == "G4"
    assert [item["roman"] for item in analysis["roman_progression"]] == ["I", "V7"]

    out = tmp_path / "midi-analysis.json"
    write_midi_analysis(out, analysis)
    assert json.loads(out.read_text(encoding="utf-8"))["engine"] == "music21"
