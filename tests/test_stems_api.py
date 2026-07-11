from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.stems import _mixdown_codec_args
from app.core.config import ffmpeg_available
from app.core.models import Job
from app.core.registry import _jobs


@pytest.fixture(autouse=True)
def _isolate_registry():
    _jobs.clear()
    yield
    _jobs.clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.api.stems as stems_mod

    monkeypatch.setattr(stems_mod, "JOBS_DIR", tmp_path)
    from app.main import app

    return TestClient(app)


def _make_stem_file(tmp_path, job_id: str, name: str, contents: bytes = b"RIFF"):
    stems_dir = tmp_path / job_id / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    path = stems_dir / f"{name}.wav"
    path.write_bytes(contents)
    return path


def test_rejects_malformed_job_id(client):
    for bad_id in ("../etc", "ABC", "abcdefabcdef0", "abcdefabcde", "abcd-efabcdef"):
        r = client.get(f"/api/jobs/{bad_id}/stems/vocals.wav")
        assert r.status_code == 404, f"id {bad_id!r} should 404"


def test_rejects_unknown_stem_name(client):
    job = Job(id="abcdefabcdef")
    job.status = "done"
    _jobs[job.id] = job
    r = client.get(f"/api/jobs/{job.id}/stems/banjo.wav")
    assert r.status_code == 404


def test_requires_done_status(client, tmp_path):
    job = Job(id="abcdefabcdef")
    job.status = "separating"
    _jobs[job.id] = job
    _make_stem_file(tmp_path, job.id, "vocals")
    r = client.get(f"/api/jobs/{job.id}/stems/vocals.wav")
    assert r.status_code == 404


def test_serves_done_job_stem(client, tmp_path):
    job = Job(id="abcdefabcdee")
    job.status = "done"
    _jobs[job.id] = job
    _make_stem_file(tmp_path, job.id, "vocals", b"RIFF1234")
    r = client.get(f"/api/jobs/{job.id}/stems/vocals.wav")
    assert r.status_code == 200
    assert r.content == b"RIFF1234"
    assert r.headers["content-type"] == "audio/wav"
    assert "stems_Standard_Noise_off_Auto_All_6_stem_vocals.wav" in r.headers["content-disposition"]


def test_serves_audio_ready_stem_while_chord_analysis_continues(client, tmp_path):
    job = Job(id="abcdefabcded", status="processing", audio_ready=True, analysis_ready=False)
    _jobs[job.id] = job
    _make_stem_file(tmp_path, job.id, "vocals", b"RIFF-ready")

    response = client.get(f"/api/jobs/{job.id}/stems/vocals.wav")

    assert response.status_code == 200
    assert response.content == b"RIFF-ready"


# --- peaks endpoint ---


def _make_peaks_file(tmp_path, job_id: str, data: dict) -> None:
    stems_dir = tmp_path / job_id / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    (stems_dir / "peaks.json").write_text(json.dumps(data), encoding="utf-8")


def test_peaks_returns_json_for_done_job(client, tmp_path):
    job = Job(id="abcdefabcdea")
    job.status = "done"
    _jobs[job.id] = job
    payload = {"vocals": [[-0.1, 0.2], [-0.3, 0.4]], "drums": [[-0.5, 0.6]]}
    _make_peaks_file(tmp_path, job.id, payload)

    r = client.get(f"/api/jobs/{job.id}/stems/peaks.json")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"
    assert "immutable" in r.headers.get("cache-control", "")
    assert r.json() == payload


def test_peaks_returns_json_when_audio_is_ready(client, tmp_path):
    job = Job(id="abcdefabcda0", status="processing", audio_ready=True)
    _jobs[job.id] = job
    payload = {"bass": [[-0.2, 0.2]]}
    _make_peaks_file(tmp_path, job.id, payload)

    response = client.get(f"/api/jobs/{job.id}/stems/peaks.json")

    assert response.status_code == 200
    assert response.json() == payload


def test_peaks_404_when_file_missing(client, tmp_path):
    job = Job(id="abcdefabcdeb")
    job.status = "done"
    _jobs[job.id] = job
    # stems dir exists but no peaks.json
    (tmp_path / job.id / "stems").mkdir(parents=True, exist_ok=True)

    r = client.get(f"/api/jobs/{job.id}/stems/peaks.json")
    assert r.status_code == 404


