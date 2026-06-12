NeonSplice for macOS
====================

Install:

1. Open the NeonSplice DMG.
2. Drag NeonSplice.app to Applications.
3. Open NeonSplice from Applications.

First launch:

- NeonSplice is a thin native app. It downloads a pinned, checksummed NeonSplice
  runtime pack on first launch.
- The runtime currently installs to the legacy compatibility path:
  ~/Library/Application Support/StemDeck/runtime
- FFmpeg and ffprobe install to:
  ~/Library/Application Support/StemDeck/ffmpeg
- Demucs model weights download on first use and are cached under:
  ~/Library/Application Support/StemDeck/models

Uninstall:

1. Delete /Applications/NeonSplice.app.
2. To remove runtime files, jobs, caches, models, and logs, delete the legacy compatibility path:
   ~/Library/Application Support/StemDeck

Notes:

- Internet access is required for first-run setup.
- Public releases should be signed and notarized.
- Unsigned local builds are for development and internal testing only.
- NeonSplice is a modified derivative of StemDeck and is distributed under the
  Apache License 2.0. See LICENSE and NOTICE in the DMG for attribution.
