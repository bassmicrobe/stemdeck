param(
  [string]$Configuration = "release",
  [string]$OutputRoot    = "dist",
  [string]$PackageName   = "LayerLab-Windows-x64.NVIDIA",
  [string]$PackageVersion,
  [switch]$SkipTauriBuild,
  [switch]$CpuOnly,
  [switch]$StripVenv
)

$ErrorActionPreference = "Stop"
$PSNativeCommandErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
  throw "This packaging script must run on Windows."
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Stage = Join-Path $Root "$OutputRoot\$PackageName"
$ZipPath = Join-Path $Root "$OutputRoot\$PackageName.zip"
$ChecksumPath = "$ZipPath.sha256"
$PythonDir = Join-Path $Stage "python"
$PythonExe = Join-Path $PythonDir "Scripts\python.exe"
$BackendDir = Join-Path $Stage "backend"
$DesktopDir = Join-Path $Root "desktop"
$TauriDir = Join-Path $DesktopDir "src-tauri"
$TargetExe = Join-Path $TauriDir "target\$Configuration\layerlab.exe"
$PcmTargetExe = Join-Path $TauriDir "target\$Configuration\layerlab-pcm.exe"

function Require-Command([string]$Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command not found on PATH: $Name"
  }
}

function Copy-Tree([string]$Source, [string]$Destination) {
  if (Test-Path $Destination) {
    Remove-Item -Recurse -Force $Destination
  }
  Copy-Item -Recurse -Force $Source $Destination
}

