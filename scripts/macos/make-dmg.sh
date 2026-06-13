#!/usr/bin/env bash
set -euo pipefail

ARCH="${ARCH:-arm64}"
VERSION="${VERSION:-LOCAL_DEV_TEST}"
VERSION="${VERSION#v}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="${REPO_ROOT}/.build"
DIST_DIR="${BUILD_DIR}/macos-dist"
DMG_STAGING="${BUILD_DIR}/dmg-staging-${ARCH}"
DMG_NAME="STEMDECK-Enhanced-macOS-${ARCH}.dmg"
DMG_PATH="${DIST_DIR}/${DMG_NAME}"
DMG_RW_PATH="${DIST_DIR}/STEMDECK-Enhanced-macOS-${ARCH}.rw.dmg"
RUNTIME_NAME="STEMDECK-Enhanced-runtime-macOS-${ARCH}.tar.zst"
RUNTIME_PATH="${BUILD_DIR}/${RUNTIME_NAME}"
BACKGROUND_SRC="${REPO_ROOT}/packaging/macos/dmg-background.svg"
BACKGROUND_DIR_NAME=".background"
BACKGROUND_PNG_NAME="dmg-background.png"
APP_BUNDLE_NAME="STEMDECK Enhanced.app"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "ERROR: make-dmg.sh must run on macOS" >&2
  exit 1
fi

if [[ "$ARCH" != "arm64" && "$ARCH" != "x64" ]]; then
  echo "ERROR: ARCH must be arm64 or x64, got '${ARCH}'" >&2
  exit 1
fi

for cmd in ditto hdiutil qlmanage shasum; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command not found on PATH: $cmd" >&2
    exit 1
  fi
done

if [[ "$ARCH" == "arm64" ]]; then
  APP_DIR="${REPO_ROOT}/desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/STEMDECK Enhanced.app"
else
  APP_DIR="${REPO_ROOT}/desktop/src-tauri/target/x86_64-apple-darwin/release/bundle/macos/STEMDECK Enhanced.app"
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "ERROR: built app not found: $APP_DIR" >&2
  echo "Run: ARCH=${ARCH} scripts/macos/make-app.sh" >&2
  exit 1
fi

if [[ ! -f "$RUNTIME_PATH" ]]; then
  echo "ERROR: runtime pack not found: $RUNTIME_PATH" >&2
  echo "Run: ARCH=${ARCH} VERSION=${VERSION} scripts/macos/make-runtime-pack.sh" >&2
  exit 1
fi

rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING" "$DIST_DIR"
mkdir -p "$DMG_STAGING/$BACKGROUND_DIR_NAME"

ditto --noextattr "$APP_DIR" "$DMG_STAGING/$APP_BUNDLE_NAME"
ln -s /Applications "$DMG_STAGING/Applications"

if [[ -f "$BACKGROUND_SRC" ]]; then
  qlmanage -t -s 1320 -o "$DMG_STAGING/$BACKGROUND_DIR_NAME" "$BACKGROUND_SRC" >/dev/null 2>&1
  mv "$DMG_STAGING/$BACKGROUND_DIR_NAME/dmg-background.svg.png" "$DMG_STAGING/$BACKGROUND_DIR_NAME/$BACKGROUND_PNG_NAME"
fi

if [[ -f "$REPO_ROOT/packaging/macos/README-macOS.txt" ]]; then
  cp "$REPO_ROOT/packaging/macos/README-macOS.txt" "$DMG_STAGING/README-macOS.txt"
fi

if [[ -f "$REPO_ROOT/packaging/macos/THIRD_PARTY_NOTICES.txt" ]]; then
  cp "$REPO_ROOT/packaging/macos/THIRD_PARTY_NOTICES.txt" "$DMG_STAGING/THIRD_PARTY_NOTICES.txt"
fi

if [[ -f "$REPO_ROOT/LICENSE" ]]; then
  cp "$REPO_ROOT/LICENSE" "$DMG_STAGING/LICENSE"
fi

if [[ -f "$REPO_ROOT/NOTICE" ]]; then
  cp "$REPO_ROOT/NOTICE" "$DMG_STAGING/NOTICE"
fi

rm -f "$DMG_PATH" "$DMG_RW_PATH"
hdiutil create \
  -volname "STEMDECK Enhanced" \
  -srcfolder "$DMG_STAGING" \
  -ov \
  -format UDRW \
  "$DMG_RW_PATH"

MOUNT_DIR="$(mktemp -d /tmp/stemdeck-dmg.XXXXXX)"
cleanup_mount() {
  hdiutil detach "$MOUNT_DIR" >/dev/null 2>&1 || true
  rmdir "$MOUNT_DIR" >/dev/null 2>&1 || true
}
trap cleanup_mount EXIT

hdiutil attach "$DMG_RW_PATH" -readwrite -noverify -nobrowse -mountpoint "$MOUNT_DIR" >/dev/null

if command -v SetFile >/dev/null 2>&1; then
  SetFile -a V "$MOUNT_DIR/$BACKGROUND_DIR_NAME" || true
  SetFile -a V "$MOUNT_DIR/README-macOS.txt" || true
  SetFile -a V "$MOUNT_DIR/THIRD_PARTY_NOTICES.txt" || true
  SetFile -a V "$MOUNT_DIR/LICENSE" || true
  SetFile -a V "$MOUNT_DIR/NOTICE" || true
fi

if [[ -f "$MOUNT_DIR/$BACKGROUND_DIR_NAME/$BACKGROUND_PNG_NAME" ]]; then
  osascript <<APPLESCRIPT || echo "warning: DMG window styling failed (cosmetic only, DMG is still valid)"
