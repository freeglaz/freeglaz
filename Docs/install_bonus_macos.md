# freeglaz on macOS — launching without the command line (bonus)

Optional setup for running freeglaz day to day without the Terminal. Two paths:

- **Native application (recommended).** A small `.app`, created once, that opens
  freeglaz in its own window (desktop mode) and carries the freeglaz icon in the
  Finder and the Dock. See §2.
- **Web launch (simple alternative).** The bundled `launch_freeglaz.command`
  opens freeglaz in the default browser — nothing to build. See §4.

A Finder right-click action that opens a TIFF straight into freeglaz is in §3.

freeglaz is not code-signed (no paid Apple Developer account), so macOS
quarantines it on first use. macOS Sequoia (15) removed the old right-click →
**Open** bypass, so one Terminal command clears the quarantine **once** (§1);
after that, launching is double-click only.

---

## 1. One-time authorization (clear the quarantine)

After extracting the release, run `xattr -dr com.apple.quarantine` once in
Terminal, **followed by the path of the extracted freeglaz folder**. Two ways to
supply that path:

- **Drag-and-drop (recommended).** Type the command with a trailing space, then
  drag the extracted freeglaz folder from the Finder into the Terminal window —
  macOS inserts the exact path, correctly escaped, wherever the folder lives.
  Then press Return:

  ```bash
  xattr -dr com.apple.quarantine     # then drag the folder here, then press Return
  ```

- **Typed path.** If the location is known, type it directly. Replace `<version>`
  and the location with the real values — the folder is not necessarily in
  `~/Downloads`:

  ```bash
  xattr -dr com.apple.quarantine ~/Downloads/freeglaz-<version>
  ```

  In the Finder, right-click the folder → **Copy “<folder>” as Pathname** (hold
  ⌥ Option to reveal the entry) copies the exact path to paste.

`xattr` removes the `com.apple.quarantine` attribute that Gatekeeper attaches to
downloaded, unsigned software. Without it, macOS refuses to run the launcher.
Once cleared, it stays cleared for that folder.

---

## 2. Native application (`.app`, recommended)

An Automator **Application** that opens freeglaz in its own window (desktop mode)
and carries the freeglaz icon. Created once; the freeglaz folder path is set at
creation, so it works from anywhere (Applications, Dock).

### Create the application (Automator)

1. Open **Automator** → **New Document** → **Application**.
2. Add **Run Shell Script**. Set *Shell* to `/bin/bash` (no input is used).
3. Paste the script below and set `FREEGLAZ_DIR` to the extracted freeglaz folder
   — drag the folder from the Finder into the script area, or right-click it →
   **Copy as Pathname** (§1), or type the known path:

   ```bash
   # freeglaz folder — replace with the real path (drag it here, or paste it):
   FREEGLAZ_DIR="/path/to/freeglaz-<version>"

   cd "$FREEGLAZ_DIR" || {
       osascript -e 'display alert "freeglaz" message "freeglaz folder not found. Edit FREEGLAZ_DIR." as critical'
       exit 1
   }

   # Find uv (Finder starts with a minimal PATH)
   if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
   elif [ -x /opt/homebrew/bin/uv ]; then UV=/opt/homebrew/bin/uv       # Apple Silicon
   elif [ -x /usr/local/bin/uv ]; then UV=/usr/local/bin/uv             # Intel
   elif [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"     # install script
   else
       osascript -e 'display alert "freeglaz" message "uv not found. See the installation guide." as critical'
       exit 1
   fi

   # Desktop mode needs the desktop extra (native window). --extra desktop is
   # mandatory — a plain `uv run` strips it. Check it is available first, so a
   # missing pywebview surfaces an alert instead of failing silently in an .app.
   if ! "$UV" run --extra desktop python -c "import webview" >/dev/null 2>&1; then
       osascript -e 'display alert "freeglaz" message "Desktop mode unavailable (pywebview). See the installation guide." as critical'
       exit 1
   fi

   # Launch the native window; log for diagnostics
   "$UV" run --extra desktop python -m webapp.desktop >>"$HOME/.freeglaz-desktop.log" 2>&1 &
   ```

4. Save as **freeglaz** (for example in the Applications folder).

### Set the icon

In the Finder, select the saved `freeglaz.app`, **File → Get Info** (⌘I), then
drag `webapp/icons/freeglaz-1024.png` (or `webapp/icons/freeglaz.icns`) from the
Finder onto the small icon at the top-left of the Info panel.

The application shows the freeglaz icon in the Finder and the Dock, opens
freeglaz in its own window (no Terminal), and keeps running while the server
runs; quitting it stops freeglaz.

