from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

from fastapi.testclient import TestClient


def test_health_endpoints_report_ok():
    from app.main import app

    with TestClient(app) as client:
        for path in ("/health", "/api/health"):
            r = client.get(path)
            assert r.status_code == 200
            body = r.json()
            assert body["name"] == "LayerLab"
            assert body["status"] == "ok"
            assert body["version"]
            assert "ffmpeg_configured" in body
            assert "beat_this_available" in body
            assert "music21_available" in body
            assert "jobs_dir" not in body
            assert "data_dir" not in body


def test_license_and_notice_are_served():
    from app.main import app

    with TestClient(app) as client:
        license_response = client.get("/LICENSE")
        assert license_response.status_code == 200
        assert "Apache License" in license_response.text

        notice_response = client.get("/NOTICE")
        assert notice_response.status_code == 200
        assert "unofficial modified fork test build" in notice_response.text
        assert "https://github.com/stemdeckapp/stemdeck" in notice_response.text

        third_party_response = client.get("/THIRD_PARTY_NOTICES.txt")
        assert third_party_response.status_code == 200
        assert "Demucs pretrained model weights" in third_party_response.text

        licenses_response = client.get("/THIRD_PARTY_LICENSES.txt")
        assert licenses_response.status_code == 200
        assert "LAYERLAB THIRD-PARTY LICENSES AND NOTICES" in licenses_response.text

        inventory_response = client.get("/THIRD_PARTY_INVENTORY.json")
        assert inventory_response.status_code == 200
        assert inventory_response.json()["componentCount"] > 300


def test_app_version_uses_layerlab_distribution_name():
    from app import main

    def fake_version(name: str) -> str:
        assert name == "layerlab"
        raise PackageNotFoundError

    with patch.object(main, "package_version", side_effect=fake_version):
        assert main.app_version()
