# LayerLab 日本語 README

LayerLab は、音源をローカル環境で stem 分離するためのデスクトップ/ローカルWebアプリです。MP3、WAV、FLAC、M4A、または YouTube URL を入力し、ボーカル、ドラム、ベース、ギター、ピアノ、その他などの stem に分離します。処理は基本的にユーザーのマシン上で完結し、音源をクラウドへアップロードしない設計です。

このリポジトリは、元の StemDeck プロジェクトをベースにした非公式 fork test build です。元プロジェクトと本 fork は Apache License 2.0 のもとで配布されます。再配布時は `LICENSE` と `NOTICE` を同梱し、元プロジェクトの表示と本 fork の変更点を保持してください。本 fork は変更版であり、元プロジェクトの公式リリースではありません。元プロジェクトとは提携しておらず、元プロジェクトによる承認や推奨を受けたものでもありません。

## 元プロジェクト

- 元プロジェクト: [stemdeckapp/stemdeck](https://github.com/stemdeckapp/stemdeck)
- ライセンス: Apache License 2.0
- 本リポジトリ内のライセンス表記: [LICENSE](LICENSE), [NOTICE](NOTICE)
- 配布物に含める表記: `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.txt`, `THIRD_PARTY_LICENSES.txt`, `THIRD_PARTY_INVENTORY.json`

Apache License 2.0 では、再配布時にライセンス本文を渡すこと、変更したファイルに変更があることを示すこと、NOTICE がある場合はその表示を保持することが求められます。本 fork では `NOTICE` に元プロジェクトへの帰属と変更概要を記載しています。

Apache License 2.0 は商標権の利用許諾を自動的に与えるものではありません。公開配布する場合は、説明文や配布ページで「元プロジェクトをベースにした変更版」であることを明示し、元プロジェクトの公式配布物と誤認されないようにしてください。

## 商用利用・有償配布について

Apache License 2.0 は、ソフトウェアの商用利用、有償配布、社内利用、改変版の配布を禁止していません。したがって、`LayerLab` を商用プロジェクトで使うことや、有償サポート、インストーラー配布、業務利用に組み込むこと自体はライセンス上ただちに禁止されるものではありません。

ただし、以下は守ってください。

- `LICENSE`、`NOTICE`、`THIRD_PARTY_NOTICES.txt`、`THIRD_PARTY_LICENSES.txt`、`THIRD_PARTY_INVENTORY.json` を配布物に含める。
- 元の StemDeck と本 fork の帰属表示、変更版であること、非公式であることを隠さない。
- 元プロジェクトの公式版、公式販売、公式認定、公式サポートのように見せない。
- `StemDeck` / `STEMDECK` などの名称・ロゴ・商標的表示は、出所説明に必要な範囲を超えて使わない。独自サービス名や独自ブランドで配布する場合も、説明文では「StemDeckをベースにした非公式変更版」と明記する。
- 同梱する FFmpeg、PyTorch、Demucs、yt-dlp、Python runtime、Tauri/Rust crate など第三者依存のライセンスを、実際の配布物に合わせて確認する。
- 音源処理サービスとして提供する場合は、処理対象音源の著作権、配信サイトの利用規約、ユーザーアップロード物の扱いを別途確認する。

ただし、これは Apache-2.0 がカバーするソースコードについての説明です。別途取得するモデル重み、FFmpegバイナリ、フォント、画像、商標、ユーザーが処理する音源まで自動的に商用利用可能になるわけではありません。

特に Demucs は、本体コードが MIT である一方、公式リポジトリの開発者回答では、公開済み学習済み重みは MIT の対象外で、学習データの制約から個人・研究用途の成果物と説明されています。LayerLab は重みをインストーラーへ同梱せず初回分離時に取得しますが、この構成によって商用利用権が追加されるわけではありません。商用販売・法人サービス・有償Webサービスで使う場合は、重みの権利許諾を得る、商用利用条件が明確な別モデルへ置き換える、または標準重みの自動利用を無効化してください。

したがって、**LayerLab/StemDeck由来コード自体は条件を守れば商用利用可能ですが、現在の標準Demucs重みを含む処理系全体を無条件に商用利用可能とは案内できません**。本READMEは法的助言ではないため、公開販売や法人サービス化の前には実際の配布物・取得モデル・更新方式を前提に専門家へ確認することを推奨します。

## この fork で加えた主な変更

- アプリ名と表示を `LayerLab` として整理し、非公式 StemDeck fork test build であることを明示。
- Neon 系のUI、スマホ向けレスポンシブ調整、進捗バー視認性改善。
- ジョブキュー、キャンセル、進捗率、残り推定時間表示を強化。
- 画面内の `Logs` から、ジョブ単位または現在セッション全体の処理段階、進捗、警告、失敗理由を確認できる診断ビューを追加。
- 複数曲キュー中も、完了済みの選択曲を前面に残して試聴やダウンロードを続けられるバックグラウンド抽出に対応。
- 同じ曲を `Quality` / `Device` / `Clean` / 選択stem の組み合わせごとに別プロファイルとして複数回抽出できるように対応。画面表示、ダウンロードファイル名、stem ZIP内の `LAYERLAB_PROFILE.txt` で設定を確認可能。
- クライアントマシンのCPU/GPU/メモリ状況に応じてDemucs同時実行数を自動判定。GPU分離だけを共有ロックし、別ジョブの取得・解析・後処理は並行できるよう改善。
- Demucsをモデル/デバイス単位の常駐ワーカーにし、同じ設定の次曲ではモデルを再ロードせず再利用。MPS/CUDAは1プロセス、CPUは自動同時実行数までに制限し、異なるモデルへ切り替える時もアイドルワーカーを退避してメモリ増加を抑制。
- ジョブごとに `Auto` / `CPU` / `Apple GPU(MPS)` / `NVIDIA CUDA` の処理デバイスを選択可能。
- `ffprobe` がない環境でも `ffmpeg` fallback で duration を読めるよう改善。
- ローカル開発時は `imageio-ffmpeg` をFFmpeg探索fallbackとして利用。公開デスクトップruntimeではwheel内のGPL版実行ファイルを除外。
- BPM、Tempo Stability、拍グリッド検出を追加。波形上に検出拍を表示し、曲ごとのメタデータとして保存。
- 全品質で MIT ライセンスの `Beat This! final0` を使って拍とダウンビートをニューラル検出。モデル未取得、オフライン、推論失敗時は既存の librosa 解析へ自動フォールバック。
- piano/guitar stem優先chroma、bass root別解析、音源ごとのチューニング補正、単音リフの信頼度抑制、4分音符ごとの再推定からコード進行を生成。孤立した欠落拍を補間し、MIDIには音源先頭オフセットと拍ごとのテンポマップを埋め込むため、可変テンポ曲でもDAWの拍位置を維持。
- コード解析は同一CQTをCQT/CENS特徴で共有し、独立したstem特徴を最大2本まで並列計算。音響結果を変えず長尺曲のMIDI生成時間を短縮。
- BSD-3-Clause ライセンスの `music21` で生成MIDIを検証し、推定キー、音域、長さ、ローマ数字コード進行を `midi-analysis.json` に保存。
- 高精度プリセット `High` / `Max` / `Ultra` を追加し、`htdemucs_ft`、shift average、明示的なoverlap、float32 出力を利用。Standardは`overlap=0.20`、Highは`0.20`、Max/Ultraは`0.25`。
- Demucs自身の平均/標準偏差正規化を利用し、音圧の高い音源でも不要な正規化WAVを作らず、float32維持と出力クリップ抑制を強化。
- ベース欠け補正、stem 合計と原音の位相/残差補正を追加。
- 分離後の各 stem に任意のノイズ除去 `Noise off` / `Light denoise` / `Strong denoise` を追加。
- 評価用ベンチマーク `scripts/benchmark_audio.py` を追加し、stem合計残差、クリップリスク、コードMIDIメタデータをJSONで比較可能。正解`.lab`を渡すとMITの`mir_eval`でroot/maj-min/triad/tetrad WCSRも測定。
- YouTube入力はメタデータ取得を1回に集約し、長さ超過をダウンロード前に拒否。ローカル/YouTubeともDemucsが直接読める入力は再変換せず、互換性変換が必要な場合も解析と分離で1つのWAVを共有。
- ジョブごとに複数のffmpeg子プロセスを追跡し、並列解析中のキャンセルでも全プロセスを停止。
- ミックス書き出しはmake-up gainなしのlook-ahead limiterでピークだけを抑制。WAVは一時ファイルでヘッダーを確定してから返し、プレイヤー上で異常に長い再生時間になる問題を防止。
- 実 ffmpeg による WAV 合成/置き換えテストとパイプラインテストを追加。
- Tauri/Rust 側に runtime setup、FFmpeg取得、GPU検出、backend起動、保守/掃除を実装。Apache-2.0のHoundを使う`layerlab-pcm` sidecarへ、WAV/PCMの無音ゲート、DC/ピーク安定化、RMS、波形ピーク生成を移行し、利用不可時はPythonへ自動fallback。
- macOS/Windows 配布向けに署名、Notarize、容量、ライセンス表記の確認導線を追加。

## 使い方

詳しい操作方法は [MANUAL.ja.md](MANUAL.ja.md) にまとめています。

### ローカルWebとして起動

```sh
./run.sh setup
./run.sh start
```

起動後、ブラウザで `http://127.0.0.1:8765/` を開きます。`ffmpeg` がPATHにない場合でも、Python側で `imageio-ffmpeg` fallback を使えるようにしています。

### 品質評価ベンチマーク

品質プリセット、ノイズ除去、phase/bass repair の比較にはベンチマークスクリプトを使えます。左右チャンネルを保持してstem WAVを合計し、原音との差分、相関、実サンプルのクリップリスク、コード進行メタデータをJSONで出力します。

```sh
uv run python scripts/benchmark_audio.py \
  --source /path/to/original.wav \
  --stems-dir jobs/<job-id>/stems \
  --metadata jobs/<job-id>/metadata.json \
  --out .build/benchmarks/<job-id>.json
```

`source.*` がまだ残っているジョブなら、以下だけでも実行できます。

```sh
uv run python scripts/benchmark_audio.py --job-dir jobs/<job-id>
```

正解コードを `開始秒 終了秒 C:maj` 形式の `.lab` で用意できる場合は、コード認識をWCSRで評価できます。

```sh
uv run python scripts/benchmark_audio.py \
  --job-dir jobs/<job-id> \
  --reference-chords /path/to/reference.lab
```

完了済みジョブでは容量節約のため原音が削除されている場合があります。その場合、stem合計誤差まで測るには `--source` で元音源を指定してください。

### macOSデスクトップアプリ

Apple Silicon の場合:

```sh
rustup target add aarch64-apple-darwin
ARCH=arm64 scripts/macos/make-runtime-pack.sh
ARCH=arm64 scripts/macos/make-app.sh
ARCH=arm64 scripts/macos/make-dmg.sh
```

ビルド後の `.app` は `desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/LayerLab.app` に生成されます。DMG は `.build/macos-dist/LayerLab-macOS-arm64.dmg` に生成されます。

### Windows portable

Windows 環境で:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/make-portable.ps1 `
  -PackageName LayerLab-Windows-x64.NVIDIA `
  -StripVenv
```

CPU版は `-CpuOnly` とCPU用の `PackageName` を付けます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/make-portable.ps1 `
  -PackageName LayerLab-Windows-x64 `
  -CpuOnly `
  -StripVenv
```

### Windows installer

Windows 用の `.exe` インストーラーは、完成した portable フォルダを Inno Setup 6 で包む方式です。Tauri単体の `msi` / `nsis` では、現状の Python runtime と backend 一式をそのまま含められないため、このリポジトリでは portable 生成後に installer 化します。

```powershell
# NVIDIA/CUDA版 portable + installer
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/make-installer.ps1 `
  -PackageName LayerLab-Windows-x64.NVIDIA `
  -StripVenv

# CPU版 portable + installer
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/make-installer.ps1 `
  -PackageName LayerLab-Windows-x64 `
  -CpuOnly `
  -StripVenv
```

生成物は `dist/LayerLab-Windows-x64.NVIDIA-Setup.exe` または `dist/LayerLab-Windows-x64-Setup.exe` です。インストール先は管理者権限なしで書き込みできる `%LocalAppData%\Programs\LayerLab` にしています。Start Menu ショートカット、任意のDesktopショートカット、アンインストーラーが作成されます。

既に portable ZIP を作成済みの場合は、再ビルドせずに包めます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/make-installer.ps1 `
  -PackageName LayerLab-Windows-x64.NVIDIA `
  -SkipPortableBuild
```

## 初回セットアップ導線

デスクトップ版は Tauri アプリとして起動し、初回セットアップでローカル処理に必要な runtime を用意します。macOS の自己完結DMGには Python runtime archive を同梱し、GitHub Releaseから取得せずローカルへ検証・展開します。LayerLab は upstream 版と衝突しにくいよう、既定のアプリデータと書き出し先を `LayerLab` 系のフォルダに分離します。

- 同梱Python runtimeをSHA-256検証して展開。軽量オンラインビルドではダウンロードへフォールバック。
- workspace と data folder を作成。
- FFmpeg / ffprobe を確認またはダウンロード。
- Apple Silicon MPS または NVIDIA CUDA を検出し、必要ならGPU向け設定を行う。
- 同時実行数は既定で自動判定される。MPS/CUDA ではメモリ安全性を優先して通常1本、CPUのみで十分なコアとメモリがある環境では2本まで並列実行する。必要なら `STEMDECK_PIPELINE_CONCURRENCY=1` から `4` で上書き可能。
- 複数のローカルLayerLabバックエンドが同時に起動しても、`STEMDECK_PIPELINE_LOCK` を基準に共有スロットを使い、マシン全体で過剰なDemucs同時実行だけを防ぐ。取得・解析・後処理・完了曲の試聴はブロックしない。
- Demucs model は初回分離時にキャッシュされる。
- backend起動中は読み込んだDemucsモデルも常駐ワーカー内で再利用される。アプリ再起動、キャンセル、モデル切替、ワーカー異常終了後の最初の曲では再ロードが必要。
- Rust PCM sidecarはruntimeの`bin`へ同梱される。これは後処理の速度、float32保持、I/O安定性を改善するもので、Demucs推論モデル自体の分離精度を変更するものではない。
- セットアップ画面に runtime download サイズ、jobs/cache 使用量、data folder、ライセンス表記の案内を表示する。

初回セットアップにはインターネット接続と数GB以上の空き容量が必要です。長尺音源や `High` / `Max` / `Ultra` の float32 出力では、stem WAV と一時ファイルでさらに容量を使います。

## 署名とNotarize

認証情報はリポジトリに保存しません。必要な環境変数を渡した場合だけ署名処理が走ります。

### macOS app署名

```sh
APPLE_SIGNING_IDENTITY="Developer ID Application: Example (TEAMID)" \
ARCH=arm64 scripts/macos/make-app.sh
```

### macOS DMG署名とNotarize

```sh
APPLE_SIGNING_IDENTITY="Developer ID Application: Example (TEAMID)" \
APPLE_NOTARIZE=1 \
APPLE_NOTARY_KEYCHAIN_PROFILE="layerlab-notary" \
ARCH=arm64 scripts/macos/make-dmg.sh
```

`APPLE_NOTARY_KEYCHAIN_PROFILE` を使わない場合は、`APPLE_ID`、`APPLE_TEAM_ID`、`APPLE_APP_SPECIFIC_PASSWORD` を指定します。公開配布する macOS 版は Developer ID 署名と Notarize を推奨します。

### Windows Authenticode署名

```powershell
$env:WINDOWS_SIGN_CERT_PATH = "C:\certs\layerlab.pfx"
$env:WINDOWS_SIGN_CERT_PASSWORD = "..."
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/make-installer.ps1 `
  -PackageName LayerLab-Windows-x64.NVIDIA `
  -StripVenv
```

署名証明書がない場合は無署名ZIP/インストーラーとして生成されます。公開配布では Authenticode 署名を推奨します。`make-installer.ps1` は同じ証明書設定で `LayerLab.exe` と installer 本体の両方に署名します。

## 更新

アプリ本体は fork 側の GitHub Releases の最新リリースを確認し、利用中のバージョンと違う場合は通知を表示します。デスクトップ版では、DMG更新後に runtime manifest のバージョンが変わっていれば初回セットアップ画面で runtime を更新します。

公開時は `unofficial fork test build` として GitHub Pre-release にする方針です。Release本文の冒頭で、元プロジェクトの公式版ではないこと、元プロジェクトと提携・承認関係がないこと、Apache License 2.0 に基づく fork であることを明記してください。

更新時も `LICENSE`、`NOTICE`、`THIRD_PARTY_NOTICES.txt`、`THIRD_PARTY_LICENSES.txt`、`THIRD_PARTY_INVENTORY.json` を配布物に含めてください。リリースビルドは実際のPython runtimeと対象OSのRust依存グラフからこれらを再生成し、ライセンス宣言または本文が欠ける依存がある場合は失敗します。

音楽解析OSSの採用・見送り理由とライセンス確認結果は
[OSS_COMPONENTS.md](OSS_COMPONENTS.md) にまとめています。

現在のmacOS arm64実ランタイムから生成した監査結果:

- [第三者コンポーネント一覧](packaging/generated/macos-arm64/THIRD_PARTY_NOTICES.md)
- [機械可読インベントリ](packaging/generated/macos-arm64/THIRD_PARTY_INVENTORY.json)
- [ライセンス/NOTICE全文](packaging/generated/macos-arm64/THIRD_PARTY_LICENSES.txt)

## 容量の目安

- macOS runtime pack: 数百MB規模。
- Windows CPU portable: 約700MB目安。
- Windows NVIDIA portable: CUDA/PyTorch を含むため約1.6GB目安。
- Demucs model cache: 初回利用時に数百MB規模。
- 10分の stereo 16-bit WAV: 約101MiB。
- 10分の stereo float32 WAV: 約202MiB。
- `High` / `Max` / `Ultra` では 4-stem float32 出力が中心になり、10分曲で stem だけでも約808MiB程度になります。
- `Noise denoise` や phase/bass repair では一時WAVも作るため、長尺では数GBの作業領域を見てください。

## ライセンス上の注意

本READMEは法的助言ではありません。配布前には、実際に同梱する Python runtime、PyTorch、Demucsコード、初回取得するDemucs重み、FFmpeg build、Tauri/Rust crate、フォント、画像、その他依存関係のライセンス・利用条件を確認してください。

本 fork で守るべき最低限の方針:

- 元プロジェクトの `LICENSE` と `NOTICE` を削除しない。
- 変更点を `NOTICE`、README、Git履歴で追えるようにする。
- 配布物のルートまたはアプリリソースに `LICENSE`、`NOTICE`、`THIRD_PARTY_NOTICES.txt`、`THIRD_PARTY_LICENSES.txt`、`THIRD_PARTY_INVENTORY.json` を含める。
- 公開配布時は、元プロジェクトの公式版ではなく `LayerLab` という変更版 fork test build であることを明示する。
- 元プロジェクトとの提携、承認、推奨を示唆しない。
- packaged runtimeからは `imageio-ffmpeg` wheel内のGPL版FFmpeg実行ファイルを除外する。デスクトップ版が初回取得するmacOS既定buildは evermeet.cx の `8.1.1-tessus` GPL-3.0-or-later buildとして明記する。
- FFmpeg実行ファイルをミラーまたは同梱する場合は、その正確なbuildに対応するソース、ビルド構成、外部ライブラリの条件を満たす。
- DemucsコードのMITと、個人・研究用途と説明されている学習済み重みを混同しない。
- `THIRD_PARTY_NOTICES.txt`、全文、JSONインベントリは最終成果物の依存関係から自動生成する。

## 確認コマンド

```sh
uv run --extra dev ruff check .
uv run --extra dev pytest
scripts/check-distribution-notices.sh
```

macOS 署名確認例:

```sh
codesign --verify --deep --strict --verbose=2 "path/to/LayerLab.app"
spctl -a -vv -t open path/to/LayerLab-macOS-arm64.dmg
```

Windows 署名確認例:

```powershell
signtool verify /pa /v "LayerLab.exe"
```
