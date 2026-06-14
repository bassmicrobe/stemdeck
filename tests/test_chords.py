from __future__ import annotations

from pathlib import Path

import numpy as np

from app.core.models import Job
from app.pipeline import chords as chords_mod
from app.pipeline.chords import (
    ChordSegment,
    _available_chord_source_paths,
    _ChromaSource,
    _combine_segment_chroma,
    _score_chord,
    _varlen,
    generate_chord_midi,
    write_chord_midi,
)


def _fake_chroma_source(name: str, weight: float, notes: dict[int, float]) -> _ChromaSource:
    chroma = np.zeros((12, 1), dtype=np.float32)
    for note, value in notes.items():
        chroma[note, 0] = value
    return _ChromaSource(name=name, weight=weight, chroma=chroma, frame_times=np.array([0.5]))


def _read_varlen(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, offset


def _track_chunks(data: bytes) -> list[bytes]:
    chunks: list[bytes] = []
    offset = 14
    while offset < len(data):
        assert data[offset : offset + 4] == b"MTrk"
        length = int.from_bytes(data[offset + 4 : offset + 8], "big")
        start = offset + 8
        chunks.append(data[start : start + length])
        offset = start + length
    return chunks


def _last_track_tick(track: bytes) -> int:
    offset = 0
    end = len(track)
    tick = 0
    running_status = None
    while offset < end:
        delta, offset = _read_varlen(track, offset)
        tick += delta
        status = track[offset]
        offset += 1
        if status == 0xFF:
            meta_type = track[offset]
            offset += 1
            size, offset = _read_varlen(track, offset)
            offset += size
            if meta_type == 0x2F:
                return tick
        elif status in (0xF0, 0xF7):
            size, offset = _read_varlen(track, offset)
            offset += size
        else:
            if status < 0x80:
                if running_status is None:
                    raise AssertionError("running status without previous status")
                offset -= 1
                status = running_status
            else:
                running_status = status
            event_type = status & 0xF0
            offset += 1 if event_type in (0xC0, 0xD0) else 2
    return tick


def _last_midi_tick(data: bytes) -> int:
    return max(_last_track_tick(track) for track in _track_chunks(data))


def test_varlen_encoding_matches_midi_spec():
    assert _varlen(0) == b"\x00"
    assert _varlen(127) == b"\x7f"
    assert _varlen(128) == b"\x81\x00"
    assert _varlen(480) == b"\x83\x60"


def test_available_chord_sources_prefer_harmonic_stems(tmp_path: Path):
    source = tmp_path / "source.wav"
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    source.write_bytes(b"wav")
    for name in ("piano", "guitar", "bass", "drums"):
        (stems_dir / f"{name}.wav").write_bytes(b"wav")

    chord_paths, bass_path = _available_chord_source_paths(source, stems_dir)

    assert [name for name, _, _ in chord_paths] == ["original", "piano", "guitar"]
    assert bass_path == stems_dir / "bass.wav"


def test_combine_segment_chroma_uses_stems_and_bass_root_hint():
    original = _fake_chroma_source("original", 1.0, {0: 0.7, 4: 0.8, 7: 0.6, 9: 0.4})
    piano = _fake_chroma_source("piano", 0.9, {9: 1.0, 0: 0.82, 4: 0.72, 7: 0.42})
    bass = _fake_chroma_source("bass", 1.0, {9: 1.0})

    combined = _combine_segment_chroma([original, piano], bass, 0.0, 1.0)

    assert combined is not None
    label, root, _, confidence = _score_chord(combined)
    assert label in {"Am", "Am7"}
    assert root == 9
    assert confidence > 0.8


def test_write_chord_midi_writes_standard_midi_file(tmp_path: Path):
    out = tmp_path / "chords.mid"
    segments = [
        ChordSegment("C", 0.0, 2.0, 0, (0, 4, 7), 0.9),
        ChordSegment("Am", 2.0, 4.0, 9, (0, 3, 7), 0.8),
    ]

    write_chord_midi(out, segments, bpm=120, title="Guide")

    data = out.read_bytes()
    assert data.startswith(b"MThd")
    assert int.from_bytes(data[8:10], "big") == 1
    assert int.from_bytes(data[10:12], "big") == 2
    assert len(_track_chunks(data)) == 2
    assert b"Guide" in data
    assert b"Am" in data
    assert b"\xff\x51\x03" in data
    assert b"\xff\x58\x04\x04\x02\x18\x08" in data
    assert _last_midi_tick(data) == 3840


def test_write_chord_midi_quantizes_detected_beats_as_quarter_notes(tmp_path: Path):
    out = tmp_path / "quantized.mid"
    segments = [
        ChordSegment("C", 2.368, 4.226, 0, (0, 4, 7), 0.9, start_beat=0, end_beat=4),
        ChordSegment("G", 4.226, 6.084, 7, (0, 4, 7), 0.8, start_beat=4, end_beat=8),
    ]

    write_chord_midi(out, segments, bpm=129, title="Quantized")

    assert _last_midi_tick(out.read_bytes()) == 8 * 480


def test_write_chord_midi_keeps_no_chord_time_on_grid(tmp_path: Path):
    out = tmp_path / "no-chord-tail.mid"
    segments = [
        ChordSegment("C", 0.0, 1.0, 0, (0, 4, 7), 0.9, start_beat=0, end_beat=4),
        ChordSegment("N.C.", 1.0, 2.0, None, (), 0.0, start_beat=4, end_beat=8),
    ]

    write_chord_midi(out, segments, bpm=120, title="No Chord Tail")

    assert _last_midi_tick(out.read_bytes()) == 8 * 480


def test_generate_chord_midi_sets_job_metadata(tmp_path: Path, monkeypatch):
    segments = [ChordSegment("C", 0.0, 1.0, 0, (0, 4, 7), 0.9, start_beat=0, end_beat=4)]
    monkeypatch.setattr(chords_mod, "detect_chord_segments", lambda *args, **kwargs: segments)
    job = Job(id="abcdefabcdef", bpm=120, title="Song")
    job_dir = tmp_path / job.id
    source = job_dir / "source.wav"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"wav")

    out = generate_chord_midi(job, source, job_dir)

    assert out == job_dir / "stems" / "chords.mid"
    assert out is not None and out.is_file()
    assert job.chord_midi_url == f"/api/jobs/{job.id}/chords.mid"
    assert job.chord_progression == [
        {
            "label": "C",
            "start": 0.0,
            "end": 1.0,
            "start_beat": 0,
            "end_beat": 4,
            "confidence": 0.9,
        }
    ]
