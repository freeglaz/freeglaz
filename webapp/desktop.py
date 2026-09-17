"""freeglaz — desktop app (native pywebview window).

Wraps the EXISTING FastAPI backend (`webapp.backend.main:app`, which already
serves the API + built static frontend) in a native window, WITHOUT a browser
or Node at runtime:

  1. pick a FREE port (fallback 8765);
  2. start uvicorn in a daemon thread (programmatic Server → clean shutdown);
  3. wait for `/api/health` (reuses the .app's wait logic);
  4. open the pywebview window on the backend (GUI loop = MAIN thread);
  5. on window close → clean backend shutdown.

Launch: ``uv run python -m webapp.desktop`` (options ``--mock``, ``--port``).
Optional dependency: ``uv sync --extra desktop`` (pywebview). Do NOT run
``uv pip install -e``: the project is VIRTUAL for uv (no [build-system]), an
editable build would fail on flat-layout auto-discovery. On Linux, the WebKitGTK
system libraries are required as well (cf. install script).

Modifies NEITHER the front/back coupling, NOR lib/z9_client, NOR freeglaz: pure
wrapper layer above the FastAPI app.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import uuid
from pathlib import Path

# ── Linux: GTK/WebKit env vars set BEFORE any GTK/webview init ──
# ORDER MATTERS: GTK reads these variables at its initialization, triggered by
# `import webview` (in main()). Setting them at MODULE level (run before main)
# guarantees they precede that init.
#
# Observed on Fedora 44 + a recent GPU on a FREE driver (NVK/Zink, e.g. RTX 5080):
#   - without GDK_BACKEND=x11: the window CRASHES ("Gdk-Message: Error 71
#     (Protocol error) … dispatching to Wayland display" — WebKitGTK/pywebview +
#     native Wayland conflict). Forcing X11 (XWayland) works around it.
#   - without WEBKIT_DISABLE_COMPOSITING_MODE=1: the startup animation GLITCHES
#     (tearing/flicker — accelerated WebKit compositing via Zink/NVK misses its
#     sync). Disabling it = clean rendering.
# setdefault (NOT an override) → an advanced user can force Wayland or re-enable
# compositing by exporting the variable (e.g. proprietary NVIDIA driver).
# Meaningless on macOS (WKWebView/Cocoa, not GTK) → Linux ONLY.
if sys.platform.startswith("linux"):
    os.environ.setdefault("GDK_BACKEND", "x11")
    os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

logger = logging.getLogger("freeglaz.desktop")

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
HEALTH_TIMEOUT_S = 15.0
# Short: these calls are local and must not stall a launch. The upload is the one
# exception (a 16-bit TIFF is easily hundreds of MB) and carries its own timeout.
HANDOFF_TIMEOUT_S = 2.0


# ---------------------------------------------------------------- single instance
# freeglaz shows ONE image at a time by construction, so a second launch carrying a
# file must NOT open a second window: it hands the file to the running one and exits.
#
# The running instance advertises its port in a file because the port is not fixed —
# _free_port falls back to an ephemeral one when 8765 is taken, so probing 8765
# blindly would miss it. XDG_RUNTIME_DIR is per-user, cleared on logout, and inside
# Flatpak it is shared by every instance of the same app id, which makes it the right
# home for this advert.


def _instance_file() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(base) / "freeglaz-instance.json"


# Identifies THIS process in the advert. Not the pid: under Flatpak every instance
# runs in its own pid namespace and sees itself as pid 2, so pids collide across
# instances and cannot tell whose advert is whose. Liveness is never inferred from
# this token either — _handoff probes the port over HTTP instead.
_INSTANCE_TOKEN = uuid.uuid4().hex


def _publish_instance(port: int) -> None:
    """Advertise this instance so a later launch can find it. Best-effort: losing
    the advert only costs the handoff, never the launch."""
    try:
        _instance_file().write_text(
            json.dumps({"port": port, "token": _INSTANCE_TOKEN}), encoding="utf-8")
    except OSError as exc:
        logger.info("Instance advert not written (%s) — handoff disabled.", exc)


def _withdraw_instance() -> None:
    """Drop our advert on the way out — and only ours, since a newer instance may
    already have replaced it."""
    try:
        path = _instance_file()
        if json.loads(path.read_text(encoding="utf-8")).get("token") == _INSTANCE_TOKEN:
            path.unlink()
    except (OSError, ValueError, TypeError):
        pass


def _handoff(path: str) -> bool:
    """Give ``path`` to an already-running desktop instance.

    True means the running window took it and the caller must exit without starting
    anything. A stale advert (the app crashed, or that port now belongs to something
    else) just returns False and the normal launch proceeds.

    THIS process reads the bytes and uploads them rather than passing the path along:
    under Flatpak the file arrives as a document-portal path
    (/run/user/N/doc/<hash>/…) granted to *this* launch, and betting on it being
    visible from the other sandbox instance would be fragile. Uploading through the
    ordinary /api/files avoids the question entirely."""
    try:
        port = int(json.loads(_instance_file().read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, TypeError, KeyError):
        return False
    try:
        import requests

        base = f"http://{HOST}:{port}"
        # It must be a DESKTOP instance: a bare web server has no window to show it in.
        probe = requests.get(f"{base}/api/desktop/instance", timeout=HANDOFF_TIMEOUT_S)
        if not probe.ok or not probe.json().get("desktop"):
            return False
        name = os.path.basename(path)
        with open(path, "rb") as fh:
            up = requests.post(f"{base}/api/files",
                               files={"file": (name, fh, "image/tiff")}, timeout=300)
        fid = up.json().get("file_id") if up.ok else None
        if not fid:
            logger.warning("Handoff rejected %s: %s", path, up.text[:200])
            return False
        opened = requests.post(f"{base}/api/desktop/open",
                               json={"file_id": fid, "name": name},
                               timeout=HANDOFF_TIMEOUT_S)
        return bool(opened.ok)
    except Exception as exc:  # noqa: BLE001 — any failure falls back to a normal launch
        logger.info("Handoff unavailable (%s: %s) — launching normally.",
                    type(exc).__name__, exc)
        return False


def _free_port(preferred: int = DEFAULT_PORT) -> int:
    """Return a free port on the local host.

    Tries ``preferred`` first (consistency with `freeglaz web`); if it is taken,
    lets the OS pick a free ephemeral port (bind on 0)."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((HOST, candidate))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("No free port available on 127.0.0.1")


