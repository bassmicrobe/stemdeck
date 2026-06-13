STEMDECK for macOS
====================

Install:

1. Open the STEMDECK DMG.
2. Drag STEMDECK.app to Applications.
3. Open STEMDECK from Applications.

First launch:

- STEMDECK is a thin native app. It downloads a pinned, checksummed STEMDECK
  runtime pack on first launch.
- The setup screen shows the expected runtime download size, current
  workspace/cache usage, the data directory, and the bundled license notice.
- The runtime currently installs to the legacy compatibility path:
  ~/Library/Application Support/StemDeck/runtime
- FFmpeg and ffprobe install to:
  ~/Library/Application Support/StemDeck/ffmpeg
- Demucs model weights download on first use and are cached under:
  ~/Library/Application Support/StemDeck/models

Uninstall:

1. Delete /Applications/STEMDECK.app.
2. To remove runtime files, jobs, caches, models, and logs, delete the legacy compatibility path:
   ~/Library/Application Support/StemDeck

Notes:

- Internet access is required for first-run setup.
- Public releases should be Developer ID signed and notarized. Local/internal
  builds may be unsigned; set APPLE_SIGNING_IDENTITY and APPLE_NOTARIZE=1 in
  the release scripts to enable signing and notarization.
- Unsigned local builds are for development and internal testing only.
- STEMDECK is based on the original StemDeck project and is distributed under the
  Apache License 2.0. See LICENSE and NOTICE in the DMG for attribution.
- This package is a modified fork, not an official upstream release.
- Third-party notices are included in THIRD_PARTY_NOTICES.txt. Final public
  releases should verify the exact Python, FFmpeg, Demucs, PyTorch, Tauri, and
  Rust dependency licenses used in the shipped artifacts.
