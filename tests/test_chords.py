from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pytest

from app.core.models import Job, JobCancelled
from app.pipeline import chords as chords_mod
from app.pipeline.chords import (
    ChordSegment,
    _apply_bass_root_hint,
    _available_chord_source_paths,
    _canonical_chord_label,
    _ChromaSource,
    _clean_beat_times,
    _combine_beatwise_chroma,
    _combine_segment_chroma,
    _estimate_key_context,
    _load_chroma_source,
    _mean_normalized_chroma,
    _midi_text,
    _parse_key_context,
    _score_chord,
    _select_chord_sequence,
    _smooth_short_segments,
    _source_chord_reliability,
    _tempo_map_events,
    _varlen,
    chord_segments_from_metadata,
    chord_segments_to_csv,
    detect_chord_segments,
    generate_chord_midi,
    prepare_chord_midi_segments,
    write_chord_midi,
)


def _fake_chroma_source(name: str, weight: float, notes: dict[int, float]) -> _ChromaSource:
    chroma = np.zeros((12, 1), dtype=np.float32)
    for note, value in notes.items():
        chroma[note, 0] = value
    return _ChromaSource(name=name, weight=weight, chroma=chroma, frame_times=np.array([0.5]))


def _vector(notes: dict[int, float]) -> np.ndarray:
    chroma = np.zeros(12, dtype=np.float32)
    for note, value in notes.items():
        chroma[note] = value
    return chroma


def _multi_frame_source(
    name: str,
    weight: float,
    frames: list[dict[int, float]],
    frame_times: list[float],
) -> _ChromaSource:
    chroma = np.zeros((12, len(frames)), dtype=np.float32)
    for idx, notes in enumerate(frames):
        for note, value in notes.items():
            chroma[note, idx] = value
    return _ChromaSource(name=name, weight=weight, chroma=chroma, frame_times=np.array(frame_times))


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
    assert chord_paths[0][2] == 0.35
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


def test_combine_beatwise_chroma_uses_bass_root_across_beats():
    harmony = _multi_frame_source(
        "piano",
        1.25,
        [
            {0: 0.82, 4: 0.74, 7: 0.62, 9: 0.55},
            {0: 0.80, 4: 0.70, 7: 0.60, 9: 0.58},
            {0: 0.84, 4: 0.72, 7: 0.63, 9: 0.56},
            {0: 0.78, 4: 0.69, 7: 0.59, 9: 0.60},
        ],
        [0.5, 1.5, 2.5, 3.5],
    )
    bass = _multi_frame_source(
        "bass",
        1.0,
        [{9: 1.0}, {9: 0.9}, {9: 1.0}, {9: 0.92}],
        [0.5, 1.5, 2.5, 3.5],
    )

    combined = _combine_beatwise_chroma([harmony], bass, [0, 1, 2, 3, 4], 0, 4)
    assert combined is not None
    label, root, _, confidence = _score_chord(combined)

    assert label in {"Am", "Am7"}
    assert root == 9
    assert confidence > 0.75


def test_combine_beatwise_chroma_skips_tail_without_next_beat():
    harmony = _multi_frame_source(
        "piano",
        1.25,
        [{0: 1.0, 4: 0.82, 7: 0.72}],
        [0.5],
    )

    assert _combine_beatwise_chroma([harmony], None, [0.0, 1.0], 1, 5) is None


def test_mean_normalized_chroma_uses_sorted_time_window():
    source = _multi_frame_source(
        "piano",
        1.0,
        [
            {0: 1.0},
            {4: 1.0},
            {7: 1.0},
            {9: 1.0},
        ],
        [0.25, 0.75, 1.25, 1.75],
    )

    vector = _mean_normalized_chroma(source, 0.7, 1.5)

    assert vector is not None
    assert vector[4] > 0
    assert vector[7] > 0
    assert vector[0] == 0
    assert vector[9] == 0


def test_bass_passing_note_does_not_override_supported_harmony():
    harmony = _vector({0: 1.0, 4: 0.84, 7: 0.74})
    harmony = harmony / float(np.sum(harmony))

    combined = _apply_bass_root_hint(harmony, (2, 1.0), boost=0.24)
    label, root, _, confidence = _score_chord(combined)

    assert label == "C"
    assert root == 0
    assert confidence > 0.75


def test_estimate_key_context_returns_confident_minor_key():
    vectors = [
        _vector({2: 1.0, 5: 0.82, 9: 0.72}),
        _vector({10: 1.0, 2: 0.82, 5: 0.72}),
        _vector({9: 1.0, 0: 0.82, 4: 0.72}),
    ]

    assert _estimate_key_context(vectors) == (2, "minor")


def test_parse_key_context_accepts_analysis_labels():
    assert _parse_key_context("B min") == (11, "minor")
    assert _parse_key_context("Db major") == (1, "major")


