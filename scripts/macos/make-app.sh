#!/usr/bin/env bash
set -euo pipefail

ARCH="${ARCH:-arm64}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# Default the version to the current git tag so local builds match the tag with
# no VERSION passed; falls back to a dev label outside a tagged checkout. (#169)
VERSION="${VERSION:-$(git -C "$REPO_ROOT" describe --tags --always 2>/dev/null || echo 0.0.0-dev)}"
VERSION="${VERSION#v}"
BUILD_DIR="${REPO_ROOT}/.build"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "ERROR: make-app.sh must run on macOS" >&2
  exit 1
fi

if [[ "$ARCH" != "arm64" && "$ARCH" != "x64" ]]; then
  echo "ERROR: ARCH must be arm64 or x64, got '${ARCH}'" >&2
  exit 1
fi

for cmd in node npm cargo; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command not found on PATH: $cmd" >&2
    exit 1
  fi
done

mkdir -p "$BUILD_DIR"

echo "==> Stamping version ${VERSION}"
sed -i '' "s/^version = \".*\"/version = \"${VERSION}\"/" "$REPO_ROOT/desktop/src-tauri/Cargo.toml"
sed -i '' "s/\"version\": \".*\"/\"version\": \"${VERSION}\"/" "$REPO_ROOT/desktop/src-tauri/tauri.conf.json"
sed -i '' "s/\"version\": \".*\"/\"version\": \"${VERSION}\"/" "$REPO_ROOT/desktop/package.json"

cd "$REPO_ROOT/desktop"

# Tauri CLI reads CI env var as a boolean flag; Woodpecker sets CI=woodpecker
# which fails the boolean parser. Force a valid value.
export CI=true

npm ci
if [[ "$ARCH" == "arm64" ]]; then
  npx tauri build --bundles app --target aarch64-apple-darwin
  TARGET_DIR="$REPO_ROOT/desktop/src-tauri/target/aarch64-apple-darwin/release"
else
  npx tauri build --bundles app --target x86_64-apple-darwin
  TARGET_DIR="$REPO_ROOT/desktop/src-tauri/target/x86_64-apple-darwin/release"
fi

APP_DIR="${TARGET_DIR}/bundle/macos/STEMDECK Enhanced.app"
if [[ ! -d "$APP_DIR" ]]; then
  APP_DIR="$(find "$REPO_ROOT/desktop/src-tauri/target" -path '*/bundle/macos/STEMDECK Enhanced.app' -type d | head -1)"
fi
if [[ -z "$APP_DIR" || ! -d "$APP_DIR" ]]; then
  echo "ERROR: could not find built STEMDECK Enhanced.app" >&2
  exit 1
fi

RESOURCES="${APP_DIR}/Contents/Resources"
mkdir -p "$RESOURCES"

if [[ -f "$BUILD_DIR/runtime-manifest-${ARCH}.json" ]]; then
  cp "$BUILD_DIR/runtime-manifest-${ARCH}.json" "$RESOURCES/runtime-manifest.json"
else
  cp "$REPO_ROOT/desktop/ui/runtime-manifest.json" "$RESOURCES/runtime-manifest.json"
fi

if [[ -f "$REPO_ROOT/packaging/macos/THIRD_PARTY_NOTICES.txt" ]]; then
  cp "$REPO_ROOT/packaging/macos/THIRD_PARTY_NOTICES.txt" "$RESOURCES/THIRD_PARTY_NOTICES.txt"
fi

if [[ -f "$REPO_ROOT/LICENSE" ]]; then
  cp "$REPO_ROOT/LICENSE" "$RESOURCES/LICENSE"
fi

if [[ -f "$REPO_ROOT/NOTICE" ]]; then
  cp "$REPO_ROOT/NOTICE" "$RESOURCES/NOTICE"
fi

xattr -cr "$APP_DIR" >/dev/null 2>&1 || true

if [[ -n "${APPLE_SIGNING_IDENTITY:-}" ]]; then
  echo "==> Signing app with identity: ${APPLE_SIGNING_IDENTITY}"
  codesign_args=(--force --deep --options runtime)
  if [[ -n "${APPLE_ENTITLEMENTS:-}" ]]; then
    if [[ ! -f "$APPLE_ENTITLEMENTS" ]]; then
      echo "ERROR: APPLE_ENTITLEMENTS does not exist: $APPLE_ENTITLEMENTS" >&2
      exit 1
    fi
    codesign_args+=(--entitlements "$APPLE_ENTITLEMENTS")
  fi
  if [[ "${APPLE_CODESIGN_TIMESTAMP:-1}" == "1" ]]; then
    codesign_args+=(--timestamp)
  fi
  codesign_args+=(--sign "$APPLE_SIGNING_IDENTITY" "$APP_DIR")
  codesign "${codesign_args[@]}"
  codesign --verify --deep --strict --verbose=2 "$APP_DIR"
else
  echo "==> Skipping app signing (set APPLE_SIGNING_IDENTITY to sign)"
fi

echo "$APP_DIR" > "$BUILD_DIR/app-path-${ARCH}.txt"
echo "==> App ready: $APP_DIR"