def _set_macos_dock_icon() -> None:
    """macOS only, best-effort: set the freeglaz logo as the Dock icon.

    pywebview's ``icon=`` parameter is a no-op on the Cocoa backend; we therefore
    set the icon at runtime via PyObjC, once ``NSApplication`` is alive. Called by
    ``webview.start(func=…)`` (i.e. AFTER pywebview has initialized its Cocoa app —
    setting it earlier would not take). PyObjC (``AppKit``) is pulled in by the
    desktop extra on macOS.

    Purely COSMETIC: any failure (AppKit missing, PNG not found, NSApplication
    unavailable, timing) is swallowed and logged at debug — launching the window
    NEVER depends on it.
    """
    if sys.platform != "darwin":
        return
    try:
        from pathlib import Path

        import AppKit

        png = Path(__file__).resolve().parent / "icons" / "freeglaz-1024.png"
        image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(png))
        if image is None:
            logger.debug("Dock icon: PNG not found/unreadable (%s).", png)
            return
        AppKit.NSApplication.sharedApplication().setApplicationIconImage_(image)
        logger.debug("Dock icon: freeglaz logo set.")
    except Exception as exc:  # noqa: BLE001 — cosmetic, never blocking
        logger.debug("Dock icon not set (%s: %s).", type(exc).__name__, exc)


def _set_macos_app_name(name: str = "freeglaz") -> None:
    """macOS only, best-effort: app name (menu + Dock tooltip) instead of
    ``python3.13``.

    The Python bundle has no ``CFBundleName`` → the Dock/menu falls back on the
    executable name. We therefore patch the current bundle's ``CFBundleName`` +
    ``NSProcessInfo`` at runtime. Call BEFORE ``webview.start()``: Cocoa builds the
    app menu at startup, a late patch would not take.

    Purely COSMETIC: any failure is swallowed (debug log), launching never depends
    on it. "freeglaz" in lowercase (the stylized Z is reserved for the logo).
    """
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle, NSProcessInfo

        info = (NSBundle.mainBundle().localizedInfoDictionary()
                or NSBundle.mainBundle().infoDictionary())
        if info is not None:
            info["CFBundleName"] = name
        try:
            NSProcessInfo.processInfo().setProcessName_(name)
        except Exception:  # noqa: BLE001 — secondary, ignored if unavailable
            pass
        logger.debug("macOS app name set: %s.", name)
    except Exception as exc:  # noqa: BLE001 — cosmetic, never blocking
        logger.debug("App name not set (%s: %s).", type(exc).__name__, exc)