def test_canonical_chord_label_supports_jams_and_complex_chords():
    assert _canonical_chord_label("C:maj") == "C"
    assert _canonical_chord_label("Bb:min7") == "A#m7"
    assert _canonical_chord_label("F#:hdim7") == "F#hdim7"
    assert _canonical_chord_label("N") == "N.C."

    segments = chord_segments_from_metadata(
        [{"label": "Bb:min7", "start": 0.0, "end": 1.0, "confidence": 0.8}]
    )
    assert segments[0].label == "A#m7"
    assert segments[0].intervals == (0, 3, 7, 10)


def test_source_chord_reliability_rejects_single_note_riffs():
    single_note = _vector({2: 1.0, 9: 0.05})
    triad = _vector({0: 1.0, 4: 0.82, 7: 0.72})

    assert _source_chord_reliability(triad) > 0.75
    assert _source_chord_reliability(single_note) < 0.35


def test_select_chord_sequence_smooths_weak_same_root_variant():
    c = _vector({0: 1.0, 4: 0.82, 7: 0.72})
    noisy_same_root = _vector({7: 0.9, 11: 0.68, 2: 0.62, 0: 0.8, 4: 0.6})

    selected = _select_chord_sequence([c, noisy_same_root, c])

    assert [candidate.label for candidate in selected] == ["C", "C", "C"]


def test_select_chord_sequence_keeps_strong_progression_change():
    c = _vector({0: 1.0, 4: 0.82, 7: 0.72})
    g = _vector({7: 1.0, 11: 0.82, 2: 0.72})

    selected = _select_chord_sequence([c, g, c])

    assert [candidate.label for candidate in selected] == ["C", "G", "C"]


def test_select_chord_sequence_uses_key_hint_to_avoid_unstable_complex_chords():
    key_context = _parse_key_context("B min")
    f_sharp_dim_like = _vector({6: 1.0, 9: 0.78, 0: 0.62})
    d_maj7_like = _vector({2: 1.0, 6: 0.82, 9: 0.72, 1: 0.45})

    selected = _select_chord_sequence([f_sharp_dim_like, d_maj7_like], key_context=key_context)

    assert [candidate.label for candidate in selected] == ["F#m", "D"]


def test_select_chord_sequence_prefers_full_harmonic_stem_key():
    c_major = _vector({0: 1.0, 4: 0.82, 7: 0.72})

    selected = _select_chord_sequence([c_major] * 8, key_context=(11, "minor"))

    assert all(candidate.label == "C" for candidate in selected)


def test_smooth_short_segments_removes_weak_one_beat_flip():
    segments = [
        ChordSegment("C", 0.0, 1.0, 0, (0, 4, 7), 0.82, start_beat=0, end_beat=1),
        ChordSegment("G", 1.0, 2.0, 7, (0, 4, 7), 0.34, start_beat=1, end_beat=2),
        ChordSegment("C", 2.0, 3.0, 0, (0, 4, 7), 0.86, start_beat=2, end_beat=3),
    ]

    smoothed = _smooth_short_segments(segments)

    assert len(smoothed) == 1
    assert smoothed[0].label == "C"
    assert smoothed[0].start_beat == 0
    assert smoothed[0].end_beat == 3


def test_smooth_short_segments_removes_single_beat_complex_label():
    segments = [
        ChordSegment("F#7", 0.0, 1.0, 6, (0, 4, 7, 10), 0.86, start_beat=0, end_beat=1),
        ChordSegment("Cmaj7", 1.0, 2.0, 0, (0, 4, 7, 11), 1.0, start_beat=1, end_beat=2),
        ChordSegment("Bm7", 2.0, 3.0, 11, (0, 3, 7, 10), 0.92, start_beat=2, end_beat=3),
    ]

    smoothed = _smooth_short_segments(segments)

    assert [(seg.label, seg.start_beat, seg.end_beat) for seg in smoothed] == [
        ("F#7", 0, 1),
        ("Bm7", 1, 3),
    ]


def test_prepare_chord_midi_segments_supports_triads_and_bar_grid():
    segments = chord_segments_from_metadata(
        [
            {"label": "Bm7", "start": 0.0, "end": 1.0, "start_beat": 0, "end_beat": 1, "confidence": 0.9},
            {"label": "Dmaj7", "start": 1.0, "end": 3.0, "start_beat": 1, "end_beat": 3, "confidence": 0.8},
            {"label": "Gmaj7", "start": 3.0, "end": 4.0, "start_beat": 3, "end_beat": 4, "confidence": 0.7},
        ]
    )

    prepared = prepare_chord_midi_segments(segments, style="triads", grid="bar")

    assert [(seg.label, seg.start_beat, seg.end_beat) for seg in prepared] == [("D", 0, 4)]
    assert prepared[0].intervals == (0, 4, 7)
    assert prepared[0].start == 0.0
    assert prepared[0].end == 4.0


