# LayerLab unofficial StemDeck fork test build

This is an unofficial modified fork test build of StemDeck:
https://github.com/stemdeckapp/stemdeck

It is not an official upstream release and is not affiliated with or endorsed by
the original StemDeck project.

License: Apache License 2.0. This distribution includes LICENSE, NOTICE, and
THIRD_PARTY_NOTICES.txt. Please review those files before redistribution.

Apache License 2.0 permits commercial use and paid redistribution when its
conditions are met. It does not grant trademark rights. Do not present this
test build as an official StemDeck release, official commercial offering,
certified build, or upstream-supported product.

## Assets

- `LayerLab-macOS-arm64.dmg`
- `LayerLab-runtime-macOS-arm64.tar.zst`
- `SHA256SUMS-macOS-arm64.txt`

## Fork changes

- Higher-quality separation presets and Demucs tuning.
- Float32-oriented processing and clipping-resistant preprocessing.
- Bass dropout repair and phase/residual repair.
- Optional per-stem denoise.
- Queue, cancellation, progress, and ETA improvements.
- Faster Demucs presets, one-pass source preparation, separation-only locking,
  and bounded parallel chord analysis.
- Neural quarter-note repair, variable-tempo MIDI maps, and labelled WCSR
  benchmark support.
- Clipping-safe mix exports with finalized WAV duration headers.
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
`THIRD_PARTY_NOTICES.txt`、`THIRD_PARTY_LICENSES.txt`、
`THIRD_PARTY_INVENTORY.json` を含めています。Demucsコードと学習済み重みの
条件は別であり、標準重みは個人・研究用途と説明されています。再配布・商用利用前に
必ず確認してください。
Apache License 2.0 は商用利用や有償配布自体を禁止していませんが、
商標権を自動許諾するものではありません。この test build を公式版、
公式販売物、認定ビルド、元プロジェクトのサポート対象のように表示しないでください。
