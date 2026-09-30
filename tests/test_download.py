from __future__ import annotations

from pathlib import Path

from app.core.models import Job
from app.pipeline import download as download_mod


def test_duration_match_filter_rejects_long_sources(monkeypatch):
    monkeypatch.setattr(download_mod, "MAX_DURATION_SEC", 300)

    assert download_mod._duration_match_filter({"duration": 299}) is None
    message = download_mod._duration_match_filter({"duration": 301})

    assert message is not None
    assert "5 min" in message


def test_download_fetches_metadata_and_audio_in_one_extraction(tmp_path: Path, monkeypatch):
    calls: list[bool] = []
    options: list[dict] = []

    class FakeYoutubeDL:
        def __init__(self, opts):
            options.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, *, download):
            calls.append(download)
            (tmp_path / "source.webm").write_bytes(b"audio")
            return {
                "title": "Track",
                "duration": 42,
                "thumbnail": "https://example.test/thumb.jpg",
                "tags": ["Music"],
                "categories": ["Music"],
            }

    monkeypatch.setattr(download_mod, "YoutubeDL", FakeYoutubeDL)
    job = Job(id="abcdefabcdef")

    source = download_mod.download(
        job,
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        tmp_path,
    )

    assert source == tmp_path / "source.webm"
    assert calls == [True]
    assert len(options) == 1
    assert options[0]["match_filter"] is download_mod._duration_match_filter
    assert options[0]["concurrent_fragment_downloads"] >= 1
    assert job.title == "Track"
    assert job.duration_sec == 42
