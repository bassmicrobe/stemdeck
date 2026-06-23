<div align="center">

<h1>LayerLab</h1>

**Unofficial fork test build. Free, local stem separation. No account. No upload. No subscription.**

<div align="center">
  <a href="https://ci.popchores.app/repos/2"><img src="https://ci.popchores.app/api/badges/2/status.svg?event=push" alt="CI"></a>
  <a href="https://github.com/bassmicrobe/stemdeck/stargazers"><img src="https://img.shields.io/github/stars/bassmicrobe/stemdeck?style=flat-square" alt="Fork GitHub Stars"></a>
  <a href="https://github.com/bassmicrobe/stemdeck/releases"><img src="https://img.shields.io/github/downloads/bassmicrobe/stemdeck/total?style=flat-square&color=52c65f" alt="Fork Downloads"></a>
  <a href="https://github.com/bassmicrobe/stemdeck/releases/latest"><img src="https://img.shields.io/github/v/release/bassmicrobe/stemdeck?style=flat-square" alt="Latest Fork Release"></a>
  <a href="https://github.com/bassmicrobe/stemdeck/blob/feat/high-quality-separation/LICENSE"><img src="https://img.shields.io/github/license/bassmicrobe/stemdeck?style=flat-square" alt="License"></a>
</div>

<br>

<p align="center"><sub>UNOFFICIAL FORK TEST BUILD</sub></p>
<div align="center">
  <a href="https://github.com/bassmicrobe/stemdeck"><img src="https://img.shields.io/badge/Fork-bassmicrobe%2Fstemdeck-181717?style=flat-square&logo=github&logoColor=white" alt="Fork GitHub"></a>
  <a href="https://github.com/stemdeckapp/stemdeck"><img src="https://img.shields.io/badge/Original-stemdeckapp%2Fstemdeck-6b7280?style=flat-square&logo=github&logoColor=white" alt="Original StemDeck"></a>
</div>

</div>

<br>