def test_peaks_404_for_non_done_job(client):
    job = Job(id="abcdefabcdec")
    job.status = "separating"
    _jobs[job.id] = job

    r = client.get(f"/api/jobs/{job.id}/stems/peaks.json")
    assert r.status_code == 404


def test_peaks_rejects_malformed_job_id(client):
    for bad_id in ("../etc", "ABC", "abcdefabcdef0", "abcdefabcde"):
        r = client.get(f"/api/jobs/{bad_id}/stems/peaks.json")
        assert r.status_code == 404, f"id {bad_id!r} should 404"


def test_chord_midi_returns_file_for_done_job(client, tmp_path):
    job = Job(id="abcdefabcda1", status="done", title="Chord Song")
    _jobs[job.id] = job
    stems_dir = tmp_path / job.id / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    (stems_dir / "chords.mid").write_bytes(b"MThd1234")

    r = client.get(f"/api/jobs/{job.id}/chords.mid")

    assert r.status_code == 200
    assert r.content == b"MThd1234"
    assert "Chord_Song" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('_chords.mid"')


def test_chord_midi_variant_renders_from_metadata(client, tmp_path):
    job = Job(id="abcdefabcda3", status="done", title="Chord Song", bpm=120)
    job.chord_progression = [
        {"label": "Bm7", "start": 0.0, "end": 1.0, "start_beat": 0, "end_beat": 1, "confidence": 0.9},
        {"label": "Dmaj7", "start": 1.0, "end": 3.0, "start_beat": 1, "end_beat": 3, "confidence": 0.8},
        {"label": "Gmaj7", "start": 3.0, "end": 4.0, "start_beat": 3, "end_beat": 4, "confidence": 0.7},
    ]
    _jobs[job.id] = job
    (tmp_path / job.id / "stems").mkdir(parents=True, exist_ok=True)

    r = client.get(f"/api/jobs/{job.id}/chords.mid?style=triads&grid=bar&markers=true")

    assert r.status_code == 200
    assert r.content.startswith(b"MThd")
    assert b"\xff\x06" in r.content
    assert "triads_bar_markers" in r.headers["content-disposition"]


def test_chord_csv_variant_renders_from_metadata(client, tmp_path):
    job = Job(id="abcdefabcda4", status="done", title="Chord CSV")
    job.chord_progression = [
        {"label": "Cmaj7", "start": 0.0, "end": 2.0, "start_beat": 0, "end_beat": 4, "confidence": 0.8},
    ]
    _jobs[job.id] = job
    (tmp_path / job.id / "stems").mkdir(parents=True, exist_ok=True)

    r = client.get(f"/api/jobs/{job.id}/chords.csv?style=triads")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "Chord_CSV" in r.headers["content-disposition"]
    assert "C,0.000,2.000,0,4,0.800" in r.text


def test_chord_midi_404_when_missing(client, tmp_path):
    job = Job(id="abcdefabcda2", status="done")
    _jobs[job.id] = job
    (tmp_path / job.id / "stems").mkdir(parents=True, exist_ok=True)

    r = client.get(f"/api/jobs/{job.id}/chords.mid")

    assert r.status_code == 404


def test_midi_analysis_returns_generated_json(client, tmp_path):
    job = Job(id="abcdefabcda5", status="done", title="Chord Analysis")
    _jobs[job.id] = job
    stems_dir = tmp_path / job.id / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    payload = b'{"engine":"music21","detected_midi_key":"C major"}'
    (stems_dir / "midi-analysis.json").write_bytes(payload)

    r = client.get(f"/api/jobs/{job.id}/midi-analysis.json")

    assert r.status_code == 200
    assert r.content == payload
    assert "Chord_Analysis" in r.headers["content-disposition"]


# ── Export All Stems (.zip) ──


