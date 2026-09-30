LayerLab for macOS
============================

Install:

1. Open the LayerLab DMG.
2. Drag LayerLab.app to Applications.
3. Open LayerLab from Applications.

First launch:

- LayerLab is a thin native app. It downloads a pinned, checksummed
  bundled LayerLab runtime pack on first launch.
- The setup screen shows the expected runtime download size, current
  workspace/cache usage, the data directory, and the bundled license notice.
- The runtime installs to:
  ~/Library/Application Support/LayerLab/runtime
- FFmpeg and ffprobe install to:
  ~/Library/Application Support/LayerLab/ffmpeg
- Demucs model weights download on first use and are cached under:
  ~/Library/Application Support/LayerLab/models
- The default FFmpeg/ffprobe download is the disclosed evermeet.cx
  8.1.1-tessus GPL-3.0-or-later build. It is not bundled in this DMG.
- Demucs code is MIT, but the downloaded pretrained weights are not covered by
  that code license; upstream describes them as personal/research-use artifacts.

Uninstall:

1. Delete /Applications/LayerLab.app.
2. To remove runtime files, jobs, caches, models, and logs, delete:
   ~/Library/Application Support/LayerLab

Notes:

- Internet access is required for first-run setup.
- Public releases should be Developer ID signed and notarized. Local/internal
  builds may be unsigned; set APPLE_SIGNING_IDENTITY and APPLE_NOTARIZE=1 in
  the release scripts to enable signing and notarization.
- Unsigned local builds are for development and internal testing only.
- LayerLab is an unofficial modified fork test build based on the
  original StemDeck project and is distributed under the Apache License 2.0.
  See LICENSE and NOTICE in the DMG for attribution.
- This package is not an official upstream release and is not affiliated with or
  endorsed by the original StemDeck project.
- Apache License 2.0 permits commercial use and paid redistribution when its
  conditions are met, but it does not grant trademark rights. Do not present
  this package as an official StemDeck release, official commercial offering,
  certified build, or upstream-supported product.
- Exact third-party component, version, delivery, and license details are in
  THIRD_PARTY_NOTICES.txt. Full texts are in THIRD_PARTY_LICENSES.txt and the
  machine-readable mapping is in THIRD_PARTY_INVENTORY.json.
- Commercial use of the Apache-2.0-covered application code does not grant
  commercial rights to the default Demucs pretrained weights.
