STEMDECK Enhanced for macOS
============================

Install:

1. Open the STEMDECK Enhanced DMG.
2. Drag STEMDECK Enhanced.app to Applications.
3. Open STEMDECK Enhanced from Applications.

First launch:

- STEMDECK Enhanced is a thin native app. It downloads a pinned, checksummed
  STEMDECK Enhanced runtime pack on first launch.
- The setup screen shows the expected runtime download size, current
  workspace/cache usage, the data directory, and the bundled license notice.
- The runtime installs to:
  ~/Library/Application Support/StemDeck Enhanced/runtime
- FFmpeg and ffprobe install to:
  ~/Library/Application Support/StemDeck Enhanced/ffmpeg
- Demucs model weights download on first use and are cached under:
  ~/Library/Application Support/StemDeck Enhanced/models

Uninstall:

1. Delete /Applications/STEMDECK Enhanced.app.
2. To remove runtime files, jobs, caches, models, and logs, delete:
   ~/Library/Application Support/StemDeck Enhanced

Notes:

- Internet access is required for first-run setup.
- Public releases should be Developer ID signed and notarized. Local/internal
  builds may be unsigned; set APPLE_SIGNING_IDENTITY and APPLE_NOTARIZE=1 in
  the release scripts to enable signing and notarization.
- Unsigned local builds are for development and internal testing only.
- STEMDECK Enhanced is an unofficial modified fork test build based on the
  original StemDeck project and is distributed under the Apache License 2.0.
  See LICENSE and NOTICE in the DMG for attribution.
- This package is not an official upstream release and is not affiliated with or
  endorsed by the original StemDeck project.
- Third-party notices are included in THIRD_PARTY_NOTICES.txt. Final public
  releases should verify the exact Python, FFmpeg, Demucs, PyTorch, Tauri, and
  Rust dependency licenses used in the shipped artifacts.
