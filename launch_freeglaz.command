#!/bin/bash
# freeglaz — double-click launcher for macOS (web app in the browser).
#
# It runs from inside the extracted freeglaz folder: it locates that folder from
# its own position (no hardcoded path), finds uv (Finder starts a process with a
# minimal PATH that excludes Homebrew and ~/.local, so uv must be detected
# explicitly), then launches `freeglaz web`. A Terminal window stays open while
# the server runs; closing it stops freeglaz. The browser opens on its own.
#
# First run only: macOS quarantines downloaded, unsigned software. Clear it once
# with `xattr -dr com.apple.quarantine "<this folder>"` — see
# Docs/install_bonus_macos.md.

# Locate the freeglaz folder from this script's own position.
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

# Find uv without relying on PATH.
if command -v uv >/dev/null 2>&1; then
    UV="$(command -v uv)"
elif [ -x /opt/homebrew/bin/uv ]; then     # Homebrew — Apple Silicon
    UV=/opt/homebrew/bin/uv
elif [ -x /usr/local/bin/uv ]; then        # Homebrew — Intel
    UV=/usr/local/bin/uv
elif [ -x "$HOME/.local/bin/uv" ]; then    # official install script
    UV="$HOME/.local/bin/uv"
else
    echo "uv was not found. Install it first — see Docs/INSTALL_tarball_macos.md."
    echo "Press Return to close this window."
    read -r _
    exit 1
fi

echo "Starting freeglaz — closing this window stops it."
"$UV" run ./freeglaz web
status=$?
if [ "$status" -ne 0 ]; then
    echo
    echo "freeglaz exited with code $status. Press Return to close."
    read -r _
fi
