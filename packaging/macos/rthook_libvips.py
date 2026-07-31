# PyInstaller runtime hook — make the bundled libvips loadable (macOS).
#
# The .app ships pyvips_binary's self-contained libvips.42.dylib (its only deps
# are /usr/lib + system frameworks — no Homebrew pango/harfbuzz/glib). pyvips
# loads it in ABI mode via ``ffi.dlopen("libvips.42.dylib")`` (a bare leaf
# name), because the API extension ``_libvips`` is deliberately NOT bundled: on
# a machine with Homebrew vips it links to the Homebrew libvips tree, which has
# a fragile pango/harfbuzz symbol set that breaks once bundled.
#
# A bare-name dlopen searches DYLD_LIBRARY_PATH, which macOS re-reads at each
# dlopen (verified: setting it here, before pyvips imports, is honored). Point
# it at the bundle's support dir (sys._MEIPASS == Contents/Frameworks), where
# the spec places libvips.42.dylib.
import os
import sys

_meipass = getattr(sys, "_MEIPASS", None)
if _meipass:
    _prev = os.environ.get("DYLD_LIBRARY_PATH", "")
    os.environ["DYLD_LIBRARY_PATH"] = _meipass + (os.pathsep + _prev if _prev else "")