def test_all_stems_zip_all_when_no_subset(client, tmp_path):
    import io
    import zipfile

    job = Job(id="abcdefabcdab")
    job.status = "done"
    job.title = "My Song! (Live)"
    _jobs[job.id] = job
    _make_stem_file(tmp_path, job.id, "vocals", b"RIFFvocals")
    _make_stem_file(tmp_path, job.id, "drums", b"RIFFdrums")
    _make_stem_file(tmp_path, job.id, "bass", b"RIFFbass")

    r = client.get(f"/api/jobs/{job.id}/stems/all.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "My_Song_Live_Standard_Noise_off_Auto_All_6_stem_stems.zip" in r.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == ["LAYERLAB_PROFILE.txt", "bass.wav", "drums.wav", "vocals.wav"]
    assert zf.read("vocals.wav") == b"RIFFvocals"
    manifest = zf.read("LAYERLAB_PROFILE.txt").decode()
    assert "Profile: Standard / Noise off / Auto / All 6-stem" in manifest
    assert "Exported stems: vocals, drums, bass" in manifest


def test_all_stems_zip_only_active_subset(client, tmp_path):
    """Only the requested (active) stems are bundled — not every stem on disk."""
    import io
    import zipfile

    job = Job(id="abcdefabcdba")
    job.status = "done"
    _jobs[job.id] = job
    for name in ("vocals", "drums", "bass", "guitar", "piano", "other"):
        _make_stem_file(tmp_path, job.id, name, f"RIFF{name}".encode())

    r = client.get(f"/api/jobs/{job.id}/stems/all.zip?stems=vocals,bass")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == ["LAYERLAB_PROFILE.txt", "bass.wav", "vocals.wav"]
    assert "Exported stems: vocals, bass" in zf.read("LAYERLAB_PROFILE.txt").decode()


def test_all_stems_zip_rejects_unknown_stem(client, tmp_path):
    job = Job(id="abcdefabcdbb")
    job.status = "done"
    _jobs[job.id] = job
    _make_stem_file(tmp_path, job.id, "vocals")
    r = client.get(f"/api/jobs/{job.id}/stems/all.zip?stems=vocals,banjo")
    assert r.status_code == 422


def test_all_stems_zip_rejects_bad_format(client, tmp_path):
    job = Job(id="abcdefabcdac")
    job.status = "done"
    _jobs[job.id] = job
    _make_stem_file(tmp_path, job.id, "vocals")
    r = client.get(f"/api/jobs/{job.id}/stems/all.zip?format=ogg")
    assert r.status_code == 422


def test_all_stems_zip_404_for_unknown_job(client):
    r = client.get("/api/jobs/abcdefabcdad/stems/all.zip")
    assert r.status_code == 404


def test_all_stems_zip_rejects_malformed_job_id(client):
    for bad_id in ("../etc", "ABC", "abcdefabcdef0", "abcdefabcde"):
        r = client.get(f"/api/jobs/{bad_id}/stems/all.zip")
        assert r.status_code == 404, f"id {bad_id!r} should 404"


def test_all_stems_zip_404_when_no_stem_files(client, tmp_path):
    job = Job(id="abcdefabcdae")
    job.status = "done"
    _jobs[job.id] = job
    (tmp_path / job.id / "stems").mkdir(parents=True, exist_ok=True)
    r = client.get(f"/api/jobs/{job.id}/stems/all.zip")
    assert r.status_code == 404


def test_all_stems_zip_mp3(client, tmp_path):
    """MP3 zip transcodes via ffmpeg; skip if ffmpeg isn't available."""
    import io
    import zipfile

    if not ffmpeg_available():
        pytest.skip("ffmpeg not available")

    # A real (tiny) WAV so ffmpeg can transcode it.
    import struct

    sr = 8000
    nframes = sr // 10
    data = b"\x00\x00" * nframes
    hdr = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    hdr += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16)
    hdr += b"data" + struct.pack("<I", len(data))
    wav = hdr + data

    job = Job(id="abcdefabcdaf")
    job.status = "done"
    job.title = "Track"
    _jobs[job.id] = job
    _make_stem_file(tmp_path, job.id, "vocals", wav)

    r = client.get(f"/api/jobs/{job.id}/stems/all.zip?format=mp3")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == ["LAYERLAB_PROFILE.txt", "vocals.mp3"]
    assert len(zf.read("vocals.mp3")) > 0


# --- dynamic mixdown endpoint (#183) ---


def _tiny_wav(seconds: float = 0.2, sr: int = 8000, amplitude: float = 0.0) -> bytes:
    """A minimal constant-level PCM16 mono WAV for real FFmpeg tests."""
    import struct

    nframes = int(sr * seconds)
    sample = max(-32768, min(32767, round(amplitude * 32767)))
    data = struct.pack("<h", sample) * nframes
    hdr = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    hdr += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16)
    hdr += b"data" + struct.pack("<I", len(data))
    return hdr + data


