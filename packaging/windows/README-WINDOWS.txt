STEMDECK Enhanced Windows Portable Test Build
==============================================

Run
---

1. Extract the zip folder.
2. Double-click STEMDECK Enhanced.exe.
3. Let first-run setup prepare local runtime assets.

Notes
-----

- This is a portable folder, not an installer.
- No Start Menu shortcut, service, or registry integration is created.
- Public releases should Authenticode-sign STEMDECK Enhanced.exe when possible.
  Local unsigned zips are intended for development/internal testing.
- Generated files stay under data/.
- FFmpeg is downloaded during first-run setup into data/ffmpeg/.
- Demucs model weights are downloaded by the backend on first use into data/models/.
- STEMDECK Enhanced is an unofficial modified fork test build based on the
  original StemDeck project and is distributed under the Apache License 2.0.
  See LICENSE and NOTICE in this folder for attribution.
- This package is not an official upstream release and is not affiliated with or
  endorsed by the original StemDeck project.
- Third-party notices are included in THIRD_PARTY_NOTICES.txt. Final public
  releases should verify the exact Python, FFmpeg, Demucs, PyTorch, Tauri, and
  Rust dependency licenses used in the shipped artifacts.

Troubleshooting
---------------

- If setup fails, check internet access and retry.
- If a job fails, inspect data/jobs/ and data/logs/ when logs are added.
- Deleting data/ forces first-run setup to recreate runtime state.