def _install_macos_open_handler(api, window) -> None:
    """macOS only, best-effort: handle a TIFF dropped on the Dock icon (or
    "Open With…" / a launch-by-open).

    A file dropped on the app icon reaches the app as an "open documents" request
    that AppKit routes to the NSApplication delegate's ``application:openFile:``.
    pywebview's delegate does not implement it, so AppKit shows a "cannot open
    files in that format" dialog.

    We add ``application:openFile:`` to pywebview's delegate CLASS (not the
    instance). An earlier attempt swapped the delegate INSTANCE for a proxy, but
    pywebview re-sets its own delegate during startup on the main thread — racing
    the (background-thread) install and often winning, so the proxy was dropped
    and the dialog came back. Adding the method to the class is immune to that:
    whichever instance of that class is the delegate, AppKit finds the method.

    On the drop the method stores the path on ``api`` and fires the payload-less
    ``freeglaz:open-file`` event; the frontend pulls the bytes via
    ``api.take_dropped_file()``. Info.plist declares the TIFF document type (see
    freeglaz.spec) so the Dock accepts the drop.

    Best-effort: any failure is swallowed; launching the window NEVER depends on
    it. PyObjC is pulled in by the desktop extra on macOS. Non-macOS is a no-op."""
    if sys.platform != "darwin":
        return
    try:
        import threading
        import time

        import objc
        from AppKit import NSApplication

        def _deliver(path) -> None:
            if not path:
                return
            logger.info("Dock open-file: received %s", path)
            # application:openFile: runs on the MAIN thread. window.evaluate_js
            # is BLOCKING (it posts JS to the WKWebView on the main thread and
            # waits) → calling it here would deadlock the main thread on itself
            # (endless beachball). Store the path (instant) and fire the notify
            # from a background thread so this callback returns to AppKit at once.
            api._pending_drop = str(path)

            def _notify() -> None:
                try:
                    window.evaluate_js(
                        "window.dispatchEvent(new CustomEvent('freeglaz:open-file'))")
                except Exception as exc:  # noqa: BLE001 — best-effort
                    logger.debug("open-file notify failed (%s: %s)",
                                 type(exc).__name__, exc)

            threading.Thread(target=_notify, daemon=True).start()

        def _application_openFile_(self, sender, filename):  # noqa: N802
            _deliver(filename)
            return True

        app = NSApplication.sharedApplication()

        # func may run before pywebview has set its app delegate → poll briefly.
        delegate = None
        for _ in range(100):          # ~10 s at most
            delegate = app.delegate()
            if delegate is not None:
                break
            time.sleep(0.1)
        if delegate is None:
            logger.info("Dock open-file: no app delegate after wait; not installed.")
            return

        cls = type(delegate)
        if delegate.respondsToSelector_("application:openFile:"):
            logger.info("Dock open-file: %s already handles openFile.", cls.__name__)
        else:
            # BOOL return + (NSApplication*, NSString*) args → "c@:@@".
            objc.classAddMethods(cls, [
                objc.selector(_application_openFile_,
                              selector=b"application:openFile:",
                              signature=b"c@:@@"),
            ])
            logger.info("Dock open-file: handler added to delegate class %s.",
                        cls.__name__)
        # Keep the callable referenced (it closes over api/window/_deliver).
        api._open_file_impl = _application_openFile_
    except Exception as exc:  # noqa: BLE001 — best-effort, never blocking
        logger.info("Dock open-file: not installed (%s: %s).",
                    type(exc).__name__, exc)


