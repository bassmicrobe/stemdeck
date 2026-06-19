param(
  [string]$Configuration = "release",
  [string]$OutputRoot    = "dist",
  [string]$PackageName   = "LayerLab-Windows-x64.NVIDIA",
  [string]$PackageVersion,
  [string]$PortableRoot,
  [string]$InnoSetupCompiler,
  [switch]$SkipPortableBuild,
  [switch]$SkipTauriBuild,
  [switch]$CpuOnly,
  [switch]$StripVenv
)

$ErrorActionPreference = "Stop"
$PSNativeCommandErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
  throw "This installer script must run on Windows."
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Template = Join-Path $Root "packaging\windows\layerlab.iss"
$InstallerOutputRoot = Join-Path $Root $OutputRoot

function Get-PackageVersion {
  if ($PackageVersion) {
    return $PackageVersion.TrimStart("v")
  }
  $tauriConfig = Get-Content -LiteralPath (Join-Path $Root "desktop\src-tauri\tauri.conf.json") -Raw |
    ConvertFrom-Json
  return ([string]$tauriConfig.version).TrimStart("v")
}

function Resolve-InnoSetupCompiler {
  if ($InnoSetupCompiler) {
    if (-not (Test-Path $InnoSetupCompiler)) {
      throw "Inno Setup compiler not found: $InnoSetupCompiler"
    }
    return (Resolve-Path $InnoSetupCompiler).Path
  }

  $cmd = Get-Command "iscc.exe" -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }

  $candidates = @(
    (Join-Path ([Environment]::GetFolderPath("ProgramFilesX86")) "Inno Setup 6\ISCC.exe"),
    (Join-Path ([Environment]::GetFolderPath("ProgramFiles")) "Inno Setup 6\ISCC.exe")
  )
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) {
      return $candidate
    }
  }

  throw "Inno Setup compiler (ISCC.exe) was not found. Install Inno Setup 6 or pass -InnoSetupCompiler."
}

function Sign-FileIfConfigured([string]$Path) {
  $certPath = $env:WINDOWS_SIGN_CERT_PATH
  if (-not $certPath) {
    Write-Host "Skipping Windows installer signing (set WINDOWS_SIGN_CERT_PATH to sign)."
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

  Write-Host "Signing Windows installer: $Path"
  & $signTool @args
  & $signTool verify /pa /v $Path
}

if (-not (Test-Path $Template)) {
  throw "Inno Setup template not found: $Template"
}

if (-not $PortableRoot) {
  $PortableRoot = Join-Path $Root "$OutputRoot\$PackageName"
}

if (-not $SkipPortableBuild) {
  $portableScript = Join-Path $PSScriptRoot "make-portable.ps1"
  $portableArgs = @(
    "-Configuration", $Configuration,
    "-OutputRoot", $OutputRoot,
    "-PackageName", $PackageName
  )
  if ($PackageVersion) { $portableArgs += @("-PackageVersion", $PackageVersion) }
  if ($SkipTauriBuild) { $portableArgs += "-SkipTauriBuild" }
  if ($CpuOnly) { $portableArgs += "-CpuOnly" }
  if ($StripVenv) { $portableArgs += "-StripVenv" }
  & $portableScript @portableArgs
}

$PortableRoot = (Resolve-Path $PortableRoot).Path
$exePath = Join-Path $PortableRoot "LayerLab.exe"
foreach ($required in @($exePath, (Join-Path $PortableRoot "LICENSE"), (Join-Path $PortableRoot "NOTICE"), (Join-Path $PortableRoot "THIRD_PARTY_NOTICES.txt"))) {
  if (-not (Test-Path $required)) {
    throw "Required portable artifact is missing: $required"
  }
}

New-Item -ItemType Directory -Force $InstallerOutputRoot | Out-Null

$version = Get-PackageVersion
$installerBaseName = "$PackageName-Setup"
$installerPath = Join-Path $InstallerOutputRoot "$installerBaseName.exe"
$checksumPath = "$installerPath.sha256"
if (Test-Path $installerPath) {
  Remove-Item -Force $installerPath
}
if (Test-Path $checksumPath) {
  Remove-Item -Force $checksumPath
}

$iscc = Resolve-InnoSetupCompiler
& $iscc `
  "/DAppVersion=$version" `
  "/DSourceDir=$PortableRoot" `
  "/DOutputDir=$InstallerOutputRoot" `
  "/DOutputBaseFilename=$installerBaseName" `
  $Template

if (-not (Test-Path $installerPath)) {
  throw "Installer was not created: $installerPath"
}

Sign-FileIfConfigured $installerPath

$hash = Get-FileHash -Algorithm SHA256 $installerPath
Set-Content -Path $checksumPath -Value "$($hash.Hash)  $installerBaseName.exe"

$variant = if ($CpuOnly) { "CPU-only" } else { "CUDA/GPU (NVIDIA)" }
Write-Host "Variant        : $variant"
Write-Host "Portable root  : $PortableRoot"
Write-Host "Installer      : $installerPath"
Write-Host "Checksum       : $checksumPath"