function Copy-TreeContents([string]$Source, [string]$Destination, [string[]]$ExcludeNames = @()) {
  New-Item -ItemType Directory -Force $Destination | Out-Null
  Get-ChildItem -LiteralPath $Source -Force |
    Where-Object { $ExcludeNames -notcontains $_.Name } |
    ForEach-Object {
      Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
}

function Set-PyvenvValue([string]$ConfigPath, [string]$Key, [string]$Value) {
  $content = Get-Content -LiteralPath $ConfigPath
  $pattern = "^\s*$([regex]::Escape($Key))\s*="
  $line = "$Key = $Value"
  $found = $false
  $updated = foreach ($entry in $content) {
    if ($entry -match $pattern) {
      $found = $true
      $line
    } else {
      $entry
    }
  }
  if (-not $found) {
    $updated += $line
  }
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllLines($ConfigPath, [string[]]$updated, $utf8NoBom)
}

function Get-PackageVersion {
  if ($PackageVersion) {
    return $PackageVersion.TrimStart("v")
  }
  $tauriConfig = Get-Content -LiteralPath (Join-Path $TauriDir "tauri.conf.json") -Raw |
    ConvertFrom-Json
  return [string]$tauriConfig.version
}

function Bundle-PythonRuntime([string]$VenvDir, [string]$VenvPython) {
  $baseExecutable = (& $VenvPython -c "import sys; print(getattr(sys, '_base_executable', sys.executable))").Trim()
  if (-not (Test-Path $baseExecutable)) {
    throw "Could not locate base Python executable: $baseExecutable"
  }
  $baseHome = Split-Path -Parent $baseExecutable
  $portableBaseHome = Join-Path $VenvDir "base"
  $baseLib = Join-Path $baseHome "Lib"
  $baseDlls = Join-Path $baseHome "DLLs"
  if (-not (Test-Path $baseLib)) {
    throw "Could not locate base Python standard library: $baseLib"
  }

  Write-Host "Bundling Python runtime from $baseHome..."
  New-Item -ItemType Directory -Force $portableBaseHome | Out-Null
  Copy-Item -Force $baseExecutable (Join-Path $portableBaseHome "python.exe")
  $basePythonw = Join-Path $baseHome "pythonw.exe"
  if (Test-Path $basePythonw) {
    Copy-Item -Force $basePythonw (Join-Path $portableBaseHome "pythonw.exe")
  }
  Get-ChildItem -LiteralPath $baseHome -Filter "*.dll" -File -Force |
    Copy-Item -Destination $portableBaseHome -Force
  if (Test-Path $baseDlls) {
    Copy-Tree $baseDlls (Join-Path $portableBaseHome "DLLs")
  }
  Copy-TreeContents $baseLib (Join-Path $portableBaseHome "Lib") @("site-packages")
  $baseLicense = Join-Path $baseHome "LICENSE.txt"
  if (Test-Path $baseLicense) {
    Copy-Item -Force $baseLicense (Join-Path $portableBaseHome "LICENSE.txt")
  }

  $cfg = Join-Path $VenvDir "pyvenv.cfg"
  Set-PyvenvValue $cfg "home" $portableBaseHome
  Set-PyvenvValue $cfg "executable" (Join-Path $portableBaseHome "python.exe")
}

function Invoke-TauriBuild {
  $TauriCli = Join-Path $DesktopDir "node_modules\@tauri-apps\cli\tauri.js"
  if (-not (Test-Path $TauriCli)) {
    throw "Tauri CLI not found at $TauriCli. npm install/ci may have omitted devDependencies."
  }
  & node $TauriCli build
}

function Assert-Fresh-TauriBuild {
  if (-not (Test-Path $TargetExe)) {
    throw "Tauri executable not found at $TargetExe. Remove -SkipTauriBuild or build the NVIDIA package first."
  }

  $exe = Get-Item $TargetExe
  $newerSources = @(
    Get-ChildItem -Path (Join-Path $DesktopDir "ui") -File -Recurse
    Get-ChildItem -Path (Join-Path $TauriDir "src") -File -Recurse
    Get-Item (Join-Path $TauriDir "Cargo.toml")
    Get-Item (Join-Path $TauriDir "tauri.conf.json")
  ) | Where-Object { $_.LastWriteTimeUtc -gt $exe.LastWriteTimeUtc }

  if ($newerSources.Count -gt 0) {
    $list = ($newerSources | Select-Object -First 8 | ForEach-Object { "  - $($_.FullName)" }) -join "`n"
    throw @"
-SkipTauriBuild would package a stale LayerLab.exe.

The existing executable is older than desktop UI/Tauri source files:
$list

Remove -SkipTauriBuild or run the NVIDIA package build first so the CPU package reuses a fresh executable.
"@
  }
}

function Sign-FileIfConfigured([string]$Path) {
  $certPath = $env:WINDOWS_SIGN_CERT_PATH
  if (-not $certPath) {
    Write-Host "Skipping Windows code signing (set WINDOWS_SIGN_CERT_PATH to sign)."
    return
  }
  if (-not (Test-Path $certPath)) {
    throw "WINDOWS_SIGN_CERT_PATH does not exist: $certPath"
  }

  $signTool = if ($env:WINDOWS_SIGNTOOL_PATH) { $env:WINDOWS_SIGNTOOL_PATH } else { "signtool.exe" }
  $timestampUrl = if ($env:WINDOWS_TIMESTAMP_URL) {
    $env:WINDOWS_TIMESTAMP_URL
  } else {
    "http://timestamp.digicert.com"
  }

  if (-not (Get-Command $signTool -ErrorAction SilentlyContinue)) {
    throw "signtool not found. Install Windows SDK or set WINDOWS_SIGNTOOL_PATH."
  }

  $args = @(
    "sign",
    "/fd", "SHA256",
    "/tr", $timestampUrl,
    "/td", "SHA256",
    "/f", $certPath
  )
  if ($env:WINDOWS_SIGN_CERT_PASSWORD) {
    $args += @("/p", $env:WINDOWS_SIGN_CERT_PASSWORD)
  }
  $args += $Path

  Write-Host "Signing Windows executable: $Path"
  & $signTool @args
  & $signTool verify /pa /v $Path
}

Require-Command "node"
Require-Command "npm"
Require-Command "cargo"

if (-not (Get-Command "py" -ErrorAction SilentlyContinue) -and -not (Get-Command "python" -ErrorAction SilentlyContinue)) {
  throw "Python launcher not found. Install Python 3.12 on the Windows build agent."
}

if (Test-Path $Stage) {
  Remove-Item -Recurse -Force $Stage
}
if (Test-Path $ZipPath) {
  Remove-Item -Force $ZipPath
}
if (Test-Path $ChecksumPath) {
  Remove-Item -Force $ChecksumPath
}

New-Item -ItemType Directory -Force $Stage | Out-Null
New-Item -ItemType Directory -Force $BackendDir | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Stage "data") | Out-Null
foreach ($Dir in @("cache", "downloads", "ffmpeg", "jobs", "logs", "models")) {
  New-Item -ItemType Directory -Force (Join-Path $Stage "data\$Dir") | Out-Null
}
if ($CpuOnly) {
  New-Item -ItemType File -Force (Join-Path $Stage "cpu-only") | Out-Null
  New-Item -ItemType File -Force (Join-Path $Stage "data\cpu-only") | Out-Null
}

