#!/bin/bash
# Build a standalone, UNSIGNED freeglaz.app for macOS (Phase 1).
#
# Double-clickable (opens in Terminal) or runnable from a shell:
#   ./packaging/macos/build_app.command
#
# What it does:
#   1. syncs the Python env (desktop extra),
#   2. builds the frontend if dist/ is missing,
#   3. runs PyInstaller against packaging/macos/freeglaz.spec,
#   4. leaves dist/freeglaz.app (and a plain dist/freeglaz.dmg).
#
# ArgyllCMS is NOT bundled: it stays an external dependency, installed
# separately (Homebrew) and auto-detected at runtime. The printing path works
# without it; only the open profiling path needs it.
#
# The result is unsigned (no Apple Developer account required). After copying
# freeglaz.app to /Applications, clear the quarantine flag ONCE:
#   xattr -dr com.apple.quarantine /Applications/freeglaz.app
set -euo pipefail

# Repo root = two levels up from this script.
cd "$(cd "$(dirname "$0")/../.." && pwd)"
echo "==> Repo: $(pwd)"

echo "==> Syncing Python dependencies (desktop extra)…"
uv sync --extra desktop

if [ ! -f webapp/frontend/dist/index.html ]; then
  echo "==> Building the frontend (dist/ missing)…"
  ( cd webapp/frontend && npm ci && npm run build )
else
  echo "==> Frontend dist/ present — skipping npm build."
fi

# Build + sign OUTSIDE the source tree. If it sits in a File Provider folder
# (iCloud Drive / a sync client — common for ~/Documents), the provider daemon
# re-applies com.apple.fileprovider xattrs to the .app the instant they are
# stripped, and codesign then fails with "resource fork ... detritus not
# allowed". TMPDIR (/var/folders) is not file-provider managed, so signing
# there is clean. On a CI runner there is no file provider and this is simply
# a scratch build dir.
BUILD_ROOT="${TMPDIR:-/tmp}/freeglaz-pkg"
APP="$BUILD_ROOT/dist/freeglaz.app"
echo "==> Building freeglaz.app with PyInstaller (in $BUILD_ROOT)…"
rm -rf "$BUILD_ROOT"
# --with pyinstaller keeps it out of the project venv (build-only tool).
uv run --extra desktop --with pyinstaller \
  pyinstaller --noconfirm --clean packaging/macos/freeglaz.spec \
    --distpath "$BUILD_ROOT/dist" --workpath "$BUILD_ROOT/build"

if [ ! -d "$APP" ]; then
  echo "!! Build did not produce $APP" >&2
  exit 1
fi

# Apple Silicon refuses to launch an arm64 binary without a valid signature.
# An ad-hoc signature ("-s -") is free (no Apple Developer account) and is
# enough to run — it is NOT notarization (Gatekeeper still asks once, handled
# by the xattr step at install time).
echo "==> Ad-hoc signing the bundle…"
xattr -cr "$APP"
codesign -s - --force --deep "$APP"
codesign --verify --deep --strict "$APP" && echo "   signature OK"

# Place the signed artifacts in the repo's dist/ (gitignored). The signature
# seals the bundle CONTENTS, so it survives the copy even though the folder
# gets a fileprovider xattr back afterwards (verified: --verify still passes).
echo "==> Placing artifacts in dist/…"
rm -rf dist && mkdir -p dist
ditto "$APP" dist/freeglaz.app
rm -f dist/freeglaz.dmg
hdiutil create -volname freeglaz -srcfolder "$APP" \
  -ov -format UDZO dist/freeglaz.dmg >/dev/null

echo
echo "✅ Built:"
echo "     dist/freeglaz.app"
echo "     dist/freeglaz.dmg"
echo
echo "   Install: drag freeglaz.app to /Applications, then once:"
echo "     xattr -dr com.apple.quarantine /Applications/freeglaz.app"
echo
echo "   ArgyllCMS (for the open profiling path) is a separate install:"
echo "     brew install argyll-cms"
