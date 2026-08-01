#!/bin/sh
# freeglaz launcher inside the Flatpak sandbox.
#
# - PYTHONPATH: the freeglaz source (lib/ + webapp/) lives under /app/share.
# - FREEGLAZ_ARGYLL_ROOT: the bundled ArgyllCMS (bin/ + ref/).
# - FREEGLAZ_DATA_DIR: writable runtime state → the sandbox's XDG data dir
#   (the package dir under /app is read-only). User-facing profiles keep living
#   in ~/Documents/freeglaz (granted via --filesystem=xdg-documents).
# - GDK_BACKEND=wayland + WEBKIT_DISABLE_DMABUF_RENDERER=1: run natively under
#   Wayland. desktop.py forces GDK_BACKEND=x11 (setdefault) as a workaround for a
#   WebKitGTK Wayland crash on NVIDIA; disabling the DMA-BUF renderer fixes that
#   crash in the GNOME runtime, so we opt back into Wayland here and the manifest
#   needs only --socket=fallback-x11 (Flathub-preferred), not --socket=x11.
export PYTHONPATH="/app/share/freeglaz${PYTHONPATH:+:$PYTHONPATH}"
export FREEGLAZ_ARGYLL_ROOT="/app/argyll"
export FREEGLAZ_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/freeglaz"
export GDK_BACKEND=wayland
export WEBKIT_DISABLE_DMABUF_RENDERER=1
exec python3 -m webapp.desktop "$@"
