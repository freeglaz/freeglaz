#!/bin/sh
# freeglaz launcher inside the Flatpak sandbox.
#
# - PYTHONPATH: the freeglaz source (lib/ + webapp/) lives under /app/share.
# - FREEGLAZ_ARGYLL_ROOT: the bundled ArgyllCMS (bin/ + ref/).
# - FREEGLAZ_DATA_DIR: writable runtime state → the sandbox's XDG data dir
#   (the package dir under /app is read-only). User-facing profiles keep living
#   in ~/Documents/freeglaz (granted via --filesystem=xdg-documents).
export PYTHONPATH="/app/share/freeglaz${PYTHONPATH:+:$PYTHONPATH}"
export FREEGLAZ_ARGYLL_ROOT="/app/argyll"
export FREEGLAZ_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/freeglaz"
exec python3 -m webapp.desktop "$@"
