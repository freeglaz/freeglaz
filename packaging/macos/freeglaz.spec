# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — freeglaz macOS .app (Phase 1, UNSIGNED).
#
# Bundles the Python interpreter, all dependencies, the built frontend and the
# reference ICC assets into a double-clickable .app. ArgyllCMS is NOT bundled
# (external system dependency, auto-detected at runtime).
#
# Build via packaging/macos/build_app.command (do not run pyinstaller by hand:
# the driver script syncs deps and builds the frontend first).
#
# The .app is unsigned. Clear the quarantine flag once after install:
#   xattr -dr com.apple.quarantine /Applications/freeglaz.app

import glob
import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is the directory of this spec (packaging/macos). Repo root is 2 up.
ROOT = Path(SPECPATH).resolve().parents[1]

# Locate pyvips_binary's SELF-CONTAINED libvips (only /usr/lib + system-
# framework deps). We bundle this one and load it in ABI mode; see
# rthook_libvips.py for why _libvips (API mode) is excluded instead.
# Resolve via pyvips' own location (find_spec does not import/init libvips):
# pyvips_binary.dylibs sits next to the pyvips package in site-packages, which
# is robust across uv's layered build environments (sysconfig points at the
# ephemeral overlay, not the project venv where the sidecar lives).
_pyvips_spec = importlib.util.find_spec("pyvips")
if _pyvips_spec is None or not _pyvips_spec.origin:
    raise SystemExit("pyvips not importable — run `uv sync --extra desktop` first.")
_SITE = Path(_pyvips_spec.origin).resolve().parent.parent  # .../site-packages
_LIBVIPS = glob.glob(str(_SITE / "pyvips_binary.dylibs" / "libvips.*.dylib"))
if not _LIBVIPS:
    raise SystemExit(
        "libvips sidecar not found under %s/pyvips_binary.dylibs — "
        "is pyvips installed with the [binary] extra?" % _SITE
    )

# ── Data files (resolved at runtime relative to the source tree) ──────────
# The backend serves webapp/frontend/dist and reads lib/z9_client/assets via
# Path(__file__)-relative lookups, which resolve inside the bundle when the
# same relative layout is preserved here.
datas = [
    (str(ROOT / "webapp" / "frontend" / "dist"), "webapp/frontend/dist"),
    (str(ROOT / "lib" / "z9_client" / "assets"), "lib/z9_client/assets"),
    (str(ROOT / "webapp" / "icons"), "webapp/icons"),
]
# Bundle the self-contained libvips at the support-dir root (Contents/Frameworks,
# i.e. sys._MEIPASS). The runtime hook adds that dir to DYLD_LIBRARY_PATH so
# pyvips' ABI-mode dlopen("libvips.42.dylib") resolves it.
binaries = [(_LIBVIPS[0], ".")]
hiddenimports = []

# Native/data-heavy or lazily-imported packages: collect libs + data + submodules.
# skimage → lazy submodule loading defeats static analysis without this.
for pkg in ("pyvips", "pikepdf", "pypdfium2", "skimage"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# uvicorn[standard]: the loop/protocol backends are imported by string.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "httptools",
    "websockets",
    "multipart",           # python-multipart, imported as `multipart`
    "sse_starlette",
    "lxml._elementpath",
    "tifffile",
]

# pywebview cocoa backend + PyObjC frameworks it dlopens.
hiddenimports += collect_submodules("webview")
hiddenimports += [
    "objc",
    "Foundation",
    "AppKit",
    "WebKit",
    "Quartz",
    "Security",
]

a = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(Path(SPECPATH) / "rthook_libvips.py")],
    # _libvips = pyvips' API extension. Excluded on purpose: on a machine with
    # Homebrew vips it links to the Homebrew libvips tree (fragile pango/
    # harfbuzz symbols once bundled). Excluding it forces pyvips into ABI mode
    # against the self-contained libvips we bundle instead.
    excludes=["_libvips", "tkinter", "pytest", "matplotlib"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="freeglaz",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed app (no terminal)
    disable_windowed_traceback=False,
    argv_emulation=False,   # we pass [] explicitly; no drag-and-drop needed
    target_arch=None,       # native arch of the build machine
    codesign_identity=None, # unsigned (Phase 1)
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="freeglaz",
)

app = BUNDLE(
    coll,
    name="freeglaz.app",
    icon=str(ROOT / "webapp" / "icons" / "freeglaz.icns"),
    bundle_identifier="org.freeglaz.app",
    info_plist={
        "CFBundleName": "freeglaz",
        "CFBundleDisplayName": "freeglaz",
        "CFBundleShortVersionString": "0.1.1",
        "CFBundleVersion": "0.1.1",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        # Single-window utility: no Dock-less agent, keep it a normal app.
        "LSApplicationCategoryType": "public.app-category.graphics-design",
    },
)
