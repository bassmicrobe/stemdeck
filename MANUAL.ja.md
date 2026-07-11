# LayerLab ユーザーマニュアル

LayerLab は、音源をローカル環境で stem 分離するためのアプリです。MP3、WAV、FLAC、M4A、または YouTube URL を入力し、ボーカル、ドラム、ベース、ギター、ピアノ、その他の stem に分離できます。

このマニュアルは、アプリを使う人向けの操作ガイドです。開発、配布、署名、ライセンス詳細は [README.ja.md](README.ja.md) も参照してください。

## 重要な前提

- LayerLab は [stemdeckapp/stemdeck](https://github.com/stemdeckapp/stemdeck) をベースにした非公式 fork test build です。
- 元プロジェクトの公式リリースではなく、元プロジェクトと提携・承認関係はありません。
- 処理は基本的にローカルマシン上で完結します。音源をクラウドへアップロードする設計ではありません。
- YouTube URL 入力は、処理する権利を持つコンテンツで使ってください。LayerLab はダウンローダーではなく stem 分離ツールです。
- 再配布する場合は `LICENSE`、`NOTICE`、`THIRD_PARTY_NOTICES.txt`、`THIRD_PARTY_LICENSES.txt`、`THIRD_PARTY_INVENTORY.json` を同梱し、Apache License 2.0 と各依存関係・モデル重みの条件を確認してください。

## 起動方法

### ローカルWeb版

```sh
./run.sh setup
./run.sh start
```

ブラウザで `http://127.0.0.1:8765/` を開きます。

停止する場合:

```sh
./run.sh stop
```

状態確認:

```sh
./run.sh status
curl -s http://127.0.0.1:8765/api/health
```

### デスクトップ版

macOS の場合は `.dmg` を開き、`LayerLab.app` を Applications にコピーして起動します。初回起動時にDMG同梱の Python runtimeを検証・展開し、FFmpeg、ffprobe、必要なモデルは必要に応じて取得します。

初回セットアップにはインターネット接続と数GB程度の空き容量が必要です。Demucs model は初回分離時にキャッシュされ、2回目以降は再利用されます。

## 画面構成

### 上部バー

- ファイル選択またはURL入力を行います。
- `Extract` で抽出する stem を選びます。
- `Quality` で分離品質を選びます。
- `Device` で `Auto`、`CPU`、`Apple GPU`、`NVIDIA CUDA` のどれを使うか選びます。利用できないGPUは選択できません。
- `Clean` で分離後のノイズ除去を選びます。
- `Split stems` でジョブをキューに追加します。

### ライブラリ

- 最近処理した曲、キュー中の曲、完了済みの曲を表示します。
- フォルダ作成、検索、お気に入り、ゴミ箱を使えます。
- 処理中の曲は `Queued`、`Processing`、`Separating` などの状態と進捗を表示します。
- 完了済みの曲をクリックすると、プレイヤーとミキサーに読み込まれます。
- 左レールの `Logs` から、現在のセッション全体または曲ごとの処理ログを確認できます。

### 中央エリア

- 曲の解析情報、stem presence、波形、ミキサーが表示されます。
- 処理中は進捗HUDが前面に表示されます。
- 複数曲をキューに追加した場合、完了済みの選択曲は前面に残り、次の抽出はバックグラウンドで続きます。

### 下部プレイヤー

- 再生、停止、ループ、書き出しを操作します。
- `Export Mix` からミックスやstemを書き出せます。
- 完了済み曲では `Extracted`、`Processed`、`Profile`、`Source`、`Quality`、`Repair` が表示されます。

## 基本ワークフロー

1. 音源ファイルを選択するか、YouTube URL を入力します。
2. 抽出したい stem を選びます。
3. 必要に応じて `Quality` と `Clean` を設定します。
4. `Split stems` を押します。
5. 進捗バー、経過時間、残り推定時間を確認します。
6. 完了後、曲をクリックして試聴します。
7. 必要に応じてミックスや個別stemを書き出します。

## 対応入力

- MP3
- WAV
- FLAC
- M4A
- YouTube URL

長尺音源は処理時間とディスク使用量が大きくなります。既定では長すぎる音源は拒否されます。

## Stem選択

`All` を選ぶと利用可能な全stemを抽出対象にします。個別に `Vocals`、`Drums`、`Bass`、`Guitar`、`Piano`、`Other` を選べます。

`High` / `Max` / `Ultra` 品質では、品質優先のため基本的に4-stem構成になります。

## 品質設定

| 設定 | 目的 | 目安 |
|---|---|---|
| `Standard` | 通常利用向け | 最速、6-stem、1回推論 |
| `High` | 品質と速度の両立 | 4-stem、4回推論、float32、補正強化 |
| `Max` | 精度優先 | 4-stem、8回推論 |
| `Ultra` | 実用上の最高品質 | 4-stem、16回推論、overlap強化 |

`htdemucs_ft` は4モデルのアンサンブルです。そのためshift数1でも4回、shift数4では16回の推論を行います。Standard/Highのoverlapは`0.20`、Max/Ultraは`0.25`です。`Ultra` は `Max` より処理時間が伸びますが、旧設定の64回推論から16回へ削減し、品質向上幅に対して過大だった待ち時間を抑えています。

## Device設定

| 設定 | 内容 | 目安 |
|---|---|---|
| `Auto` | アプリが利用可能なGPUを優先して選択 | 通常はこれ |
| `Apple GPU` | Apple Silicon の MPS を使用 | Macで高速化したい場合 |
| `NVIDIA CUDA` | NVIDIA GPU の CUDA を使用 | Windows/LinuxのCUDA環境向け |
| `CPU` | GPUを使わずCPUで処理 | GPU不調時、比較検証、互換性重視 |

`Device` はジョブごとに保存され、`Profile`、書き出しファイル名、stem ZIP内の `LAYERLAB_PROFILE.txt` にも記録されます。同じ曲でもCPU版とGPU版を別プロファイルとして比較できます。

## Clean設定

| 設定 | 内容 | 注意 |
|---|---|---|
| `Noise off` | ノイズ除去なし | 最も自然 |
| `Light denoise` | 軽いノイズ除去 | 迷ったらこちら |
| `Strong denoise` | 強めのノイズ除去 | ノイズは減るが音が水っぽくなる場合あり |

ノイズ除去は分離後のstemに対する後処理です。元の分離精度そのものを上げる機能ではありません。

## 進捗表示

進捗バーはパイプライン全体の進捗です。Demucs単体の進捗ではありません。

主な段階:

- `Preparing audio`
- `Analyzing`
- `Preparing separation input`
- `Separating`
- `Collecting stems`
- `Restoring stem levels`
- `Checking bass dropouts`
- `Checking phase coherence`
- `Checking stem denoise`
- `Stabilizing stems`
- `Rendering waveforms`

`Elapsed` は処理開始からの経過時間です。進捗イベントとは別のタイマーで計算します。`ETA` は現在の進捗から推定するため、曲や処理段階によって揺れます。

## ログと診断

進捗HUDの `Logs` は表示中ジョブのログを開きます。左レールの `Logs` は全体ログを開き、上部の `Source` から対象曲を切り替えられます。

- 処理時刻、ログレベル、全体進捗率、パイプライン段階、メッセージを表示します。
- ログ画面は1.5秒ごとに自動更新されます。
- エラー/キャンセルジョブのログもレジストリへ保存されるため、原因確認に使えます。
- 各ジョブは最新300件、現在のセッション全体は最新1000件まで保持します。
- 古いジョブとログはジョブ保存期限に従って掃除されます。

## キューとバックグラウンド処理

複数曲を追加できます。完了済みの曲を選択している状態で次の曲が始まっても、前面のプレイヤーやダウンロード操作は維持されます。

同じ曲でも、`Quality`、`Device`、`Clean`、選択stemが異なれば別プロファイルとしてライブラリに残せます。たとえば同じ音源で `Standard / Noise off / Auto (Apple GPU) / 4-stem` と `Ultra / Strong denoise / CPU / Vocals+Bass` を比較できます。

選択中の曲はStemパネル上部に `Profile` として抽出設定が表示されます。個別stem、ミックス、リージョン、stem ZIPを書き出す場合もファイル名にプロファイル名が入るため、同じ曲の複数設定を書き出しても判別できます。

同時に重い解析・stem抽出を何本走らせるかは、マシン性能に応じて自動判定されます。

- MPS/CUDA: 安全優先で通常1本
- CPUのみで十分なコアとメモリがある場合: 最大2本
- 手動上書き: `STEMDECK_PIPELINE_CONCURRENCY=1` から `4`

複数のローカルLayerLabバックエンドが同時起動しても、`STEMDECK_PIPELINE_LOCK` を基準に同時実行数ぶんの共有スロットを使い、マシン全体で過剰なDemucs同時実行を防ぎます。

## キャンセル

処理中のジョブは `Cancel` でキャンセルできます。キャンセルすると、実行中のDemucs/ffmpegプロセスを停止し、途中生成物を削除します。

キャンセルできるのは前面で表示中のジョブです。バックグラウンドジョブを止めたい場合は、そのジョブを選択して状態を確認してください。

## 試聴とミキサー

完了済み曲を選ぶと、stemごとの波形とミキサーが表示されます。

- `Play/Pause`: 再生/一時停止
- `Stop`: 停止
- `M`: stemをミュート
- `S`: stemをソロ
- `Monitor`: そのstemだけを確認
- フェーダー: stem音量を調整
- ダブルクリック: フェーダーを0 dBに戻す
- ループ範囲: ルーラー上で指定
- ズーム: 波形表示を拡大/縮小

## 書き出し

`Export Mix` から書き出します。

主な用途:

- 選択stemだけのミックスを書き出す
- 個別stemを書き出す
- ループ範囲を書き出す
- 4分音符グリッド上で推定したコード進行をMIDIまたはCSVで書き出す
- WAV / MP3 / FLAC などの形式を選ぶ

書き出しファイル名には、曲名に加えて `Quality` / `Clean` / 選択stem のプロファイルが入ります。`Export All Stems` のZIPには `LAYERLAB_PROFILE.txt` も同梱され、解凍後でも抽出設定を確認できます。

`Export Chord Guide` は、拍グリッドとchroma解析から推定した補助用のコード進行です。完全な採譜ではありませんが、DAWでコード進行の下書きとして使えます。書き出し時に `MIDI` / `CSV`、`Auto` / `Triads only` / `Allow 7ths`、`1/4 beat grid` / `1 bar blocks` を選べます。MIDIではDAW markerイベントも任意で含められます。

全品質で `Beat This! final0` が拍とダウンビートを検出し、孤立した2倍間隔は
欠落した4分音符として補間します。失敗時はlibrosaに自動で戻ります。MIDIには音源先頭までのオフセットと拍ごとの
テンポ変更が保存されるため、一定BPMに丸めるよりDAW上の拍へ合わせやすくなります。生成後の
`/api/jobs/{job_id}/midi-analysis.json` には、music21によるMIDIの推定キー、
音域、四分音符長、平均信頼度、ローマ数字コード進行が保存されます。

WAVは音質劣化が少ない一方、ファイルサイズが大きくなります。
ミックス書き出しではmake-up gainを行わないlook-ahead limiterが0 dBFS超過だけを
抑えます。WAVは一時ファイルへ完成させてから返すため、RIFFヘッダーの長さが未確定の
ままにならず、プレイヤーで数時間のファイルとして誤表示されません。

## 曲ごとの記録

完了した曲には以下の情報が保存されます。

- `Extracted`: 抽出完了時刻
- `Processed`: 抽出処理にかかった時間
- `Source`: ローカルファイルまたはWeb
- `Quality`: 入力種別や品質目安
- `Repair`: bass repair、phase repair、denoiseの適用状況
- BPM
- Beat grid: 検出した拍数。波形上にも拍線を表示します（現在は解析対象の先頭180秒）。
- Key
- LUFS
- Dynamic Range
- Tempo Stability
- Chord guide: 4分音符グリッド上で推定したコード進行MIDI/CSV
- Stem Presence

古いバージョンで処理した曲は、`Processed` が `—` になる場合があります。新しいバージョンで処理した曲から正確に記録されます。

## データ保存場所

ローカルWeb版では、既定でリポジトリ内の `jobs/` に処理結果が保存されます。

デスクトップ版では、アプリデータ領域と `Documents/LayerLab` 系のフォルダを使います。実際の場所はセットアップ画面やアプリ内表示を確認してください。

長尺音源、`High` / `Max` / `Ultra`、float32 WAV、ノイズ除去、phase/bass repairでは一時ファイルも増えます。空き容量には余裕を持ってください。

## パフォーマンスの目安

処理速度は主に以下に依存します。

- CPU性能
- GPU/MPS/CUDAの有無
- メモリ容量
- 音源の長さ
- `Quality` 設定
- `Clean` 設定
- 他のLayerLabプロセスや音声処理プロセスの有無

Apple Silicon ではMPSを使えますが、複数プロセスで同時にDemucsを走らせると遅くなったり、止まって見える場合があります。共有ロックにより基本的には同時実行を避けます。

## よくあるトラブル

### `ffprobe` が見つからない

`ffprobe` がPATHにない場合でも、LayerLab は可能な範囲で `ffmpeg` fallback を使います。それでも失敗する場合は、FFmpeg/ffprobeをインストールするか、デスクトップ版の初回セットアップをやり直してください。

### `Audio processing failed. Please try again.`

原因候補:

- 音源ファイルが壊れている
- 対応外形式
- ディスク容量不足
- Demucsモデル取得失敗
- MPS/CUDA/CPUメモリ不足
- FFmpeg処理失敗

まず短いMP3/WAV/FLAC/M4Aで再テストしてください。ローカルWeb版ではサーバーログも確認できます。

### 82%付近から進まない

82%付近はDemucs分離後、stem収集と後処理へ入る境目です。

確認すること:

- 他のLayerLab.appや古い開発サーバーが起動していないか
- Demucsプロセスが複数残っていないか
- ディスク容量が不足していないか
- `jobs/` に途中ファイルだけ残っていないか

不要な古いプロセスを止め、アプリを再起動してください。新しいバージョンでは共有ロックにより同時Demucs実行を抑制します。

### プログレスバーが更新されない

ブラウザをリロードしてください。サーバーは `GET /api/jobs/active` とSSEで状態同期します。古い表示が残る場合、サーバー側にジョブが存在しない古いローカル保存データの可能性があります。

### ベースが途切れる

`High` / `Max` / `Ultra` では、bass dropout repair が有効になります。短い欠けを原音の低域残差から補います。ただし完全な復元ではなく、曲によっては原音の漏れや違和感が増える場合があります。

### 位相がおかしい

`High` / `Max` / `Ultra` では、stem合計と原音の差分を使ったphase repairが有効になります。原音再構成は改善しますが、stem単体の分離感とはトレードオフがあります。

### CPUとGPUで音は変わる？

基本的に品質を決める主因は `Quality` プリセットとモデル設定で、`Device` は主に処理速度とメモリ使用量に影響します。ただし CPU / MPS / CUDA では浮動小数点演算の順序や内部実装が異なるため、完全なバイナリ一致は保証されません。通常は聴感上ほぼ同じですが、厳密な再現性を優先する比較では `CPU` を固定し、速度を優先する通常利用では `Auto` またはGPUを使ってください。

### ノイズ除去で音が水っぽい

`Strong denoise` を使うとFFT系の副作用が出ることがあります。自然さを優先する場合は `Noise off` または `Light denoise` を選んでください。

### 処理が遅い

`Max` / `Ultra`、長尺音源、float32、denoise、phase/bass repairはすべて重い処理です。速度優先なら `Standard` と `Noise off`、品質とのバランスなら `High` を使ってください。`htdemucs_ft` は4モデルのアンサンブルなので、`High` / `Max` / `Ultra` の分離表示は `model 1/4 · shift 1/4`、`model 2/4 · shift 1/2` のように表示します。各shiftが100%になったあと次へ進むのは正常です。現在のUltraは `4モデル × 4シフト = 16回` の推論です。

旧Ultra相当の64回推論を比較検証したい場合だけ、起動前に次を指定します。通常利用には推奨しません。

```sh
STEMDECK_QUALITY_PRESET=ultra STEMDECK_DEMUCS_SHIFTS=16 ./run.sh start
```

## 設定変数

| 変数 | 用途 |
|---|---|
| `STEMDECK_QUALITY_PRESET` | 既定品質を指定します。`standard`、`high`、`max`、`ultra` |
| `STEMDECK_DEMUCS_DEVICE` | 起動時の既定デバイスを指定します。`cuda`、`mps`、`cpu` |
| `STEMDECK_PIPELINE_CONCURRENCY` | マシン全体で同時に走らせるDemucs分離数 |
| `STEMDECK_PIPELINE_LOCK` | 複数バックエンド間で共有するDemucsスロットのロックファイル |
| `STEMDECK_DEMUCS_JOBS` | 1曲内のDemucs CPUワーカー数。自動設定は曲並列との過剰実行を避けます |
| `STEMDECK_STEM_POST_LIMITER_PEAK` | Stem安定化とミックス書き出しのピーク上限。既定`0.98` |
| `STEMDECK_MIX_LIMITER_ATTACK_MS` | ミックスlimiterのlook-ahead attack。既定`5`ms |
| `STEMDECK_MIX_LIMITER_RELEASE_MS` | ミックスlimiterのrelease。既定`50`ms |
| `STEMDECK_MAX_PENDING_JOBS` | キュー受付上限 |
| `STEMDECK_MAX_DURATION_SEC` | 入力音源の最大長 |
| `STEMDECK_TIMEOUT_DEMUCS_STALL` | Demucsから進捗出力がない状態を許容する秒数 |
| `STEMDECK_TIMEOUT_DEMUCS_TOTAL` | Demucs総実行時間の上限。既定43200秒（12時間）、`0`で無効 |
| `STEMDECK_JOBS_DIR` | ジョブ保存先 |
| `STEMDECK_DATA_DIR` | portable modeのデータルート |
| `STEMDECK_FFMPEG` | ffmpeg実行ファイル |
| `STEMDECK_FFPROBE` | ffprobe実行ファイル |

設定の詳細は [README.md](README.md) の `Configuration` を参照してください。

## 推奨設定

通常利用:

```sh
STEMDECK_QUALITY_PRESET=standard
```

音質優先:

```sh
STEMDECK_QUALITY_PRESET=high
```

時間がかかっても最高精度優先:

```sh
STEMDECK_QUALITY_PRESET=max
```

さらに時間がかかっても最高品質を検証:

```sh
STEMDECK_QUALITY_PRESET=ultra
```

Apple Siliconでは通常 `STEMDECK_DEMUCS_DEVICE` を指定しなくてもMPSが自動検出されます。

## 公開・再配布時の注意

公開する場合は、配布ページとアプリ内説明で以下を明記してください。

- LayerLab は非公式の変更版 fork test build であること
- 元プロジェクトの公式リリースではないこと
- 元プロジェクトと提携・承認関係がないこと
- Apache License 2.0 に基づく fork であること
- Apache License 2.0 は商標権を自動許諾しないこと
- Apache License 2.0 は商用利用・有償配布自体を禁止していないが、ライセンス条件、帰属表示、第三者依存のライセンス、商標・公式誤認回避は別途守る必要があること

配布物には最低限以下を含めてください。

- `LICENSE`
- `NOTICE`
- `THIRD_PARTY_NOTICES.txt`
- `THIRD_PARTY_LICENSES.txt`
- `THIRD_PARTY_INVENTORY.json`

FFmpeg、PyTorch、Demucsコードとモデル重み、Tauri/Rust crate、Python runtime など、実際に同梱または初回取得する依存関係のライセンス・利用条件も最終配布物に合わせて確認してください。

### 商用利用する場合の確認

Apache License 2.0 がカバーするコードの範囲では、商用利用、社内利用、有償配布、改変版の配布は可能です。ただし、現在標準で取得するDemucs学習済み重みは、開発者によりMIT対象外かつ個人・研究用途の成果物と説明されています。商用利用では許諾取得、別モデルへの置換、または標準重みの無効化が必要です。以下のような形は避けてください。

- 元プロジェクトの公式版や公式販売物のように見せる
- 元プロジェクトから承認、提携、認定、サポートを受けているように見せる
- `LICENSE`、`NOTICE`、`THIRD_PARTY_NOTICES.txt`、`THIRD_PARTY_LICENSES.txt`、`THIRD_PARTY_INVENTORY.json` を外して配布する
- `StemDeck` / `STEMDECK` の名称やロゴを、出所説明を超えて独自商品の商標のように使う
- FFmpeg、PyTorch、Demucs、yt-dlp などの同梱物のライセンス確認をせずに販売・再配布する
- ユーザーが権利を持たない音源を処理できるサービスとして、著作権や利用規約の整理なしに公開する

公開販売や法人向け提供をする場合は、最終的に配布するバイナリ、同梱runtime、モデル、FFmpeg build、更新方式を前提に、法律・ライセンスの専門家へ確認することを推奨します。

## 品質評価ベンチマーク

`scripts/benchmark_audio.py` を使うと、左右チャンネルを保持したstem合計が原音にどれくらい近いかをJSONで確認できます。品質プリセット、denoise、phase repair、bass repairを比較する時の基準として使ってください。

```sh
uv run python scripts/benchmark_audio.py \
  --source /path/to/original.wav \
  --stems-dir jobs/<job-id>/stems \
  --metadata jobs/<job-id>/metadata.json \
  --out .build/benchmarks/<job-id>.json
```

`source.*` がまだ残っているジョブなら、以下だけでも測定できます。

```sh
uv run python scripts/benchmark_audio.py --job-dir jobs/<job-id>
```

正解コードを `開始秒 終了秒 C:maj` 形式の `.lab` で用意できる場合は、
MITライセンスの`mir_eval`でWCSRを測定できます。

```sh
uv run python scripts/benchmark_audio.py \
  --job-dir jobs/<job-id> \
  --reference-chords /path/to/reference.lab
```

主に見る値:

- `residual_percent`: stem合計と原音の残差。小さいほど原音再構成に近い。
- `correlation`: 原音とstem合計の相関。1に近いほど近い。
- `stem_sum_clipping_percent`: stem合計で1.0を超えたサンプル割合。大きい場合は合成時のクリップに注意。
- `chords.segment_count` / `chords.average_confidence`: コードMIDI生成の区間数と平均信頼度。
- `chord_reference.*_wcsr`: 正解ラベルに対する時間重み付きコード一致率。1に近いほど良い。

注意: `residual_percent` が小さいほど常に「stem単体が良い」とは限りません。phase repairを強くすると原音再構成は改善しても、stem間の分離感や漏れとはトレードオフになる場合があります。

## 困った時の確認コマンド

ローカルWeb版:

```sh
./run.sh status
curl -s http://127.0.0.1:8765/api/health
curl -s http://127.0.0.1:8765/api/jobs/active
```

実行中プロセス:

```sh
ps -axo pid,ppid,stat,etime,pcpu,pmem,command | rg -i "demucs|ffmpeg|uvicorn|stemdeck"
```

テスト:

```sh
uv run --extra dev ruff check .
uv run --extra dev pytest
```

## 変更履歴メモ

このマニュアルは、LayerLab の以下の拡張を前提にしています。

- Neon UI
- レスポンシブレイアウト
- バックグラウンドキュー処理
- 経過時間/ETA表示
- 曲ごとの処理時間記録
- 高品質/最高品質プリセット
- bass dropout repair
- phase repair
- stem denoise
- 品質評価ベンチマーク
- cross-process Demucs lock
- portable FFmpeg fallback
