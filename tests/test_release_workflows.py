from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_macos_release_uses_layerlab_artifacts_and_current_ci_repository():
    workflow = (ROOT / ".woodpecker" / "macos-release.yml").read_text(encoding="utf-8")

    assert "LayerLab-macOS-arm64.dmg" in workflow
    assert "LayerLab-runtime-macOS-arm64.tar.zst" in workflow
    assert "LayerLab.app" in workflow
    assert "StemDeck-macOS" not in workflow
    assert '--repo "$RELEASE_REPO"' in workflow
    assert 'RELEASE_REPO="${CI_REPO:-bassmicrobe/stemdeck}"' in workflow
    assert "SETUPTOOLS_SCM_PRETEND_VERSION" in workflow
    assert 'pyproject.toml' not in workflow


def test_windows_release_uses_layerlab_artifacts_and_current_ci_repository():
    workflow = (ROOT / ".woodpecker" / "windows-release.yml").read_text(encoding="utf-8")

    assert "LayerLab-Windows-x64.NVIDIA.zip" in workflow
    assert "LayerLab-Windows-x64.zip" in workflow
    assert "StemDeck-Windows" not in workflow
    assert '--repo "$releaseRepo"' in workflow
    assert '$releaseRepo = if ($env:CI_REPO)' in workflow
    assert "$env:SETUPTOOLS_SCM_PRETEND_VERSION" in workflow
    assert "Get-Content \"pyproject.toml\"" not in workflow