Copy-Tree (Join-Path $Root "app") (Join-Path $BackendDir "app")
Copy-Tree (Join-Path $Root "static") (Join-Path $BackendDir "static")
$PackageVersion = Get-PackageVersion
$VersionJson = @{ version = $PackageVersion } | ConvertTo-Json -Compress
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText((Join-Path $BackendDir "static\version.json"), $VersionJson + "`n", $utf8NoBom)
Copy-Item -Force (Join-Path $Root "pyproject.toml") (Join-Path $BackendDir "pyproject.toml")
Copy-Item -Force (Join-Path $Root "uv.lock") (Join-Path $BackendDir "uv.lock")
Copy-Item -Force (Join-Path $Root "LICENSE") (Join-Path $BackendDir "LICENSE")
Copy-Item -Force (Join-Path $Root "NOTICE") (Join-Path $BackendDir "NOTICE")
Copy-Item -Force (Join-Path $Root "packaging\windows\README-WINDOWS.txt") (Join-Path $Stage "README-WINDOWS.txt")
Copy-Item -Force (Join-Path $Root "packaging\windows\THIRD_PARTY_NOTICES.txt") (Join-Path $Stage "THIRD_PARTY_NOTICES.txt")
Copy-Item -Force (Join-Path $Root "LICENSE") (Join-Path $Stage "LICENSE")
Copy-Item -Force (Join-Path $Root "NOTICE") (Join-Path $Stage "NOTICE")

if (Get-Command "py" -ErrorAction SilentlyContinue) {
  & py -3.12 -m venv $PythonDir
} else {
  & python -m venv $PythonDir
}

& $PythonExe -m pip install --upgrade pip

# The project version is git-derived (hatch-vcs). Pin it from $PackageVersion so
# the install doesn't depend on git tags in the build checkout (#169).
if ($PackageVersion) {
  $env:SETUPTOOLS_SCM_PRETEND_VERSION = ($PackageVersion -replace '^v', '')
}
& $PythonExe -m pip install "$Root"

# Do not redistribute the GPL-enabled FFmpeg executable embedded in
# imageio-ffmpeg wheels. First-run setup downloads the disclosed build directly.
$imageioBinaries = Join-Path $PythonDir "Lib\site-packages\imageio_ffmpeg\binaries"
if (Test-Path $imageioBinaries) {
  Get-ChildItem -LiteralPath $imageioBinaries -Filter "ffmpeg-*" -File -Force |
    Remove-Item -Force
}

if ($CpuOnly) {
  # pip strips local version identifiers when resolving requirements, so it installs
  # the CUDA wheel from PyPI even when we pre-install the CPU wheel. Force-reinstall
  # after the fact: uninstalls CUDA torch and replaces it with the CPU-only variant.
  & $PythonExe -m pip install torch==2.6.0+cpu torchaudio==2.6.0+cpu `
      --index-url https://download.pytorch.org/whl/cpu `
      --force-reinstall --no-deps
}

& $PythonExe -c "import fastapi, uvicorn, yt_dlp, demucs, torch, torchaudio, librosa, pyloudnorm, soundfile"

Bundle-PythonRuntime $PythonDir $PythonExe
& $PythonExe -c "import sys, fastapi, uvicorn; print('Portable Python:', sys.executable)"

$LicenseDir = Join-Path $Stage "licenses"
& $PythonExe (Join-Path $Root "scripts\generate-license-bundle.py") `
  --repo-root $Root `
  --python-root (Join-Path $PythonDir "base") `
  --site-packages (Join-Path $PythonDir "Lib\site-packages") `
  --cargo-manifest (Join-Path $TauriDir "Cargo.toml") `
  --target "x86_64-pc-windows-msvc" `
  --output $LicenseDir