> **Desktop extra.** Desktop mode needs the `desktop` extra (pywebview + pyobjc).
> The macOS release install already runs `uv sync --extra desktop`; a bare
> from-source install fetches it on first launch (network). Diagnostics go to
> `~/.freeglaz-desktop.log`.
>
> **Error visibility.** An `.app` opens no Terminal, so internal failures would be
> invisible. The script alerts on the pre-launch cases (missing folder, missing
> `uv`, unavailable desktop mode) via `osascript`.
>
> **Dock name (cosmetic limitation).** The running window carries the freeglaz
> icon and its menu bar reads “freeglaz”, but the Dock tooltip may still read
> “python3.13”: macOS fixes the Dock name from the executable at launch, before
> Python can rename the app — the runtime rename reaches only the menu bar. A
> dedicated `.app` bundle would fix it; out of scope for this version.

---

## 3. Right-click a file → open it in freeglaz (native)

A Finder right-click action: select a TIFF, right-click → **Quick Actions** →
**freeglaz — print TIFF**, and freeglaz opens in its own window with that file
loaded. It runs `freeglaz open <file>` in desktop mode.

The Finder input must be a **TIFF** — the web app accepts RGB TIFF only.

**The same shell script backs both host apps below — only the interface differs.**
Shortcuts is the current path (macOS 12 Monterey and later); Automator is the
fallback on older macOS.

### The script

Set `FREEGLAZ_DIR` to the extracted freeglaz folder — drag it from the Finder into
the script area, or right-click → **Copy as Pathname** (§1):

```bash
# freeglaz folder — replace with the real path (drag it here, or paste it):
FREEGLAZ_DIR="/path/to/freeglaz-<version>"

# One file at a time
if [ "$#" -gt 1 ]; then
    osascript -e 'display alert "freeglaz — one file only" message "Select a single file to print. Multiple files at once are not supported." as critical'
    exit 1
fi
f="$1"

# Must be a real file
if [ ! -f "$f" ]; then
    osascript -e 'display alert "freeglaz" message "No valid file selected." as critical'
    exit 1
fi

# TIFF only (the web app rejects other formats)
ext="$(echo "${f##*.}" | tr '[:upper:]' '[:lower:]')"
case "$ext" in
    tif|tiff) ;;
    *)
        osascript -e 'display alert "freeglaz — unsupported format" message "Only TIFF files are accepted." as critical'
        exit 1
        ;;
esac

# Enter the freeglaz folder
cd "$FREEGLAZ_DIR" || {
    osascript -e 'display alert "freeglaz" message "freeglaz folder not found. Edit FREEGLAZ_DIR." as critical'
    exit 1
}

# Find uv without relying on PATH (Finder starts with a minimal PATH)
if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
elif [ -x /opt/homebrew/bin/uv ]; then UV=/opt/homebrew/bin/uv       # Apple Silicon
elif [ -x /usr/local/bin/uv ]; then UV=/usr/local/bin/uv             # Intel
elif [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"     # install script
else
    osascript -e 'display alert "freeglaz" message "uv not found. See the installation guide." as critical'
    exit 1
fi

# Open the native window with the file loaded. --extra desktop is mandatory for
# a native window; without it, the file would open in the browser instead.
"$UV" run --extra desktop ./freeglaz open "$f" >>"$HOME/.freeglaz-open.log" 2>&1 &
```

The guards raise a visible alert on each failure — no valid file, a non-TIFF
format, a multi-file selection, a missing `uv`, or a wrong `FREEGLAZ_DIR`. The
window opens in the background (the menu returns at once); diagnostics go to
`~/.freeglaz-open.log`.

### A. Shortcut (Shortcuts) — recommended (macOS 12 and later)

1. In **Shortcuts** → **Settings → Advanced**, enable **Allow Running Scripts**.
2. **New Shortcut** → add **Run Shell Script**.
3. Set **Shell** to **bash**, **Pass Input** to **as arguments** (not *to stdin*).
   The input is **Shortcut Input** (the *Receive* block auto-sets *from Quick
   Actions*).
4. Paste **the script** above and set `FREEGLAZ_DIR`.
5. Open the **Details** panel (ⓘ) → **Use as Quick Action** → check **Finder**.
6. Name it **`freeglaz — print TIFF`**.
7. Right-click a TIFF → **Quick Actions** → **freeglaz — print TIFF** → the desktop
   window opens with the file.

### B. Quick Action (Automator) — fallback (macOS before 12)

1. Open **Automator** → **New Document** → **Quick Action**.
2. At the top: *Workflow receives current* **files or folders** *in* **Finder**.
3. Add **Run Shell Script**. Set *Shell* to `/bin/bash` and *Pass input* to
   **as arguments**.
4. Paste **the script** above and set `FREEGLAZ_DIR`.
5. Save as **freeglaz — print TIFF**.

Same script, same behavior; only the host application differs.

---

## 4. Web launch (the `.command`)

`launch_freeglaz.command`, at the root of the extracted folder, is the simple
alternative — a double-click that opens freeglaz in the default browser, with
nothing to create.

- A Terminal window opens and **stays open** while the server runs; closing it
  stops freeglaz. The browser opens once the server is ready.
- It locates its own folder and finds `uv` on its own; no path to edit, no extra.
- It runs the **web** mode (browser). For a native application window, use the
  `.app` (§2). The `.command` is deliberately kept on web: driving the desktop
  window from a `.command` would open a stray Terminal window next to the native
  one.
