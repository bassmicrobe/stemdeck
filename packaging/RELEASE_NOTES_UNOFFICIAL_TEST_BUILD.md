# STEMDECK Enhanced unofficial fork test build

This is an unofficial modified fork test build of StemDeck:
https://github.com/stemdeckapp/stemdeck

It is not an official upstream release and is not affiliated with or endorsed by
the original StemDeck project.

License: Apache License 2.0. This distribution includes LICENSE, NOTICE, and
THIRD_PARTY_NOTICES.txt. Please review those files before redistribution.

## Assets

- `STEMDECK-Enhanced-macOS-arm64.dmg`
- `STEMDECK-Enhanced-runtime-macOS-arm64.tar.zst`
- `SHA256SUMS-macOS-arm64.txt`

## Fork changes

- Higher-quality separation presets and Demucs tuning.
- Float32-oriented processing and clipping-resistant preprocessing.
- Bass dropout repair and phase/residual repair.
- Optional per-stem denoise.
- Queue, cancellation, progress, and ETA improvements.
- Neon UI and responsive layout refinements.
- Desktop setup, maintenance, signing, and distribution notice improvements.

## Notes

- This is a test build. Use at your own risk.
- macOS public builds should be Developer ID signed and notarized before broad
  distribution.
- First launch downloads a pinned runtime pack, FFmpeg/ffprobe, and later the
  Demucs model cache.
- Audio processing runs locally on the user's machine.

## 日本語

これは元の StemDeck をベースにした非公式の変更版 fork test build です。
元プロジェクトの公式リリースではなく、元プロジェクトとの提携、承認、
推奨を示すものではありません。

Apache License 2.0 に基づき、配布物には LICENSE、NOTICE、
THIRD_PARTY_NOTICES.txt を含めています。再配布前に必ず確認してください。