Copy-Item -Force (Join-Path $LicenseDir "THIRD_PARTY_NOTICES.md") (Join-Path $Stage "THIRD_PARTY_NOTICES.txt")
Copy-Item -Force (Join-Path $LicenseDir "THIRD_PARTY_LICENSES.txt") (Join-Path $Stage "THIRD_PARTY_LICENSES.txt")
Copy-Item -Force (Join-Path $LicenseDir "THIRD_PARTY_INVENTORY.json") (Join-Path $Stage "THIRD_PARTY_INVENTORY.json")
Copy-Item -Force (Join-Path $LicenseDir "THIRD_PARTY_NOTICES.md") (Join-Path $BackendDir "THIRD_PARTY_NOTICES.txt")
Copy-Item -Force (Join-Path $LicenseDir "THIRD_PARTY_LICENSES.txt") (Join-Path $BackendDir "THIRD_PARTY_LICENSES.txt")
Copy-Item -Force (Join-Path $LicenseDir "THIRD_PARTY_INVENTORY.json") (Join-Path $BackendDir "THIRD_PARTY_INVENTORY.json")

if ($StripVenv) {
  Write-Host "Stripping venv of build-time artifacts..."
  Get-ChildItem -Path $PythonDir -Filter "__pycache__" -Recurse -Directory -Force |
    Remove-Item -Recurse -Force
  foreach ($rel in @("torch\include", "torch\share\cmake", "torch\test")) {
    $p = Join-Path $PythonDir "Lib\site-packages\$rel"
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
  }
  # Remove C++ static link libraries from torch — needed only for building C++ extensions,
  # never for running Python. dnnl.lib alone is ~623 MB.
  Get-ChildItem -Path (Join-Path $PythonDir "Lib\site-packages\torch") `
      -Filter "*.lib" -Recurse -File -Force |
    Remove-Item -Force
}

Push-Location $DesktopDir
try {
  if (Test-Path "package-lock.json") {
    npm ci --include=dev
  } else {
    npm install --include=dev
  }

  if (-not $SkipTauriBuild) {
    $env:CI = "true"  # Woodpecker sets CI=woodpecker; Tauri only accepts true/false
    rustup default stable
    Invoke-TauriBuild
  } else {
    Assert-Fresh-TauriBuild
  }
} finally {
  Pop-Location
}

$PcmCargoArgs = @(
  "build",
  "--manifest-path", (Join-Path $TauriDir "Cargo.toml"),
  "--bin", "layerlab-pcm"
)
if ($Configuration -eq "release") {
  $PcmCargoArgs += "--release"
} elseif ($Configuration -ne "debug") {
  $PcmCargoArgs += @("--profile", $Configuration)
}
& cargo @PcmCargoArgs

if (-not (Test-Path $TargetExe)) {
  throw "Tauri executable not found at $TargetExe"
}
if (-not (Test-Path $PcmTargetExe)) {
  throw "Rust PCM sidecar not found at $PcmTargetExe"
}

Copy-Item -Force $TargetExe (Join-Path $Stage "LayerLab.exe")
Sign-FileIfConfigured (Join-Path $Stage "LayerLab.exe")
New-Item -ItemType Directory -Force (Join-Path $Stage "bin") | Out-Null
Copy-Item -Force $PcmTargetExe (Join-Path $Stage "bin\layerlab-pcm.exe")
Sign-FileIfConfigured (Join-Path $Stage "bin\layerlab-pcm.exe")

Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipPath -Force
$Hash = Get-FileHash -Algorithm SHA256 $ZipPath
Set-Content -Path $ChecksumPath -Value "$($Hash.Hash)  $PackageName.zip"

$Variant = if ($CpuOnly) { "CPU-only" } else { "CUDA/GPU (NVIDIA)" }
Write-Host "Variant     : $Variant"
Write-Host "Staged at   : $Stage"
Write-Host "Zip created : $ZipPath"
Write-Host "Checksum    : $ChecksumPath"
