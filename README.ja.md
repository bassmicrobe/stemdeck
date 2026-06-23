# LayerLab 日本語 README

LayerLab は、音源をローカル環境で stem 分離するためのデスクトップ/ローカルWebアプリです。MP3、WAV、FLAC、M4A、または YouTube URL を入力し、ボーカル、ドラム、ベース、ギター、ピアノ、その他などの stem に分離します。処理は基本的にユーザーのマシン上で完結し、音源をクラウドへアップロードしない設計です。

このリポジトリは、元の StemDeck プロジェクトをベースにした非公式 fork test build です。元プロジェクトと本 fork は Apache License 2.0 のもとで配布されます。再配布時は `LICENSE` と `NOTICE` を同梱し、元プロジェクトの表示と本 fork の変更点を保持してください。本 fork は変更版であり、元プロジェクトの公式リリースではありません。元プロジェクトとは提携しておらず、元プロジェクトによる承認や推奨を受けたものでもありません。

## 元プロジェクト

- 元プロジェクト: [stemdeckapp/stemdeck](https://github.com/stemdeckapp/stemdeck)
- ライセンス: Apache License 2.0
- 本リポジトリ内のライセンス表記: [LICENSE](LICENSE), [NOTICE](NOTICE)
- 配布物に含める表記: `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.txt`

Apache License 2.0 では、再配布時にライセンス本文を渡すこと、変更したファイルに変更があることを示すこと、NOTICE がある場合はその表示を保持することが求められます。本 fork では `NOTICE` に元プロジェクトへの帰属と変更概要を記載しています。

Apache License 2.0 は商標権の利用許諾を自動的に与えるものではありません。公開配布する場合は、説明文や配布ページで「元プロジェクトをベースにした変更版」であることを明示し、元プロジェクトの公式配布物と誤認されないようにしてください。

## 商用利用・有償配布について

Apache License 2.0 は、ソフトウェアの商用利用、有償配布、社内利用、改変版の配布を禁止していません。したがって、`LayerLab` を商用プロジェクトで使うことや、有償サポート、インストーラー配布、業務利用に組み込むこと自体はライセンス上ただちに禁止されるものではありません。

ただし、以下は守ってください。

- `LICENSE`、`NOTICE`、`THIRD_PARTY_NOTICES.txt` を配布物に含める。
- 元の StemDeck と本 fork の帰属表示、変更版であること、非公式であることを隠さない。
- 元プロジェクトの公式版、公式販売、公式認定、公式サポートのように見せない。
- `StemDeck` / `STEMDECK` などの名称・ロゴ・商標的表示は、出所説明に必要な範囲を超えて使わない。独自サービス名や独自ブランドで配布する場合も、説明文では「StemDeckをベースにした非公式変更版」と明記する。
- 同梱する FFmpeg、PyTorch、Demucs、yt-dlp、Python runtime、Tauri/Rust crate など第三者依存のライセンスを、実際の配布物に合わせて確認する。
- 音源処理サービスとして提供する場合は、処理対象音源の著作権、配信サイトの利用規約、ユーザーアップロード物の扱いを別途確認する。

つまり「勝手に商用利用してはいけない」というより、**Apache-2.0 の条件、NOTICE/帰属表示、商標・公式誤認の回避、第三者依存のライセンス確認を満たせば商用利用は可能**という整理です。本READMEは法的助言ではないため、公開販売や法人サービス化の前には実際の配布物を前提に専門家へ確認することを推奨します。

## この fork で加えた主な変更

- アプリ名と表示を `LayerLab` として整理し、非公式 StemDeck fork test build であることを明示。
- Neon 系のUI、スマホ向けレスポンシブ調整、進捗バー視認性改善。
- ジョブキュー、キャンセル、進捗率、残り推定時間表示を強化。
- 画面内の `Logs` から、ジョブ単位または現在セッション全体の処理段階、進捗、警告、失敗理由を確認できる診断ビューを追加。
- 複数曲キュー中も、完了済みの選択曲を前面に残して試聴やダウンロードを続けられるバックグラウンド抽出に対応。
- 同じ曲を `Quality` / `Device` / `Clean` / 選択stem の組み合わせごとに別プロファイルとして複数回抽出できるように対応。画面表示、ダウンロードファイル名、stem ZIP内の `LAYERLAB_PROFILE.txt` で設定を確認可能。
- クライアントマシンのCPU/GPU/メモリ状況に応じて、重い解析・stem分離パイプラインの同時実行数を自動判定。
- ジョブごとに `Auto` / `CPU` / `Apple GPU(MPS)` / `NVIDIA CUDA` の処理デバイスを選択可能。
- `ffprobe` がない環境でも `ffmpeg` fallback で duration を読めるよう改善。
- `imageio-ffmpeg` を使った portable FFmpeg fallback を追加。
- BPM、Tempo Stability、拍グリッド検出を追加。波形上に検出拍を表示し、曲ごとのメタデータとして保存。
- piano/guitar stem を優先したchroma解析、bass root別解析、4分音符グリッド上の拍ごとの再推定からコード進行を推定生成し、弱い1拍誤検出を抑制したうえでExportメニューから `*_chords.mid` / `*_chords.csv` として書き出せるように追加。書き出し時に `Auto` / `Triads only` / `Allow 7ths`、`1/4 beat grid` / `1 bar blocks`、MIDI marker有無を選択可能。
- 高精度プリセット `High` / `Max` / `Ultra` を追加し、`htdemucs_ft`、shift average、overlap、float32 出力を利用。
- 音圧が高い音源向けに前処理、ゲイン復元、float32 維持、クリップ抑制を強化。
- ベース欠け補正、stem 合計と原音の位相/残差補正を追加。
- 分離後の各 stem に任意のノイズ除去 `Noise off` / `Light denoise` / `Strong denoise` を追加。
- 評価用ベンチマーク `scripts/benchmark_audio.py` を追加し、stem合計と原音の残差、クリップリスク、コードMIDIメタデータをJSONで比較できるように追加。`--jobs-root` と `--baseline` で複数ジョブの回帰比較にも対応。
- 実 ffmpeg による WAV 合成/置き換えテストとパイプラインテストを追加。
- Tauri/Rust 側に runtime setup、FFmpeg取得、GPU検出、backend起動、保守/掃除、軽量WAV解析を実装。
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

品質プリセット、ノイズ除去、phase/bass repair の比較にはベンチマークスクリプトを使えます。stem WAVを合計し、原音との差分、相関、クリップリスク、コード進行メタデータをJSONで出力します。

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

デスクトップ版は薄い Tauri アプリとして起動し、初回セットアップでローカル処理に必要な runtime を用意します。LayerLab は upstream 版と衝突しにくいよう、既定のアプリデータと書き出し先を `LayerLab` 系のフォルダに分離します。

- Python runtime を確認またはダウンロード。
- workspace と data folder を作成。
- FFmpeg / ffprobe を確認またはダウンロード。
- Apple Silicon MPS または NVIDIA CUDA を検出し、必要ならGPU向け設定を行う。
- 同時実行数は既定で自動判定される。MPS/CUDA ではメモリ安全性を優先して通常1本、CPUのみで十分なコアとメモリがある環境では2本まで並列実行する。必要なら `STEMDECK_PIPELINE_CONCURRENCY=1` から `4` で上書き可能。
- 複数のローカルLayerLabバックエンドが同時に起動しても、`STEMDECK_PIPELINE_LOCK` を基準に自動判定した同時実行数ぶんの共有スロットを使い、マシン全体で過剰な Demucs 同時実行を防ぐ。
- Demucs model は初回分離時にキャッシュされる。
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

更新時も `LICENSE`、`NOTICE`、`THIRD_PARTY_NOTICES.txt` を配布物に含めてください。第三者ライブラリや FFmpeg build のライセンスは、最終的に配布する実バイナリに合わせて確認してください。

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

本READMEは法的助言ではありません。配布前には、実際に同梱する Python runtime、PyTorch、Demucs、FFmpeg build、Tauri/Rust crate、その他依存関係のライセンスを確認してください。

本 fork で守るべき最低限の方針:

- 元プロジェクトの `LICENSE` と `NOTICE` を削除しない。
- 変更点を `NOTICE`、README、Git履歴で追えるようにする。
- 配布物のルートまたはアプリリソースに `LICENSE`、`NOTICE`、`THIRD_PARTY_NOTICES.txt` を含める。
- 公開配布時は、元プロジェクトの公式版ではなく `LayerLab` という変更版 fork test build であることを明示する。
- 元プロジェクトとの提携、承認、推奨を示唆しない。
- FFmpeg は build により LGPL/GPL 条件が変わるため、使用する配布元とライセンスを明記する。
- `THIRD_PARTY_NOTICES.txt` は最終成果物の依存関係に合わせて更新する。

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