def _done_job_with_stems(tmp_path, job_id: str, names) -> Job:
    job = Job(id=job_id)
    job.status = "done"
    job.title = "Track"
    _jobs[job.id] = job
    for name in names:
        _make_stem_file(tmp_path, job_id, name, _tiny_wav())
    return job


def test_mixdown_rejects_bad_ext(client):
    r = client.get("/api/jobs/abcdef000001/mixdown.ogg?stems=vocals&gains=1")
    assert r.status_code == 404


def test_mixdown_rejects_length_mismatch(client):
    r = client.get("/api/jobs/abcdef000001/mixdown.wav?stems=vocals,drums&gains=1")
    assert r.status_code == 422


def test_mixdown_rejects_empty(client):
    r = client.get("/api/jobs/abcdef000001/mixdown.wav?stems=&gains=")
    assert r.status_code == 422


def test_mixdown_rejects_bad_gain(client):
    for gains in ("abc", "-1", "99"):
        r = client.get(f"/api/jobs/abcdef000001/mixdown.wav?stems=vocals&gains={gains}")
        assert r.status_code == 422, f"gains={gains!r} should 422"


def test_mixdown_rejects_unknown_stem(client):
    # "mix" is intentionally excluded (it is the static pre-render we replace).
    for stem in ("banjo", "mix"):
        r = client.get(f"/api/jobs/abcdef000001/mixdown.wav?stems={stem}&gains=1")
        assert r.status_code == 422, f"stem={stem!r} should 422"


def test_mixdown_rejects_bad_region(client):
    r = client.get("/api/jobs/abcdef000001/mixdown.wav?stems=vocals&gains=1&start=5&end=2")
    assert r.status_code == 422


def test_mixdown_rejects_malformed_job_id(client):
    r = client.get("/api/jobs/ZZZ/mixdown.wav?stems=vocals&gains=1")
    assert r.status_code == 404


def test_mixdown_requires_done(client, tmp_path):
    job = Job(id="abcdef000002")
    job.status = "separating"
    _jobs[job.id] = job
    _make_stem_file(tmp_path, job.id, "vocals", _tiny_wav())
    r = client.get(f"/api/jobs/{job.id}/mixdown.wav?stems=vocals&gains=1")
    assert r.status_code == 404


def test_mixdown_404_for_missing_stem_file(client, tmp_path):
    job = Job(id="abcdef000003")
    job.status = "done"
    _jobs[job.id] = job  # no stem files on disk
    r = client.get(f"/api/jobs/{job.id}/mixdown.wav?stems=vocals&gains=1")
    assert r.status_code == 404


def _skip_without_ffmpeg():
    if not ffmpeg_available():
        pytest.skip("ffmpeg not available")


def test_mixdown_wav_happy(client, tmp_path):
    _skip_without_ffmpeg()
    job = _done_job_with_stems(tmp_path, "abcdef000010", ["vocals", "drums"])
    r = client.get(f"/api/jobs/{job.id}/mixdown.wav?stems=vocals,drums&gains=1.000,0.500")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert "Track_Standard_Noise_off_Auto_All_6_stem_mix.wav" in r.headers[
        "content-disposition"
    ]
    assert r.content[:4] == b"RIFF"


def test_mixdown_graph_limits_without_auto_makeup_gain(client, tmp_path, monkeypatch):
    import app.api.stems as stems_api

    job = _done_job_with_stems(tmp_path, "abcdef000016", ["vocals", "drums"])
    commands = []

    def fake_stream(cmd):
        commands.append(cmd)
        yield _tiny_wav()

    monkeypatch.setattr(stems_api, "_stream_ffmpeg", fake_stream)

    response = client.get(
        f"/api/jobs/{job.id}/mixdown.mp3?stems=vocals,drums&gains=1.000,1.000"
    )

    assert response.status_code == 200
    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "alimiter=limit=0.98" in graph
    assert "level=0" in graph
    assert "latency=1" in graph