class _DesktopFileApi:
    """JS-exposed API (``window.pywebview.api``) for NATIVE file I/O in the desktop
    window.

    Rationale (#22): letting the WebKitGTK/WKWebView webview navigate to a
    ``blob:``/attachment URL replaces the whole page with the file content and
    FREEZES the app. In desktop we therefore route every export through a native
    SAVE dialog (Python writes the bytes) and expose an OPEN dialog. In browser
    mode ``window.pywebview`` is absent → the frontend keeps its standard
    ``<a download>`` / ``<input type=file>`` path (which works there).
    """

    def __init__(self) -> None:
        self._window = None   # set right after create_window (create_file_dialog is a window method)
        self._pending_drop = None  # path of a file dropped on the Dock icon (macOS), pulled by the frontend

    def save_file(self, filename: str, content_b64: str):
        """Write base64 ``content_b64`` to a user-chosen path (native SAVE dialog).
        Returns the saved path, or ``None`` if cancelled/failed."""
        import base64
        import webview
        try:
            data = base64.b64decode(content_b64 or "")
        except Exception:  # noqa: BLE001 — never crash the app on a bad payload
            return None
        win = self._window
        if win is None:
            return None
        result = win.create_file_dialog(webview.SAVE_DIALOG, save_filename=filename)
        # SAVE_DIALOG → path str (or None); some backends wrap it in a 1-tuple.
        path = result if isinstance(result, str) else (result[0] if result else None)
        if not path:
            return None
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as exc:
            logger.warning("save_file failed (%s): %s", path, exc)
            return None
        return path

    def take_dropped_file(self):
        """Return ``{'name','content_b64'}`` for a file dropped on the Dock icon
        (macOS), then clear it. ``None`` if there is no pending drop.

        Same payload shape as ``open_file``: the frontend reuses its load path.
        The heavy bytes travel through the js_api bridge (like ``open_file``),
        not through an injected JS string — the notification event carries no
        payload (see ``_install_macos_open_handler`` in this module)."""
        import base64
        import os
        path = self._pending_drop
        self._pending_drop = None
        if not path:
            return None
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            logger.warning("take_dropped_file failed (%s): %s", path, exc)
            return None
        return {"name": os.path.basename(path),
                "content_b64": base64.b64encode(data).decode("ascii")}

    def open_file(self):
        """Open a file (native OPEN dialog). Returns ``{'name','content_b64'}`` or ``None``."""
        import base64
        import os
        import webview
        win = self._window
        if win is None:
            return None
        result = win.create_file_dialog(webview.OPEN_DIALOG)   # → tuple of paths (or None)
        path = result[0] if result else None
        if not path:
            return None
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            logger.warning("open_file failed (%s): %s", path, exc)
            return None
        return {"name": os.path.basename(path),
                "content_b64": base64.b64encode(data).decode("ascii")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="freeglaz-desktop", description="freeglaz — native window (pywebview)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Preferred port (default {DEFAULT_PORT}; auto-free if taken)")
    parser.add_argument("--mock", action="store_true",
                        help="Mock mode (no Z9 send) — FREEGLAZ_MOCK_PRINT=1")
    parser.add_argument("file", nargs="?", default=None,
                        help="Optional TIFF to open on launch (file manager "
                             "'Open With…' passes it here — Linux/Flatpak).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)

    # A second launch carrying a file gives it to the window already open and exits
    # (single-image by construction — see _handoff). --mock is excluded: it would
    # silently land in a live instance and print for real.
    if args.file and not args.mock and _handoff(args.file):
        logger.info("Handed %s to the running instance.", args.file)
        return 0

    if args.mock:
        os.environ["FREEGLAZ_MOCK_PRINT"] = "1"
        logger.info("Mock mode enabled (no send to the Z9).")

    # pywebview is optional → clear message if it is missing.
    try:
        import webview
    except ImportError:
        print("pywebview missing. Install the desktop path:\n"
              "  uv sync --extra desktop\n"
              "(Linux: also install the WebKitGTK system libs — cf. install script.)")
        return 2

    # Server primitives SHARED with the CLI (dedup: former `_build_server` /
    # former `_wait_health` → `run_background` / `wait_until_up`). run_background
    # starts uvicorn in a daemon thread (programmatic Server, signal handlers off)
    # and returns (server, thread); clean shutdown via `should_exit`.
    from webapp.backend.main import run_background, wait_until_up

    # _free_port keeps the ephemeral-port fallback (desktop-specific): we pick a
    # FREE port before starting, so run_background will not raise.
    port = _free_port(args.port)
    server, t = run_background(host=HOST, port=port)

    if not wait_until_up(HOST, port, timeout=HEALTH_TIMEOUT_S, interval=0.25):
        server.should_exit = True
        print(f"Backend did not start within {HEALTH_TIMEOUT_S:.0f}s — aborting.")
        return 1

    logger.info("Backend ready on http://%s:%d — opening the window.", HOST, port)
    _publish_instance(port)
    # macOS: app name (menu + Dock tooltip) BEFORE Cocoa builds its menu.
    _set_macos_app_name()
    # Maximized by default: the app needs width (1280 is not enough to show
    # everything). width/height = "restored" size if the user un-maximizes.
    # js_api = native file I/O exposed as window.pywebview.api (save/open dialogs)
    # → exports never navigate the webview (the app-freezing bug #22). Browser mode
    # never sees this object (window.pywebview absent).
    api = _DesktopFileApi()
    # Open-on-launch (file manager "Open With…", Linux/Flatpak): upload the file
    # and boot the window straight onto it, reusing the existing ?file_id=… path
    # (same as `freeglaz open`). Best-effort — a failure just opens the app empty.
    boot_query = ""
    if args.file:
        try:
            import requests
            with open(args.file, "rb") as f:
                resp = requests.post(
                    f"http://{HOST}:{port}/api/files",
                    files={"file": (os.path.basename(args.file), f, "image/tiff")},
                    timeout=30)
            fid = resp.json().get("file_id") if resp.ok else None
            if fid:
                from urllib.parse import quote
                boot_query = f"?file_id={fid}&name={quote(os.path.basename(args.file))}"
            else:
                logger.warning("Open-on-launch rejected %s: %s", args.file, resp.text[:200])
        except Exception as exc:  # noqa: BLE001 — never block launch on this
            logger.warning("Open-on-launch failed (%s): %s", args.file, exc)
    window = webview.create_window("freeglaz Print", f"http://{HOST}:{port}/{boot_query}",
                                   width=1600, height=1000, min_size=(1024, 700),
                                   maximized=True, js_api=api)
    api._window = window

    # Single instance: let a LATER launch navigate this window (routes/desktop.py).
    from webapp.backend.routes import desktop as desktop_routes

    def _open_in_window(file_id: str, name: str) -> None:
        from urllib.parse import quote
        url = f"http://{HOST}:{port}/?file_id={quote(file_id)}&name={quote(name)}"

        def _apply() -> None:
            try:
                window.load_url(url)
                # Wayland/GNOME forbids an app raising itself, so this may only
                # flag the taskbar icon rather than come to the front.
                window.restore()
            except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
                logger.warning("Window navigation failed (%s): %s",
                               type(exc).__name__, exc)

        # Off the request thread: the HTTP reply must never wait on the GUI.
        threading.Thread(target=_apply, daemon=True).start()

    desktop_routes.set_open_handler(_open_in_window)

    # BLOCKING GUI loop on the main thread; returns on window close.
    # debug=True (WebKit DevTools) if FREEGLAZ_DEBUG=1 — to capture the real stack
    # of a possible blank screen in the webview (WebKitGTK). Off by default (prod).
    debug = os.environ.get("FREEGLAZ_DEBUG", "").lower() in ("1", "true", "yes")
    if debug:
        logger.info("FREEGLAZ_DEBUG: WebKit DevTools enabled (right-click → Inspect).")
    # func= runs once the Cocoa app has started → the moment NSApplication is
    # alive to set the Dock icon AND register the Dock open-file handler (macOS).
    # No-op elsewhere; both are best-effort (swallow failures internally).
    def _on_cocoa_started():
        _set_macos_dock_icon()
        _install_macos_open_handler(api, window)

    webview.start(func=_on_cocoa_started, debug=debug)

    # Window close → clean backend shutdown.
    logger.info("Window closed — shutting down the backend.")
    desktop_routes.set_open_handler(None)
    _withdraw_instance()
    server.should_exit = True
    t.join(timeout=5.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
