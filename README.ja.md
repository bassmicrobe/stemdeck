# STEMDECK Enhanced 日本語 README

STEMDECK Enhanced は、音源をローカル環境で stem 分離するためのデスクトップ/ローカルWebアプリです。MP3、WAV、FLAC、または YouTube URL を入力し、ボーカル、ドラム、ベース、ギター、ピアノ、その他などの stem に分離します。処理は基本的にユーザーのマシン上で完結し、音源をクラウドへアップロードしない設計です。

このリポジトリは、元の StemDeck プロジェクトをベースにした非公式 fork test build です。元プロジェクトと本 fork は Apache License 2.0 のもとで配布されます。再配布時は `LICENSE` と `NOTICE` を同梱し、元プロジェクトの表示と本 fork の変更点を保持してください。本 fork は変更版であり、元プロジェクトの公式リリースではありません。元プロジェクトとは提携しておらず、元プロジェクトによる承認や推奨を受けたものでもありません。

## 元プロジェクト

- 元プロジェクト: [stemdeckapp/stemdeck](https://github.com/stemdeckapp/stemdeck)
- ライセンス: Apache License 2.0
- 本リポジトリ内のライセンス表記: [LICENSE](LICENSE), [NOTICE](NOTICE)
- 配布物に含める表記: `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.txt`

Apache License 2.0 では、再配布時にライセンス本文を渡すこと、変更したファイルに変更があることを示すこと、NOTICE がある場合はその表示を保持することが求められます。本 fork では `NOTICE` に元プロジェクトへの帰属と変更概要を記載しています。

Apache License 2.0 は商標権の利用許諾を自動的に与えるものではありません。公開配布する場合は、説明文や配布ページで「元プロジェクトをベースにした変更版」であることを明示し、元プロジェクトの公式配布物と誤認されないようにしてください。

## この fork で加えた主な変更

- アプリ名と表示を `STEMDECK Enhanced` として整理し、非公式 fork test build であることを明示。
- Neon 系のUI、スマホ向けレスポンシブ調整、進捗バー視認性改善。
- ジョブキュー、キャンセル、進捗率、残り推定時間表示を強化。
- `ffprobe` がない環境でも `ffmpeg` fallback で duration を読めるよう改善。
- `imageio-ffmpeg` を使った portable FFmpeg fallback を追加。
- 高精度プリセット `High` / `Max` を追加し、`htdemucs_ft`、shift average、float32 出力を利用。
- 音圧が高い音源向けに前処理、ゲイン復元、float32 維持、クリップ抑制を強化。
- ベース欠け補正、stem 合計と原音の位相/残差補正を追加。
- 分離後の各 stem に任意のノイズ除去 `Noise off` / `Light denoise` / `Strong denoise` を追加。
- 実 ffmpeg による WAV 合成/置き換えテストとパイプラインテストを追加。
- Tauri/Rust 側に runtime setup、FFmpeg取得、GPU検出、backend起動、保守/掃除、軽量WAV解析を実装。
- macOS/Windows 配布向けに署名、Notarize、容量、ライセンス表記の確認導線を追加。

## 使い方

### ローカルWebとして起動

```sh
./run.sh setup
./run.sh start
```

起動後、ブラウザで `http://127.0.0.1:8765/` を開きます。`ffmpeg` がPATHにない場合でも、Python側で `imageio-ffmpeg` fallback を使えるようにしています。

### macOSデスクトップアプリ

Apple Silicon の場合:

```sh
rustup target add aarch64-apple-darwin
ARCH=arm64 scripts/macos/make-runtime-pack.sh
ARCH=arm64 scripts/macos/make-app.sh
ARCH=arm64 scripts/macos/make-dmg.sh
```

ビルド後の `.app` は `desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/STEMDECK Enhanced.app` に生成されます。DMG は `.build/macos-dist/STEMDECK-Enhanced-macOS-arm64.dmg` に生成されます。

### Windows portable

Windows 環境で:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/make-portable.ps1 -StripVenv
```

CPU版は `-CpuOnly` を付けます。

## 初回セットアップ導線

デスクトップ版は薄い Tauri アプリとして起動し、初回セットアップでローカル処理に必要な runtime を用意します。STEMDECK Enhanced は upstream 版と衝突しにくいよう、既定のアプリデータと書き出し先を `StemDeck Enhanced` 系のフォルダに分離します。

- Python runtime を確認またはダウンロード。
- workspace と data folder を作成。
- FFmpeg / ffprobe を確認またはダウンロード。
- Apple Silicon MPS または NVIDIA CUDA を検出し、必要ならGPU向け設定を行う。
- Demucs model は初回分離時にキャッシュされる。
- セットアップ画面に runtime download サイズ、jobs/cache 使用量、data folder、ライセンス表記の案内を表示する。

初回セットアップにはインターネット接続と数GB以上の空き容量が必要です。長尺音源や `High` / `Max` の float32 出力では、stem WAV と一時ファイルでさらに容量を使います。

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
APPLE_NOTARY_KEYCHAIN_PROFILE="stemdeck-notary" \
ARCH=arm64 scripts/macos/make-dmg.sh
```

`APPLE_NOTARY_KEYCHAIN_PROFILE` を使わない場合は、`APPLE_ID`、`APPLE_TEAM_ID`、`APPLE_APP_SPECIFIC_PASSWORD` を指定します。公開配布する macOS 版は Developer ID 署名と Notarize を推奨します。

### Windows Authenticode署名

```powershell
$env:WINDOWS_SIGN_CERT_PATH = "C:\certs\stemdeck.pfx"
$env:WINDOWS_SIGN_CERT_PASSWORD = "..."
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/make-portable.ps1 -StripVenv
```

署名証明書がない場合は無署名ZIPとして生成されます。公開配布では Authenticode 署名を推奨します。

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
- `High` / `Max` では 4-stem float32 出力が中心になり、10分曲で stem だけでも約808MiB程度になります。
- `Noise denoise` や phase/bass repair では一時WAVも作るため、長尺では数GBの作業領域を見てください。

## ライセンス上の注意

本READMEは法的助言ではありません。配布前には、実際に同梱する Python runtime、PyTorch、Demucs、FFmpeg build、Tauri/Rust crate、その他依存関係のライセンスを確認してください。

本 fork で守るべき最低限の方針:

- 元プロジェクトの `LICENSE` と `NOTICE` を削除しない。
- 変更点を `NOTICE`、README、Git履歴で追えるようにする。
- 配布物のルートまたはアプリリソースに `LICENSE`、`NOTICE`、`THIRD_PARTY_NOTICES.txt` を含める。
- 公開配布時は、元プロジェクトの公式版ではなく `STEMDECK Enhanced` という変更版 fork test build であることを明示する。
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
codesign --verify --deep --strict --verbose=2 "path/to/STEMDECK Enhanced.app"
spctl -a -vv -t open path/to/STEMDECK-Enhanced-macOS-arm64.dmg
```

Windows 署名確認例:

```powershell
signtool verify /pa /v "STEMDECK Enhanced.exe"
```
