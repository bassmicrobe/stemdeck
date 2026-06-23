# LayerLab 評価ベンチマーク

このフォルダは、stem分離やChord MIDI推定を変更した時に品質が悪化していないか確認するための評価セット置き場です。

音源ファイルは著作権や容量の都合でリポジトリには含めません。ローカルでは、同じ曲を同じ設定で処理した `jobs/` ディレクトリを残しておき、以下のように比較します。

```bash
uv run scripts/benchmark_audio.py --jobs-root jobs --out .build/benchmark-current.json
uv run scripts/benchmark_audio.py --jobs-root jobs --baseline .build/benchmark-baseline.json --out .build/benchmark-compare.json
```

推奨する評価曲の内訳:

- 音圧が高くクリップ気味の曲
- ベースが細かく動く曲
- ピアノまたはギター主体のコードが明確な曲
- ボーカルが大きく、chromaが濁りやすい曲
- BPMが揺れる曲
- m4a/mp3/flac/wav の入力形式違い
- 短い曲と長い曲

確認する主な指標:

- `stem_sum.residual_percent`: stem合計と原音の残差
- `stem_sum.stem_sum_clipping_percent`: 合成時のクリップ率
- `chords.average_confidence`: コード推定の平均信頼度
- `chords.short_segment_count`: 1拍程度の短いコード区間数
- `chords.unstable_short_segment_count`: 1拍だけ出た不安定なdim/sus/maj7区間数
- `summary.*`: 複数ジョブ全体の平均・最大・合計
