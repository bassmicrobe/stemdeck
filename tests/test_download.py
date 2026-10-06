from __future__ import annotations

from pathlib import Path

import pytest

from app.core.models import Job
from app.pipeline import download as download_mod


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@soundcloud.com/artist/track",
        "https://soundcloud.com:8080/artist/track",
        "https://soundcloud.com:bad/artist/track",
        "https://user@youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com:9999/watch?v=dQw4w9WgXcQ",
    ],
)
def test_media_url_rejects_credentials_and_nonstandard_ports(url):
    with pytest.raises(download_mod.InvalidYouTubeURL):
        download_mod.validate_youtube_url(url)


def test_standard_port_media_urls_remain_supported():
    assert download_mod.validate_youtube_url("https://soundcloud.com:443/artist/track")
    assert download_mod.validate_youtube_url("http://youtu.be:80/dQw4w9WgXcQ") == (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"is_live": True},
        {"live_status": "is_upcoming"},
        {"duration": float("nan")},
        {"duration": float("inf")},
        {"duration": -1},
    ],
)
def test_metadata_filter_rejects_unbounded_or_invalid_sources(metadata):
    assert download_mod._duration_match_filter(metadata) is not None


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


def test_download_exposes_duration_before_media_transfer_finishes(tmp_path: Path, monkeypatch):
    observed_durations: list[float | None] = []
    job = Job(id="abcdefabcde1")

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, *, download):
            hook = self.opts["progress_hooks"][0]
            hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 25,
                    "total_bytes": 100,
                    "info_dict": {"duration": 42},
                }
            )
            observed_durations.append(job.duration_sec)
            (tmp_path / "source.webm").write_bytes(b"audio")
            return {"title": "Track", "duration": 42}

    monkeypatch.setattr(download_mod, "YoutubeDL", FakeYoutubeDL)

    download_mod.download(
        job,
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        tmp_path,
    )

    assert observed_durations == [42.0]