def test_chord_segments_to_csv_writes_beat_metadata():
    segments = [ChordSegment("C", 0.0, 1.5, 0, (0, 4, 7), 0.876, start_beat=0, end_beat=4)]

    text = chord_segments_to_csv(segments)

    assert "label,start_sec,end_sec,start_beat,end_beat,confidence" in text
    assert "C,0.000,1.500,0,4,0.876" in text


def test_detect_chord_segments_uses_quarter_note_grid(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.wav"
    stems_dir = tmp_path / "stems"
    source.write_bytes(b"wav")
    stems_dir.mkdir()
    (stems_dir / "piano.wav").write_bytes(b"wav")

    piano = _multi_frame_source(
        "piano",
        1.25,
        [
            {0: 1.0, 4: 0.82, 7: 0.72},
            {0: 1.0, 4: 0.82, 7: 0.72},
            {7: 1.0, 11: 0.82, 2: 0.72},
            {7: 1.0, 11: 0.82, 2: 0.72},
        ],
        [0.5, 1.5, 2.5, 3.5],
    )

    def fake_load_chroma_source(*args, **kwargs):
        return piano if kwargs["name"] == "piano" else None

    monkeypatch.setattr(chords_mod, "_load_chroma_source", fake_load_chroma_source)

    segments = detect_chord_segments(
        source,
        [0.0, 1.0, 2.0, 3.0, 4.0],
        duration_sec=4.0,
        stems_dir=stems_dir,
    )

    assert [(seg.label, seg.start_beat, seg.end_beat) for seg in segments] == [
        ("C", 0, 2),
        ("G", 2, 4),
    ]


def test_detect_chord_segments_skips_redundant_hpss_for_piano(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.wav"
    stems_dir = tmp_path / "stems"
    source.write_bytes(b"wav")
    stems_dir.mkdir()
    (stems_dir / "piano.wav").write_bytes(b"wav")
    calls = []
    piano = _multi_frame_source(
        "piano",
        1.25,
        [{0: 1.0, 4: 0.82, 7: 0.72}, {0: 1.0, 4: 0.82, 7: 0.72}],
        [0.5, 1.5],
    )

    def fake_load_chroma_source(*args, **kwargs):
        calls.append((kwargs["name"], kwargs["harmonic"]))
        return piano if kwargs["name"] == "piano" else None

    monkeypatch.setattr(chords_mod, "_load_chroma_source", fake_load_chroma_source)

    detect_chord_segments(source, [0.0, 1.0, 2.0], duration_sec=2.0, stems_dir=stems_dir)

    assert ("original", True) in calls
    assert ("piano", False) in calls


def test_detect_chord_segments_loads_independent_sources_concurrently(
    monkeypatch,
    tmp_path: Path,
):
    source = tmp_path / "source.wav"
    stems_dir = tmp_path / "stems"
    source.write_bytes(b"wav")
    stems_dir.mkdir()
    (stems_dir / "piano.wav").write_bytes(b"wav")
    barrier = threading.Barrier(2)
    chroma = _multi_frame_source(
        "piano",
        1.0,
        [{0: 1.0, 4: 0.8, 7: 0.7}, {0: 1.0, 4: 0.8, 7: 0.7}],
        [0.5, 1.5],
    )

    def fake_load_chroma_source(*args, **kwargs):
        barrier.wait(timeout=1.0)
        return chroma

    monkeypatch.setattr(chords_mod, "_load_chroma_source", fake_load_chroma_source)

    segments = detect_chord_segments(
        source,
        [0.0, 1.0, 2.0],
        duration_sec=2.0,
        stems_dir=stems_dir,
    )

    assert segments


def test_load_chroma_source_reuses_one_cqt_for_cqt_and_cens(monkeypatch, tmp_path: Path):
    import librosa

    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")
    samples = np.ones(22050, dtype=np.float32) * 0.1
    cqt_spectrum = np.ones((7 * 36, 48), dtype=np.float32)
    cqt_calls: list[dict] = []
    feature_inputs: list[np.ndarray] = []

    monkeypatch.setattr(chords_mod, "_load_audio_ffmpeg", lambda *args, **kwargs: (samples, 22050))
    monkeypatch.setattr(librosa, "estimate_tuning", lambda **kwargs: 0.0)

    def fake_cqt(*args, **kwargs):
        cqt_calls.append(kwargs)
        return cqt_spectrum.astype(np.complex64)

    def fake_chroma_cqt(**kwargs):
        feature_inputs.append(kwargs["C"])
        return np.ones((12, 48), dtype=np.float32)

    def fake_chroma_cens(**kwargs):
        feature_inputs.append(kwargs["C"])
        return np.ones((12, 48), dtype=np.float32)

    monkeypatch.setattr(librosa, "cqt", fake_cqt)
    monkeypatch.setattr(librosa.feature, "chroma_cqt", fake_chroma_cqt)
    monkeypatch.setattr(librosa.feature, "chroma_cens", fake_chroma_cens)

    result = _load_chroma_source(
        source,
        name="piano",
        weight=1.0,
        end_time=1.0,
        harmonic=False,
    )

    assert result is not None
    assert len(cqt_calls) == 1
    assert feature_inputs[0] is feature_inputs[1]


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


def test_write_chord_midi_uses_ascii_safe_title_metadata(tmp_path: Path):
    out = tmp_path / "multibyte-title.mid"
    title = "譜医〜煌椰け(bassmicrobe remix)"
    segments = [ChordSegment("C", 0.0, 2.0, 0, (0, 4, 7), 0.9)]

    write_chord_midi(out, segments, bpm=120, title=title)

    data = out.read_bytes()
    assert title.encode("utf-8") not in data
    assert b"bassmicrobe remix" in data


def test_midi_text_falls_back_when_title_has_no_ascii():
    assert _midi_text("曲名だけ", "LayerLab Chord Progression", 120) == b"LayerLab Chord Progression"


def test_write_chord_midi_quantizes_detected_beats_as_quarter_notes(tmp_path: Path):
    out = tmp_path / "quantized.mid"
    segments = [
        ChordSegment("C", 2.368, 4.226, 0, (0, 4, 7), 0.9, start_beat=0, end_beat=4),
        ChordSegment("G", 4.226, 6.084, 7, (0, 4, 7), 0.8, start_beat=4, end_beat=8),
    ]

    write_chord_midi(out, segments, bpm=129, title="Quantized")

    assert _last_midi_tick(out.read_bytes()) == 8 * 480


def test_write_chord_midi_preserves_audio_leadin_and_tempo_map(tmp_path: Path):
    out = tmp_path / "tempo-map.mid"
    beat_times = [0.25, 0.75, 1.26, 1.74, 2.25]
    segments = [
        ChordSegment("C", 0.25, 2.25, 0, (0, 4, 7), 0.9, start_beat=0, end_beat=4)
    ]

    write_chord_midi(out, segments, bpm=120, title="Tempo Map", beat_times=beat_times)

    data = out.read_bytes()
    leadin_ticks = round(beat_times[0] * 120 / 60 * 480)
    assert _last_midi_tick(data) == leadin_ticks + (4 * 480)
    assert data.count(b"\xff\x51\x03") >= 4


def test_tempo_map_preserves_large_detected_interval_after_grid_repair():
    events = _tempo_map_events([0.0, 0.5, 1.0, 2.0], 120)

    assert events[-1] == (2 * 480, 1_000_000)


def test_clean_beat_times_rejects_non_finite_and_duplicate_values():
    assert _clean_beat_times([float("nan"), float("inf"), -1.0, 0.0, 0.04, 0.5]) == [
        0.0,
        0.5,
    ]


def test_write_chord_midi_keeps_no_chord_time_on_grid(tmp_path: Path):
    out = tmp_path / "no-chord-tail.mid"
    segments = [
        ChordSegment("C", 0.0, 1.0, 0, (0, 4, 7), 0.9, start_beat=0, end_beat=4),
        ChordSegment("N.C.", 1.0, 2.0, None, (), 0.0, start_beat=4, end_beat=8),
    ]

    write_chord_midi(out, segments, bpm=120, title="No Chord Tail")

    assert _last_midi_tick(out.read_bytes()) == 8 * 480


def test_write_chord_midi_can_write_marker_events(tmp_path: Path):
    out = tmp_path / "markers.mid"
    segments = [ChordSegment("C", 0.0, 1.0, 0, (0, 4, 7), 0.9, start_beat=0, end_beat=4)]

    write_chord_midi(out, segments, bpm=120, title="Markers", markers=True)

    assert b"\xff\x06\x01C" in out.read_bytes()


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
    assert job.midi_analysis_url == f"/api/jobs/{job.id}/midi-analysis.json"
    assert job.midi_analysis is not None
    assert job.midi_analysis["engine"] == "music21"
    assert (job_dir / "stems" / "midi-analysis.json").is_file()
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


def test_generate_chord_midi_propagates_cancellation(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        chords_mod,
        "detect_chord_segments",
        lambda *args, **kwargs: (_ for _ in ()).throw(JobCancelled()),
    )
    job = Job(id="abcdefabcdef", bpm=120)
    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")

    with pytest.raises(JobCancelled):
        generate_chord_midi(job, source, tmp_path)
