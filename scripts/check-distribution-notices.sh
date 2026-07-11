#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

require_file() {
  local path="$1"
  if [[ ! -f "$REPO_ROOT/$path" ]]; then
    echo "ERROR: missing required distribution file: $path" >&2
    exit 1
  fi
}

require_text() {
  local path="$1"
  local text="$2"
  if ! grep -Fq "$text" "$REPO_ROOT/$path"; then
    echo "ERROR: '$path' does not contain required text: $text" >&2
    exit 1
  fi
}

require_file "LICENSE"
require_file "NOTICE"
require_file "README.md"
require_file "README.ja.md"
require_file "packaging/macos/README-macOS.txt"
require_file "packaging/macos/THIRD_PARTY_NOTICES.txt"
require_file "packaging/windows/README-WINDOWS.txt"
require_file "packaging/windows/THIRD_PARTY_NOTICES.txt"
require_file "packaging/RELEASE_NOTES_UNOFFICIAL_TEST_BUILD.md"
require_file "packaging/generated/macos-arm64/THIRD_PARTY_NOTICES.md"
require_file "packaging/generated/macos-arm64/THIRD_PARTY_LICENSES.txt"
require_file "packaging/generated/macos-arm64/THIRD_PARTY_INVENTORY.json"
require_file "packaging/manual-licenses/DEMUCS_MODEL_TERMS.md"
require_file "packaging/manual-licenses/FFMPEG_RUNTIME_NOTICE.md"
require_file "packaging/third-party-components.json"
require_file "scripts/generate-license-bundle.py"
require_file "static/vendor/multitrack.LICENSE.txt"

require_text "NOTICE" "https://github.com/stemdeckapp/stemdeck"
require_text "NOTICE" "Apache License, Version 2.0"
require_text "NOTICE" "Modification notice"
require_text "NOTICE" "LayerLab"
require_text "NOTICE" "not an official upstream release"
require_text "NOTICE" "not affiliated with or endorsed"
require_text "README.ja.md" "元プロジェクト"
require_text "README.ja.md" "Apache License 2.0"
require_text "README.ja.md" "LayerLab"
require_text "README.ja.md" "公式リリースではありません"
require_text "README.ja.md" "非公式 fork test build"
require_text "README.ja.md" "この fork で加えた主な変更"
require_text "README.md" "README.ja.md"
require_text "README.md" "unofficial modified fork"
require_text "README.md" "not affiliated with"
require_text "README.md" "not covered by that MIT license"
require_text "README.md" "THIRD_PARTY_LICENSES.txt"
require_text "README.ja.md" "個人・研究用途"
require_text "README.ja.md" "THIRD_PARTY_INVENTORY.json"
require_text "packaging/RELEASE_NOTES_UNOFFICIAL_TEST_BUILD.md" "unofficial modified fork test build"
require_text "packaging/RELEASE_NOTES_UNOFFICIAL_TEST_BUILD.md" "not affiliated with or endorsed"
require_text "packaging/macos/README-macOS.txt" "LayerLab"
require_text "packaging/macos/README-macOS.txt" "not affiliated with or"
require_text "packaging/windows/README-WINDOWS.txt" "LayerLab"
require_text "packaging/windows/README-WINDOWS.txt" "not affiliated with or"
require_text "packaging/macos/THIRD_PARTY_NOTICES.txt" "LayerLab"
require_text "packaging/windows/THIRD_PARTY_NOTICES.txt" "LayerLab"
require_text "packaging/macos/THIRD_PARTY_NOTICES.txt" "GPL-3.0-or-later"
require_text "packaging/windows/THIRD_PARTY_NOTICES.txt" "personal/research-use"
require_text "packaging/generated/macos-arm64/THIRD_PARTY_NOTICES.md" "Demucs pretrained model weights"
require_text "packaging/generated/macos-arm64/THIRD_PARTY_NOTICES.md" "wavesurfer-multitrack"
require_text "packaging/generated/macos-arm64/THIRD_PARTY_LICENSES.txt" "LAYERLAB THIRD-PARTY LICENSES AND NOTICES"

for script in scripts/macos/make-app.sh scripts/macos/make-dmg.sh scripts/windows/make-portable.ps1 scripts/windows/make-installer.ps1; do
  require_text "$script" "LICENSE"
  require_text "$script" "NOTICE"
  require_text "$script" "THIRD_PARTY_NOTICES"
done

for script in scripts/macos/make-app.sh scripts/macos/make-dmg.sh scripts/windows/make-portable.ps1; do
  require_text "$script" "THIRD_PARTY_LICENSES"
  require_text "$script" "THIRD_PARTY_INVENTORY"
done

require_text "scripts/macos/make-app.sh" "APPLE_SIGNING_IDENTITY"
require_text "scripts/macos/make-dmg.sh" "APPLE_NOTARIZE"
require_text "scripts/windows/make-portable.ps1" "WINDOWS_SIGN_CERT_PATH"
require_text "scripts/windows/make-installer.ps1" "WINDOWS_SIGN_CERT_PATH"

echo "distribution notice check: OK"