tell application "Finder"
  set dmgFolder to POSIX file "$MOUNT_DIR" as alias
  open dmgFolder
  set current view of container window of dmgFolder to icon view
  set toolbar visible of container window of dmgFolder to false
  set statusbar visible of container window of dmgFolder to false
  set bounds of container window of dmgFolder to {100, 100, 760, 500}
  set viewOptions to icon view options of container window of dmgFolder
  set arrangement of viewOptions to not arranged
  set icon size of viewOptions to 104
  set text size of viewOptions to 13
  set background picture of viewOptions to POSIX file "$MOUNT_DIR/$BACKGROUND_DIR_NAME/$BACKGROUND_PNG_NAME"
  set position of item "$APP_BUNDLE_NAME" of dmgFolder to {205, 205}
  set position of item "Applications" of dmgFolder to {455, 205}
  close container window of dmgFolder
  open dmgFolder
  update dmgFolder without registering applications
  delay 1
  close container window of dmgFolder
end tell
APPLESCRIPT
fi

if [[ -d "$MOUNT_DIR/$APP_BUNDLE_NAME" ]]; then
  # Finder window styling and filesystem copies can attach FinderInfo/resource
  # xattrs that strict codesign rejects. Strip them after styling, then
  # re-sign the app inside the mounted image when signing is configured.
  xattr -cr "$MOUNT_DIR/$APP_BUNDLE_NAME" >/dev/null 2>&1 || true
  if [[ -n "${APPLE_SIGNING_IDENTITY:-}" ]]; then
    echo "==> Signing mounted app with identity: ${APPLE_SIGNING_IDENTITY}"
    app_codesign_args=(--force --deep --options runtime)
    if [[ -n "${APPLE_ENTITLEMENTS:-}" ]]; then
      if [[ ! -f "$APPLE_ENTITLEMENTS" ]]; then
        echo "ERROR: APPLE_ENTITLEMENTS does not exist: $APPLE_ENTITLEMENTS" >&2
        exit 1
      fi
      app_codesign_args+=(--entitlements "$APPLE_ENTITLEMENTS")
    fi
    if [[ "${APPLE_CODESIGN_TIMESTAMP:-1}" == "1" ]]; then
      app_codesign_args+=(--timestamp)
    fi
    app_codesign_args+=(--sign "$APPLE_SIGNING_IDENTITY" "$MOUNT_DIR/$APP_BUNDLE_NAME")
    codesign "${app_codesign_args[@]}"
    codesign --verify --deep --strict --verbose=2 "$MOUNT_DIR/$APP_BUNDLE_NAME"
  fi
fi

sync
hdiutil detach "$MOUNT_DIR" >/dev/null
rmdir "$MOUNT_DIR"
trap - EXIT

hdiutil convert "$DMG_RW_PATH" -format UDZO -imagekey zlib-level=9 -o "$DMG_PATH" >/dev/null
rm -f "$DMG_RW_PATH"

if [[ -n "${APPLE_SIGNING_IDENTITY:-}" ]]; then
  echo "==> Signing DMG with identity: ${APPLE_SIGNING_IDENTITY}"
  dmg_codesign_args=(--force)
  if [[ "${APPLE_CODESIGN_TIMESTAMP:-1}" == "1" ]]; then
    dmg_codesign_args+=(--timestamp)
  fi
  dmg_codesign_args+=(--sign "$APPLE_SIGNING_IDENTITY" "$DMG_PATH")
  codesign "${dmg_codesign_args[@]}"
  codesign --verify --verbose=2 "$DMG_PATH"
else
  echo "==> Skipping DMG signing (set APPLE_SIGNING_IDENTITY to sign)"
fi

if [[ "${APPLE_NOTARIZE:-0}" == "1" ]]; then
  echo "==> Submitting DMG for notarization"
  if ! command -v xcrun >/dev/null 2>&1; then
    echo "ERROR: xcrun is required for notarization" >&2
    exit 1
  fi
  if [[ -n "${APPLE_NOTARY_KEYCHAIN_PROFILE:-}" ]]; then
    xcrun notarytool submit "$DMG_PATH" \
      --keychain-profile "$APPLE_NOTARY_KEYCHAIN_PROFILE" \
      --wait
  else
    : "${APPLE_ID:?APPLE_ID is required when APPLE_NOTARY_KEYCHAIN_PROFILE is not set}"
    : "${APPLE_TEAM_ID:?APPLE_TEAM_ID is required when APPLE_NOTARY_KEYCHAIN_PROFILE is not set}"
    : "${APPLE_APP_SPECIFIC_PASSWORD:?APPLE_APP_SPECIFIC_PASSWORD is required when APPLE_NOTARY_KEYCHAIN_PROFILE is not set}"
    xcrun notarytool submit "$DMG_PATH" \
      --apple-id "$APPLE_ID" \
      --team-id "$APPLE_TEAM_ID" \
      --password "$APPLE_APP_SPECIFIC_PASSWORD" \
      --wait
  fi
  xcrun stapler staple "$DMG_PATH"
  xcrun stapler validate "$DMG_PATH"
else
  echo "==> Skipping notarization (set APPLE_NOTARIZE=1 to notarize)"
fi

CHECKSUMS_PATH="${DIST_DIR}/SHA256SUMS-macOS-${ARCH}.txt"
{
  shasum -a 256 "$DMG_PATH"
  shasum -a 256 "$RUNTIME_PATH"
} | sed "s#${REPO_ROOT}/##" > "$CHECKSUMS_PATH"

echo "==> DMG ready: $DMG_PATH"
echo "==> Runtime pack: $RUNTIME_PATH"
echo "==> Checksums: $CHECKSUMS_PATH"
