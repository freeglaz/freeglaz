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
# Components shipped inside the app, each one optional (see freeglaz.spec):
#   FREEGLAZ_BUNDLE_ARGYLL=<extracted official ArgyllCMS macOS arm64 archive>
#   FREEGLAZ_BUNDLE_FGK=<folder: chartread-fgK signed alone + its manifest + source/>
# Without FREEGLAZ_BUNDLE_ARGYLL, ArgyllCMS stays an external dependency,
# installed separately (Homebrew) and auto-detected at runtime: the printing
# path works without it, only the open profiling path needs it. Without
# FREEGLAZ_BUNDLE_FGK, external measurement has no engine.
#
# The result is unsigned (no Apple Developer account required). After copying
# freeglaz.app to /Applications, clear the quarantine flag ONCE:
#   xattr -dr com.apple.quarantine /Applications/freeglaz.app
set -euo pipefail

# Repo root = two levels up from this script.
cd "$(cd "$(dirname "$0")/../.." && pwd)"
echo "==> Repo: $(pwd)"

# Bundle a uv-managed CPython (python-build-standalone), never the runner's
# system/Homebrew Python. The latter is built for the runner's own macOS and
# links the stdlib against /usr/lib (libexpat, …): v0.1.11 shipped a Python
# 3.14 framework with minos 26.0 whose pyexpat needs a libexpat symbol absent
# before macOS 26 — every XML read from the Z9 failed on Sequoia.
export UV_PYTHON_PREFERENCE=only-managed
export UV_PYTHON=3.13

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
# --with keeps PyInstaller out of the project venv (build-only tool). Pinned, as
# the release workflow records it; FREEGLAZ_PYINSTALLER overrides it.
PYINSTALLER_REQ="${FREEGLAZ_PYINSTALLER:-pyinstaller==6.22.3}"
echo "    ($PYINSTALLER_REQ)"
uv run --extra desktop --with "$PYINSTALLER_REQ" \
  pyinstaller --noconfirm --clean packaging/macos/freeglaz.spec \
    --distpath "$BUILD_ROOT/dist" --workpath "$BUILD_ROOT/build"

if [ ! -d "$APP" ]; then
  echo "!! Build did not produce $APP" >&2
  exit 1
fi

# Guard: no Mach-O in the bundle may require a newer macOS than the declared
# LSMinimumSystemVersion, nor link the system libexpat (the v0.1.11 failure).
# The app still launches in that case — only the broken module fails, at use.
MIN_MACOS=$(/usr/libexec/PlistBuddy -c "Print :LSMinimumSystemVersion" "$APP/Contents/Info.plist")
echo "==> Checking every binary targets macOS <= ${MIN_MACOS}…"
BAD=0
while IFS= read -r -d '' f; do
  file -b "$f" | grep -q "Mach-O" || continue
  minos=$(otool -l "$f" | awk '/LC_BUILD_VERSION/{b=1} b&&/minos/{print $2; exit}
                               /LC_VERSION_MIN_MACOSX/{v=1} v&&/ version/{print $2; exit}')
  if [ -n "$minos" ] && [ "$(printf '%s\n%s\n' "$MIN_MACOS" "$minos" | sort -V | tail -1)" != "$MIN_MACOS" ]; then
    echo "!! ${f#"$APP"/} requires macOS $minos" >&2
    BAD=1
  fi
  if otool -L "$f" | grep -q "/usr/lib/libexpat"; then
    echo "!! ${f#"$APP"/} links the system libexpat" >&2
    BAD=1
  fi
done < <(find "$APP" -type f \( -name "*.so" -o -name "*.dylib" -o -perm +111 \) -print0)
if [ "$BAD" -ne 0 ]; then
  echo "!! Bundle is not portable to macOS $MIN_MACOS — aborting." >&2
  exit 1
fi
echo "   all binaries OK"

# Apple Silicon refuses to launch an arm64 binary without a valid signature.
# An ad-hoc signature ("-s -") is free (no Apple Developer account) and is
# enough to run — it is NOT notarization (Gatekeeper still asks once, handled
# by the xattr step at install time).
echo "==> Ad-hoc signing the bundle…"
xattr -cr "$APP"
codesign -s - --force --deep "$APP"
codesign --verify --deep --strict "$APP" && echo "   signature OK"

# The engine is trusted by the sha256 of its bytes. Its manifest was written
# before bundling and is sealed by the bundle signature: the engine in the
# signed app must still be byte for byte the one the manifest describes (and
# the input), otherwise freeglaz would refuse it. Checked here, never repaired.
if [ -n "${FREEGLAZ_BUNDLE_FGK:-}" ]; then
  echo "==> Checking the chartread-fgK engine in the signed app…"
  ENG="$APP/Contents/Frameworks/chartread-fgK/chartread-fgK"
  [ -f "$ENG" ] || { echo "!! $ENG missing" >&2; exit 1; }
  IN_APP=$(shasum -a 256 "$ENG" | cut -d' ' -f1)
  INPUT=$(shasum -a 256 "$FREEGLAZ_BUNDLE_FGK/chartread-fgK" | cut -d' ' -f1)
  DECLARED=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' \
             "$APP/Contents/Frameworks/chartread-fgK/chartread-fgK.manifest.json")
  if [ "$IN_APP" != "$INPUT" ] || [ "$IN_APP" != "$DECLARED" ]; then
    echo "!! engine bytes changed by bundling: in app $IN_APP, input $INPUT, manifest $DECLARED" >&2
    exit 1
  fi
  echo "   engine sha256 $IN_APP (unchanged by bundling and signing)"
  if uv run python -c 'import sys; from lib.z9_client.chartread_fgk import engine as e; sys.exit(0 if e.trusted_build(sys.argv[1], "darwin", "arm64") else 1)' "$IN_APP"; then
    echo "   listed in TRUSTED_BUILDS: measurements can be published"
  else
    echo "   !! NOT listed in TRUSTED_BUILDS: development engine, publication refused" >&2
  fi
fi
if [ -n "${FREEGLAZ_BUNDLE_ARGYLL:-}" ]; then
  echo "==> ArgyllCMS tools in the app:"
  ls "$APP/Contents/Frameworks/argyll/bin" | sed 's/^/     /'
fi

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
if [ -z "${FREEGLAZ_BUNDLE_ARGYLL:-}" ]; then
  echo "   ArgyllCMS (for the open profiling path) is a separate install:"
  echo "     brew install argyll-cms"
fi