@pytest.mark.asyncio
async def test_rendered_export_cleans_temp_file_when_ffmpeg_cannot_start(tmp_path, monkeypatch):
    import os

    import app.api.stems as stems_api

    output = tmp_path / "export.wav"

    def fake_mkstemp(**kwargs):
        return os.open(output, os.O_CREAT | os.O_RDWR), str(output)

    async def fail_to_start(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(stems_api.tempfile, "mkstemp", fake_mkstemp)
    monkeypatch.setattr(stems_api.asyncio, "create_subprocess_exec", fail_to_start)

    with pytest.raises(FileNotFoundError):
        await stems_api._render_ffmpeg_temp(["ffmpeg"], suffix=".wav")

    assert not output.exists()


def test_mixdown_limiter_prevents_clipping_without_changing_duration(client, tmp_path):
    import io
    import struct
    import wave

    job = _done_job_with_stems(tmp_path, "abcdef000017", ["vocals", "drums"])
    for name in ("vocals", "drums"):
        _make_stem_file(tmp_path, job.id, name, _tiny_wav(amplitude=0.8))

    response = client.get(
        f"/api/jobs/{job.id}/mixdown.wav?stems=vocals,drums&gains=1.000,1.000"
    )

    assert response.status_code == 200
    with wave.open(io.BytesIO(response.content), "rb") as wav:
        assert wav.getnframes() == 1600
        samples = struct.unpack(f"<{wav.getnframes()}h", wav.readframes(wav.getnframes()))
    assert max(abs(sample) for sample in samples) <= round(0.98 * 32767) + 1


def test_mixdown_single_lane_skips_amix(client, tmp_path):
    _skip_without_ffmpeg()
    job = _done_job_with_stems(tmp_path, "abcdef000011", ["bass"])
    r = client.get(f"/api/jobs/{job.id}/mixdown.wav?stems=bass&gains=1.500")
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF"


def test_mixdown_mp3_happy(client, tmp_path):
    _skip_without_ffmpeg()
    job = _done_job_with_stems(tmp_path, "abcdef000012", ["vocals", "bass"])
    r = client.get(f"/api/jobs/{job.id}/mixdown.mp3?stems=vocals,bass&gains=1,1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert len(r.content) > 0


def test_mixdown_region_trim(client, tmp_path):
    import io
    import wave

    _skip_without_ffmpeg()
    job = _done_job_with_stems(tmp_path, "abcdef000013", ["vocals", "drums"])
    r = client.get(
        f"/api/jobs/{job.id}/mixdown.wav?stems=vocals,drums&gains=1,1&start=0.05&end=0.15"
    )
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF"
    with wave.open(io.BytesIO(r.content), "rb") as wav:
        assert wav.getnframes() == 800


def test_mixdown_flac_happy(client, tmp_path):
    _skip_without_ffmpeg()
    job = _done_job_with_stems(tmp_path, "abcdef000014", ["vocals", "bass"])
    r = client.get(f"/api/jobs/{job.id}/mixdown.flac?stems=vocals,bass&gains=1,1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/flac"
    assert r.content[:4] == b"fLaC"  # FLAC stream marker


def test_mixdown_rejects_unknown_ext_still(client):
    # ogg remains unsupported even after adding flac.
    r = client.get("/api/jobs/abcdef000001/mixdown.ogg?stems=vocals&gains=1")
    assert r.status_code == 404


def test_mixdown_wav_codec_follows_quality_preset():
    assert _mixdown_codec_args("wav", "standard") == ["-c:a", "pcm_s16le", "-f", "wav"]
    assert _mixdown_codec_args("wav", "max") == ["-c:a", "pcm_f32le", "-f", "wav"]
    assert _mixdown_codec_args("wav", "ultra") == ["-c:a", "pcm_f32le", "-f", "wav"]
    assert _mixdown_codec_args("flac", "max") == ["-c:a", "flac", "-f", "flac"]


def test_all_stems_zip_flac(client, tmp_path):
    _skip_without_ffmpeg()
    job = _done_job_with_stems(tmp_path, "abcdef000015", ["vocals"])
    job.title = "Track"
    import io
    import zipfile

    r = client.get(f"/api/jobs/{job.id}/stems/all.zip?format=flac")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == ["LAYERLAB_PROFILE.txt", "vocals.flac"]
    assert zf.read("vocals.flac")[:4] == b"fLaC"