LayerLab is an unofficial modified fork of [StemDeck](https://github.com/stemdeckapp/stemdeck). It is a test build for higher-quality local stem separation and is not an official upstream release, not affiliated with, and not endorsed by the original StemDeck project.

Drop in an MP3, WAV, FLAC, or M4A file, or paste a YouTube URL, and LayerLab splits the audio into up to six stems (vocals, drums, bass, guitar, piano, other). Play them back in a DAW-style multitrack mixer: mute, solo, balance levels, zoom the waveform, loop a region, and export individual stems or a custom mix. Everything runs locally on your own machine.

> **What is this?** LayerLab is a stem separation tool, not a downloader. Its main job is processing audio you already own: drag an MP3, WAV, FLAC, or M4A onto the import bar and go. YouTube support is a convenience for content you have the right to process. LayerLab does not store, cache, or redistribute any downloaded content. Everything happens locally and nothing leaves your machine.

> LayerLab is a free, open alternative to cloud stem-splitters like Moises and LALAL.AI: no account, no quota, no uploads, no subscription. If you want stems for personal study and prefer to keep things local and free, LayerLab has you covered. If you need the polish, a mobile app, or deeper musician tooling, the commercial products are a better fit.

日本語での概要、配布手順、ライセンス/NOTICE、そしてこの fork で加えた変更点は [README.ja.md](README.ja.md) にまとめています。操作マニュアルは [MANUAL.ja.md](MANUAL.ja.md) を参照してください。

![LayerLab screenshot](imgs/screenshot/stemdeck.png)

## We Recommend

LayerLab is free and **does not accept any money, sponsorship, or funding** - not from users, not from anyone listed below. We share these makers and artists purely for the joy of pointing you toward wonderful people doing beautiful work. Go meet them ❤️

| Supporter | What they do | Link |
|---|---|---|
| Dlima Guitars | Custom guitars and basses | [@dlimaguitars](https://www.instagram.com/dlimaguitars) |
| Lisbon Guitar Works | Guitar building | [dlimaguitars.com](https://dlimaguitars.com) |
| Joao Gaspar | Producer/Film Scorer, Touring/Session Musician | [@jay_glaspar](https://www.instagram.com/jay_glaspar) |
| Kris Luthier | Luthier and Musical Instrument Repair, Lisboa | [@krisluthier](https://www.instagram.com/krisluthier) |


---

## Features

**6-stem separation** via Demucs `htdemucs_6s`, with auto-detection of the best Torch device (CUDA on NVIDIA, MPS on Apple Silicon, CPU fallback).

**YouTube and local file import.** Paste a YouTube URL or drop an MP3, WAV, FLAC, or M4A directly onto the import bar.

**DAW-style waveform editor** with min/max sample rendering across all stems, shared normalization, zoom in/out/Fit, loop drag on the ruler, gold playhead overlay, and stem-aligned lanes.

**Stem subset extraction.** Click stem chips to choose which stems to keep. Clicking from "all selected" snaps to "only this one"; subsequent clicks add or remove.

**Multiple profiles per song.** Re-run the same source with different `Quality`, `Device`, `Clean`, or selected-stem settings and LayerLab keeps each result as a separate profile in the library. Exported filenames include the profile, and stem ZIPs include `LAYERLAB_PROFILE.txt`.

**Device selection.** Choose `Auto`, `CPU`, Apple GPU (`mps`), or NVIDIA CUDA per job. Unavailable GPU options are disabled in the UI.

**"Original" backing track.** When you pick a subset, a 7th lane contains the complement (full song minus selected stems), perfect for A/B reference without doubling.

**Downloadable selected mix.** A single `mix.wav` of just your selected stems, summed via ffmpeg amix.

**Per-stem mixer** with volume fader, mute, solo, and "monitor" (solo-only) per stem. State syncs between the preview mixer and the stems sidebar.

**Live VU meters** per stem. Post-gain RMS via Web Audio analysers with peak hold and slow falloff.

**Song analysis** including BPM and beat grid timestamps (librosa beat tracker, first 180 seconds), key, scale, and confidence (Albrecht-Shanahan profiles), integrated LUFS (BS.1770), and sample peak in dBFS.

**Chord guide export.** LayerLab estimates quarter-note-grid chord labels from piano/guitar-weighted chroma plus bass-root hints, suppresses weak one-beat misreads, merges stable repeats into sustained chord blocks, and exports MIDI or CSV from the Export menu. Chord export can be switched between beat/bar grid and auto/triad/seventh styles, with optional DAW marker events in MIDI.

**Local quality benchmark.** `scripts/benchmark_audio.py` compares a source file against exported stems, reports stem-sum residual error, clipping risk, chord metadata coverage, and writes machine-readable JSON for regression tracking. It can also scan a whole `jobs/` root and compare against a previous baseline.

**Cancellable jobs.** Cancel mid-pipeline and the runner terminates the active subprocess immediately, deletes the partial job dir, and returns to ready.

**Library panel** with folder-based track organisation, drag-and-drop, search, and trash.

---

## Honest Comparison

LayerLab is not trying to compete with commercial stem-separation products. It covers the core use case well and stops there. This table exists so you can make an informed choice rather than discover the gaps after the fact.

| | LayerLab | Moises / LALAL.AI / similar |
|---|---|---|
| **Price** | Free, forever | Freemium; credits or subscription required for regular use |
| **Hosting** | Runs entirely on your machine | Cloud; audio must be uploaded to their servers |
| **Account / login** | None | Required |
| **Internet required** | Only for YouTube download and first model fetch (~170 MB, cached after) | Always; no offline use |
| **Privacy** | Audio never leaves your machine | Audio is uploaded and processed on third-party servers |
| **Data retention** | You control it; delete anytime | Governed by their privacy policy and retention period |
| **Stem model** | Demucs `htdemucs_6s` / `htdemucs_ft` depending on quality preset (open source, Meta AI) | Proprietary models, regularly updated, generally higher quality |
| **Stem count** | 6 (vocals, drums, bass, guitar, piano, other) | Up to 10 depending on service and plan |
| **Input formats** | YouTube URL, MP3, WAV, FLAC, M4A | MP3, WAV, FLAC, M4A, and more depending on service |
| **Processing speed** | Depends on your hardware; fast with a GPU, slow on CPU only | Fast regardless of your hardware (runs on their servers) |
| **Batch processing** | Local queue with background processing; concurrency auto-limited by CPU/GPU memory | Yes, on paid plans |
| **Diagnostics** | In-app per-job/session logs with stage, progress, warnings, and persisted failure details | Varies |
| **Mobile app** | No | iOS and Android |
| **Extra features** | BPM/beat grid, chord MIDI guide, local quality benchmark; no lyrics, pitch shift, or mobile tooling | Yes, varies by product |
| **Polish** | Functional, hobby-grade UI | Polished, production-grade apps |
| **Source code** | Open source, forkable, self-hostable | Closed source |

If you need speed, quality, mobile access, or the extra musician tooling, the commercial products are worth the money. If you want stems for personal study, prefer to keep audio private, or just want something that runs locally with no strings attached, LayerLab is enough.

---

## Download

Unofficial fork test builds are attached to [GitHub Releases](https://github.com/bassmicrobe/stemdeck/releases). For production use, prefer a Developer ID signed and notarized macOS build.

**macOS**

| DMG | GPU | Chip |
|---|---|---|
| `LayerLab-macOS-arm64.dmg` | Apple Silicon (MPS) | M1 and later |
| `LayerLab-macOS-x64.dmg` | CPU only | Intel |

Open the DMG, drag LayerLab to Applications, and launch it. On first launch the setup screen downloads the Python runtime, FFmpeg, and the Demucs model (~170 MB). Subsequent launches skip setup and start in seconds. No Python or system dependencies required.

macOS may show a Gatekeeper prompt on first open — right-click the app and choose Open to bypass it.

**Windows**

| Zip | GPU | Approx. size |
|---|---|---|
| `LayerLab-Windows-x64.zip` | CPU only | ~700 MB |
| `LayerLab-Windows-x64.NVIDIA.zip` | NVIDIA CUDA | ~1.6 GB |

Extract the zip anywhere, run `LayerLab.exe`. On first launch the app verifies the bundled Python runtime and downloads FFmpeg and the Demucs model (~170 MB). Subsequent launches skip this and start in seconds. Everything is self-contained; no Python or system dependencies required.

Windows installers are generated by wrapping the completed portable folder with Inno Setup 6:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/make-installer.ps1 `
  -PackageName LayerLab-Windows-x64.NVIDIA `
  -StripVenv

powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/make-installer.ps1 `
  -PackageName LayerLab-Windows-x64 `
  -CpuOnly `
  -StripVenv
```

The installer lands in `dist/*-Setup.exe`, installs per-user under `%LocalAppData%\Programs\LayerLab`, creates Start Menu/Desktop shortcut entries, and includes `LICENSE`, `NOTICE`, and `THIRD_PARTY_NOTICES.txt`.

### Release Signing / Notarization

Local builds are unsigned unless signing credentials are provided. Release scripts support optional signing without storing secrets in the repository:

- macOS app signing: set `APPLE_SIGNING_IDENTITY` before `scripts/macos/make-app.sh`.
- macOS DMG signing: set `APPLE_SIGNING_IDENTITY` before `scripts/macos/make-dmg.sh`.
- macOS notarization: set `APPLE_NOTARIZE=1` and either `APPLE_NOTARY_KEYCHAIN_PROFILE` or `APPLE_ID`, `APPLE_TEAM_ID`, and `APPLE_APP_SPECIFIC_PASSWORD` before `scripts/macos/make-dmg.sh`.
- Windows executable signing: set `WINDOWS_SIGN_CERT_PATH` and optionally `WINDOWS_SIGN_CERT_PASSWORD`, `WINDOWS_SIGNTOOL_PATH`, and `WINDOWS_TIMESTAMP_URL` before `scripts/windows/make-portable.ps1`.
- Windows installer signing: use the same variables before `scripts/windows/make-installer.ps1`; the script signs both `LayerLab.exe` and the generated installer when credentials are present.

Unsigned internal builds are acceptable for local testing, but public macOS builds should be Developer ID signed and notarized. Public Windows builds should be Authenticode signed when possible.

### Distribution Size and Storage

The desktop shell is intentionally thin. The Python runtime, FFmpeg/ffprobe, and Demucs model are prepared during first-run setup or first use. This keeps the app bundle smaller, but the first launch needs internet access and enough disk space.

- macOS first-run setup downloads a runtime pack, FFmpeg/ffprobe, and later the model cache.
- Windows portable builds include a Python environment; the NVIDIA variant is larger because CUDA/PyTorch wheels are large.
- Stem WAVs are large: a 10-minute stereo 16-bit WAV is about 101 MiB, and a 10-minute stereo float32 WAV is about 202 MiB before multiplying by the number of stems.
- `LICENSE`, `NOTICE`, and platform `THIRD_PARTY_NOTICES.txt` are copied into release packages. Runtime dependency inventories are generated where available.

---

## Technologies

<div align="center">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-0078D6?style=flat-square&logo=windows" alt="Platform">
  <img src="https://img.shields.io/badge/Powered_by-Demucs-FF6B35?style=flat-square" alt="Powered by Demucs">
  <img src="https://img.shields.io/badge/CI-Woodpecker-4D9DE0?style=flat-square&logo=woodpecker-ci&logoColor=white" alt="CI: Woodpecker">
</div>

<br>

LayerLab is built on **[Python 3.12](https://python.org)** managed via **[uv](https://github.com/astral-sh/uv)**, with a **[FastAPI](https://fastapi.tiangolo.com)** backend serving REST and Server-Sent Events. Stem separation uses **[Demucs](https://github.com/facebookresearch/demucs)** (`htdemucs_6s` for Standard, `htdemucs_ft` for High / Max / Ultra), Meta AI's open-source neural stem separation models. YouTube audio is fetched via **[yt-dlp](https://github.com/yt-dlp/yt-dlp)**; transcoding and mixing use **[FFmpeg](https://ffmpeg.org)**. BPM detection and key analysis run on **[librosa](https://librosa.org)**; loudness measurement uses **[pyloudnorm](https://github.com/csteinmetz1/pyloudnorm)** (ITU-R BS.1770). The macOS and Windows desktop shells are **[Tauri v2](https://tauri.app)** (Rust/WKWebView on macOS, Rust/WebView2 on Windows). The frontend is vanilla JS with the Web Audio API, no framework and no build step; waveforms are rendered on `<canvas>` using min/max sample rendering.

*Thanks to the creators and maintainers of all the open-source libraries that make LayerLab possible.*

---

## Build from Source

### macOS Native App

Requires Rust, Node.js, and Python 3.12. Builds a self-contained `.app` that downloads its own runtime on first launch.

```sh
# First time only — add the cross-compilation targets
rustup target add aarch64-apple-darwin   # Apple Silicon
rustup target add x86_64-apple-darwin    # Intel

# Build Apple Silicon
ARCH=arm64 scripts/macos/make-runtime-pack.sh
ARCH=arm64 scripts/macos/make-app.sh
ARCH=arm64 scripts/macos/make-dmg.sh

# Build Intel (requires Rosetta 2 and an x86_64 Python)
ARCH=x64 scripts/macos/make-runtime-pack.sh
ARCH=x64 scripts/macos/make-app.sh
ARCH=x64 scripts/macos/make-dmg.sh
```

The `.app` lands at `desktop/src-tauri/target/<target>/release/bundle/macos/LayerLab.app`. The DMG lands at `.build/macos-dist/LayerLab-macOS-<arch>.dmg`.

To run a fresh build directly without the DMG:

```sh
open "desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/LayerLab.app"
```

If macOS blocks the app with a Gatekeeper prompt, run:

```sh
xattr -dr com.apple.quarantine "desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/LayerLab.app"
```

> **Note:** To test a clean first-launch during development, you can wipe previous app data first: `rm -rf ~/Library/Application\ Support/LayerLab`. Don't do this on a real install.

---

### Web Server (macOS / Linux / Windows with Python 3.12+)

#### Prerequisites

Python 3.12 or newer, `ffmpeg` on your PATH, and [uv](https://github.com/astral-sh/uv). Around 170 MB of free disk for the Demucs model, which downloads automatically on first run.

#### macOS / Linux (one-shot)

```sh
git clone https://github.com/bassmicrobe/stemdeck layerlab && cd layerlab
./run.sh setup     # installs ffmpeg + uv, runs uv sync
./run.sh start
```

Open <http://localhost:8000>.

`setup` uses Homebrew on macOS and `apt-get` on Debian/Ubuntu. For other Linux distros, install `ffmpeg` and [uv](https://github.com/astral-sh/uv) manually, then run `uv sync` followed by `./run.sh start`.

#### Windows (PowerShell)

Install prerequisites:
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — `winget install astral-sh.uv`
- [ffmpeg](https://ffmpeg.org/download.html) — `winget install Gyan.FFmpeg` (or Chocolatey: `choco install ffmpeg`)

```powershell
git clone https://github.com/bassmicrobe/stemdeck layerlab; cd layerlab
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>.

> `run.sh` is macOS/Linux only. On Windows use the PowerShell commands above, or run inside WSL.

**NVIDIA GPU (CUDA):** install the CUDA-enabled torch build before starting:

```powershell
uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
$env:STEMDECK_DEMUCS_DEVICE = "cuda"
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

#### Manual (any platform)

```sh
git clone https://github.com/bassmicrobe/stemdeck layerlab && cd layerlab
uv sync
uv run uvicorn app.main:app --reload
```

#### Docker

```sh
docker compose -f build/docker-compose.yml up --build
```

Stems land in `./jobs/` on the host. Demucs weights are cached in a named volume so they don't re-download on rebuild. Note: no GPU passthrough on macOS Docker.

#### `run.sh` control script

```sh
./run.sh setup      # one-shot: install ffmpeg + uv, then uv sync
./run.sh start      # boots uvicorn in the background
./run.sh stop       # graceful shutdown
./run.sh restart    # stop + start
./run.sh status     # is it running?
```

If `ffmpeg` is not installed on your PATH, `./run.sh start` and the Python
runtime fall back to an `imageio-ffmpeg` binary automatically.

---

## How to Use

1. On the import bar, click stem chips to choose which stems to extract (defaults to all 6).
2. Paste a YouTube URL **or** drop an MP3/WAV/FLAC/M4A file, then click **Process**.
3. Wait through `Uploading...` / `Downloading...` → `Analyzing...` → `Separating...` → `Mixing tracks...`.
4. You can add multiple tracks to the queue. Once a completed track is selected, later jobs continue in the background so preview and downloads stay available.
5. When done, the studio dashboard appears. If you picked a subset, the first lane is **Original** (full song minus your selection); the rest are your isolated stems.
6. Mix: **Play/Pause/Stop** controls the master transport. **M** mutes a stem, **S** solos it (additive; multiple solos stay audible), **Monitor** solos only that stem and clears others. The volume fader moves 1:1 with drag; double-click resets to 0 dB; `Shift+wheel` gives coarse adjustment and plain wheel gives fine. The **Reset**, **Mute**, and **Solo** toolbar buttons act on all stems at once.
7. Drag on the ruler to define a loop region; click `Loop` to enable. Use `+` / `-` / `Fit` or `Ctrl/Cmd+wheel` to zoom.
8. **Download Mix** in the footer gives you a WAV of your selected stems summed together.

**Keyboard shortcuts:** `Space` play/pause · `[` seek -5s · `]` seek +5s · `L` loop · `I` loop in · `O` loop out

---

## Quality Benchmark

Use the local benchmark helper when comparing quality presets, denoise settings, or phase/bass repair changes. It decodes the source and stem WAVs through ffmpeg, sums the stems, and reports residual error as JSON.

```sh
uv run python scripts/benchmark_audio.py \
  --source /path/to/original.wav \
  --stems-dir jobs/<job-id>/stems \
  --metadata jobs/<job-id>/metadata.json \
  --out .build/benchmarks/<job-id>.json
```

For an unswept or in-progress job that still has `source.*` in its job directory:

```sh
uv run python scripts/benchmark_audio.py --job-dir jobs/<job-id>
```

Completed jobs may have their source audio removed to save disk space. In that case, pass `--source` explicitly if you want stem-sum residual metrics; without a source, the report still summarizes available stems and chord metadata.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `STEMDECK_QUALITY_PRESET` | `standard` | Separation quality preset: `standard`, `high`, `max`, or `ultra`. `high` / `max` / `ultra` use slower Demucs settings and preserve 32-bit float WAV output. |
| `STEMDECK_DEMUCS_DEVICE` | auto | Force Torch device: `cuda`, `mps`, or `cpu`. |
| `STEMDECK_PIPELINE_CONCURRENCY` | auto | Heavy analysis/separation jobs to run in parallel. Auto keeps CUDA/MPS at `1` for memory safety and uses `2` only on roomy CPU-only machines. Set `1`-`4` to override. |
| `STEMDECK_PIPELINE_LOCK` | system temp file | Base path for cross-process processing-slot locks. LayerLab creates one shared slot per configured concurrency level. |
| `STEMDECK_DEMUCS_MODEL` | preset-dependent | Demucs model name. `standard` uses `htdemucs_6s`; `high` / `max` / `ultra` use `htdemucs_ft` unless overridden. |
| `STEMDECK_DEMUCS_SHIFTS` | preset-dependent | Number of Demucs shift averages. Higher is slower and can reduce artifacts. |
| `STEMDECK_DEMUCS_PRE_GAIN_DB` | preset-dependent | Optional input gain before Demucs. Negative values such as `-6` can help very loud masters separate more cleanly. |
| `STEMDECK_DEMUCS_FLOAT32` | preset-dependent | Write Demucs stems as 32-bit float WAVs when truthy. |
| `STEMDECK_DEMUCS_CLIP_MODE` | preset-dependent | Demucs output clipping mode: `rescale`, `clamp`, or `none`. |
| `STEMDECK_DEMUCS_OVERLAP` | `0` | Optional Demucs segment overlap override. `0` leaves the Demucs default untouched. |
| `STEMDECK_DEMUCS_SEGMENT` | `0` | Optional Demucs segment length override. `0` leaves the Demucs default untouched. |
| `STEMDECK_BASS_REPAIR` | `high`/`max`/`ultra`: on, `standard`: off | Repair short bass dropouts after separation by blending a low-frequency residual from the original mix. Set `0` to disable or `1` to force-enable. |
| `STEMDECK_BASS_REPAIR_LOW_PASS_HZ` | `180` | Low-pass cutoff used for the bass residual candidate. |
| `STEMDECK_BASS_REPAIR_TRIGGER_RATIO` | `1.9` | How much stronger the residual must be than the bass stem before repair blends in. Higher is more conservative. |
| `STEMDECK_BASS_REPAIR_MAX_BLEND` | `0.65` | Maximum amount of residual blended into detected bass dropouts. |
| `STEMDECK_PHASE_REPAIR` | `high`/`max`/`ultra`: on, `standard`: off | Repair stem-sum phase/residual mismatch against the original source. Set `0` to disable or `1` to force-enable. |
| `STEMDECK_PHASE_REPAIR_MAX_BLEND` | `standard`: `0.42`, `high`: `0.65`, `max`: `0.90`, `ultra`: `0.95` | Maximum source-minus-stem-sum residual blended back into active stems. Higher reconstructs the source more strongly but can increase bleed. |
| `STEMDECK_PHASE_REPAIR_FLOOR_DB` | `-58` | Residual floor below which phase repair stays inactive. Lower values are less conservative. |
| `STEMDECK_JOBS_DIR` | `./jobs` | Where job directories land. |
| `STEMDECK_DATA_DIR` | (none) | Portable mode root; sets all sub-dirs below to live inside it. |
| `STEMDECK_CACHE_DIR` | `<data>/cache` | Torch model cache directory. |
| `STEMDECK_DOWNLOADS_DIR` | `<data>/downloads` | yt-dlp download scratch space. |
| `STEMDECK_MODELS_DIR` | `<data>/models` | Demucs model weights directory. |
| `STEMDECK_LOGS_DIR` | `<data>/logs` | Reserved log file output directory; structured job diagnostics are also available in the in-app Logs viewer. |
| `STEMDECK_FFMPEG_DIR` | (none) | Directory containing a bundled ffmpeg binary. |
| `STEMDECK_FFMPEG` | `ffmpeg` | Path to the ffmpeg executable. |
| `STEMDECK_FFPROBE` | `ffprobe` | Path to the ffprobe executable. |
| `STEMDECK_MAX_DURATION_SEC` | `1200` | Reject audio longer than this (seconds). |
| `STEMDECK_JOB_TTL_SECONDS` | `86400` | How long to keep job dirs on disk. |
| `STEMDECK_MAX_PENDING_JOBS` | `3` | Max queued jobs before returning 503. |
| `STEMDECK_TIMEOUT_FFMPEG` | `300` | ffmpeg subprocess timeout (seconds). |
| `STEMDECK_TIMEOUT_ANALYZE` | `120` | Audio analysis timeout (seconds). |
| `STEMDECK_TIMEOUT_DEMUCS_STALL` | `1800` | Kill Demucs if no output for this many seconds. |

`run.sh` also reads: `HOST` (default `127.0.0.1`), `PORT` (default `8765`), `RELOAD=1` (enable uvicorn auto-reload for development), `FOREGROUND=1` (run in foreground instead of backgrounding).

---

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Server health and version info |
| POST | `/api/jobs` | JSON `{url, stems?}` or multipart `file + stems` → `{job_id}` |
| GET | `/api/jobs` | List completed (library) jobs |
| GET | `/api/jobs/{id}` | Job state snapshot |
| GET | `/api/jobs/{id}/events` | SSE stream of job state |
| POST | `/api/jobs/{id}/cancel` | Terminate active subprocess and cancel job |
| PATCH | `/api/jobs/{id}/sections` | Save waveform section markers for a job |
| GET | `/api/jobs/{id}/stems/{name}.wav` | Stream a single stem WAV file |
| GET | `/api/jobs/{id}/stems/{name}.mp3` | Transcode and stream a stem as MP3 |
| DELETE | `/api/jobs/{id}` | Remove job dir from disk (terminal jobs only) |

---

## Troubleshooting

**`ffmpeg: command not found`:** install ffmpeg and restart with `./run.sh restart`.

**`WARNING: [youtube] No supported JavaScript runtime`:** install deno (`brew install deno` on macOS) and restart. Downloads still work without it but may pick suboptimal formats.

**First separation is very slow:** Demucs downloads `htdemucs_6s` weights (~170 MB) on first run; cached afterwards.

**Demucs runs on CPU only:** check the startup log for `device=mps` or `device=cuda`. If you see `cpu`, your torch install may be CPU-only.

**Page reloaded mid-job:** the job keeps running server-side. Wait for it to finish, then resubmit.

**`./run.sh: Permission denied`:** run `chmod +x run.sh`.

---

## Layout on Disk

```
jobs/<job_id>/
└── stems/
    ├── vocals.wav      # the 6 Demucs stems (always present)
    ├── drums.wav
    ├── bass.wav
    ├── guitar.wav
    ├── piano.wav
    ├── other.wav
    ├── original.wav    # sum of un-selected stems (subset only)
    └── mix.wav         # ffmpeg amix of selected stems (subset only)
```

Job state is in-memory. Restart the server and the job list resets, but files persist on disk. Old dirs are swept automatically (TTL 24 h, configurable).

---

## Disclaimer

LayerLab is a local audio stem separation tool intended for personal study, research, and experimentation. It is not a downloading service. It does not store, cache, or redistribute any audio content. All processing runs on the user's own machine and no audio is transmitted anywhere.

YouTube URL support is provided via [yt-dlp](https://github.com/yt-dlp/yt-dlp) as a convenience. Automated downloading may violate YouTube's Terms of Service. You, the user, are solely responsible for ensuring you have the right to process any audio you submit, complying with the terms of service of any site you download from, and respecting the copyright of the material you work with.

You are also responsible for following the licenses of the underlying tools this project depends on (yt-dlp, Demucs, FFmpeg, PyTorch, and others listed in `pyproject.toml`).

The author(s) of LayerLab provide this software "as is", without warranty of any kind, and accept no responsibility or liability for how it is used.

---

## License and Attribution

LayerLab is based on the original [StemDeck](https://github.com/stemdeckapp/stemdeck) project. The original StemDeck project is licensed under the [Apache License 2.0](LICENSE), and this fork retains that license and attribution.

See [NOTICE](NOTICE) for the upstream attribution and modification notice. Third-party runtime dependencies are licensed by their respective authors; packaged builds include `THIRD_PARTY_NOTICES.txt` and generated dependency inventories where available.

This repository is an unofficial modified fork test build, not an official upstream release. It is not affiliated with or endorsed by the original StemDeck project. Apache-2.0 does not grant trademark rights, so public distributions should avoid implying upstream endorsement.

### Commercial Use

Apache License 2.0 does not prohibit commercial use, paid distribution, internal business use, or distribution of modified versions. Commercial use of this fork is therefore possible when the Apache-2.0 conditions and all third-party dependency licenses are respected.

Do not remove `LICENSE`, `NOTICE`, or packaged third-party notices. Do not present this fork as an official StemDeck release, official commercial offering, certified build, or upstream-supported product. Trademark-like use of the StemDeck/STEMDECK name or logos is not granted by Apache-2.0 except as needed to describe the origin of the work and reproduce NOTICE content.

If you sell, host, bundle, or provide services around this software, you are responsible for verifying the exact licenses of the shipped FFmpeg build, PyTorch, Demucs, yt-dlp, Python runtime, Tauri/Rust crates, and any other bundled components, and for ensuring that users have the rights to process the audio they submit. This documentation is not legal advice.

---

## Community

| Platform | Link |
|---|---|
| Fork GitHub | [bassmicrobe/stemdeck](https://github.com/bassmicrobe/stemdeck) |
| Original project | [stemdeckapp/stemdeck](https://github.com/stemdeckapp/stemdeck) |

---

## Environment Variables

These are for development and testing. Release builds only recognize the variables marked "release".

| Variable | Platform | Scope | Description |
|---|---|---|---|
| `STEMDECK_DATA_DIR` | all | release | Override the user data directory (default: platform-standard location) |
| `STEMDECK_ROOT` | all | release | Override the app root directory (default: derived from executable path) |
| `STEMDECK_PYTHON` | all | **debug builds only** | Override the Python executable path |
| `STEMDECK_FFMPEG_URL` | Windows, macOS | release | Override the FFmpeg download URL |
| `STEMDECK_FFPROBE_URL` | macOS | release | Override the ffprobe download URL |

---

## Contributing

Issues, feature suggestions, and pull requests are welcome. See open issues for what's planned.

---

## Star History

<a href="https://www.star-history.com/?repos=bassmicrobe%2Fstemdeck&type=date&legend=top-left">
  <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=bassmicrobe/stemdeck&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=bassmicrobe/stemdeck&type=date&legend=top-left" />
   <img alt="Fork Star History Chart" src="https://api.star-history.com/chart?repos=bassmicrobe/stemdeck&type=date&legend=top-left" />
  </picture>
</a>
