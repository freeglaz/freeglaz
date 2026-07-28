# Installing freeglaz on Windows — from a release

The recommended install: a prebuilt release, two tools (uv + Argyll CMS), then run
the app. **No Node, no `git`, no build step.**

freeglaz drives an **HP DesignJet Z9** printer: printing, calibration, and
custom ICC color profiling. A graphical app (browser or native window) and a
command line share one engine.

> Commands below run in **PowerShell** or **Command Prompt**. Windows has no
> shebang, so the CLI is invoked as `python freeglaz …` (not `./freeglaz`).

---

## 1. Prerequisites

| Tool | Why |
|---|---|
| **uv** | runs freeglaz and its Python environment |
| **Argyll CMS** | the color engine (calibration & profiling) |
| An **HP DesignJet Z9** on the network | the printer (optional — a demo `--mock` mode exists) |

Python is not installed by hand — `uv` fetches the right version. On Windows,
Argyll has no package manager: you download the official binaries once (§2.2).

---

## 2. Install the tools (once)

### 2.1 uv (runs freeglaz)

In PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then **open a new terminal** and verify:

```powershell
uv --version
```

(Alternative: `winget install --id=astral-sh.uv -e`.)

### 2.2 Argyll CMS (the color engine)

Windows has no package for it — download the binaries from
https://www.argyllcms.com/ (the `Argyll_VX.X.X_win64_exe.zip`), then **extract it**
somewhere stable, e.g. `C:\Argyll`. The extracted folder contains `bin\` and `ref\`.

Point freeglaz at that folder (the one **containing** `bin\` and `ref\`) with a
user environment variable, then **open a new terminal** so it takes effect:

```powershell
setx FREEGLAZ_ARGYLL_ROOT "C:\Argyll"
```

> `setx` sets it permanently but only for **future** terminals — close and reopen.
> You can also set it via *Settings → System → About → Advanced system settings →
> Environment Variables*.

Other installations are described in the ArgyllCMS documentation. See §10 for the
bin/ref override details.

### 2.3 WebView2 runtime (only for the native window)

The **native desktop window** (§6) renders via Microsoft Edge **WebView2**,
pre-installed on Windows 11 and recent Windows 10. If the native window later
fails to open, install the free **WebView2 Runtime (Evergreen)** from Microsoft —
or just use the **browser mode**, which needs nothing extra.

---

## 3. Download and extract freeglaz

Download the latest `freeglaz-<version>.tar.gz` from the **Releases** page —
https://github.com/freeglaz/freeglaz/releases. Windows 10/11 include `tar`, so in
a terminal (assuming it landed in `Downloads`):

```powershell
cd %USERPROFILE%\Downloads
tar -xf freeglaz-<version>.tar.gz
cd freeglaz-<version>
```

> File Explorer cannot open a `.tar.gz` directly — use the `tar` command above (or
> a tool such as 7-Zip).

The graphical UI is **already built inside** — so Node is not needed. **Run every
command below from this folder.**

---

## 4. Set up the environment

```powershell
uv sync
```

Creates a local environment and installs everything for **browser mode** (the
recommended mode — see §6).

> **Optional — native desktop window.** The native window (§6) needs the desktop
> extra: `uv sync --extra desktop` (and the WebView2 runtime, §2.3). Browser mode
> does not need the extra.

---

## 5. Check the color engine

```powershell
uv run python freeglaz check
```

Expected: `✅ Argyll CMS is fully available.` Otherwise the command reports
exactly what is missing — usually `FREEGLAZ_ARGYLL_ROOT` not set or a terminal not
reopened after `setx` (§2.2).

---

## 6. Launch freeglaz

### Web app (browser) — recommended

```powershell
uv run python freeglaz web
```

The default browser opens at **http://127.0.0.1:8765** once the app is ready.
Options: `--port 9000` (different port), `--mock` (explore with no real printer).

**Without the command line:** double-click **`launch_freeglaz.bat`** in the
extracted folder — it finds uv and starts the web app. On the first run, Windows
SmartScreen may warn (*"Windows protected your PC"*): click **More info → Run
anyway**. Closing the console window stops freeglaz.

### Or the desktop app (native window)

Requires the desktop extra (§4) and WebView2 (§2.3). Then:

```powershell
uv run --extra desktop python -m webapp.desktop
```

A freeglaz window opens once the app is ready. (You can also open a file straight
into a native window: `uv run --extra desktop python freeglaz open "C:\path\to\image.tif"`.)

> If the native window opens blank or fails, use the browser mode above — the
> interface is identical.

---

## 7. Connect the printer

On first launch the app opens **directly on the “add printer” screen**:

1. Enter the Z9's **IP address or hostname** (e.g. `192.168.1.50`).
2. Optionally, enter the **admin password** — needed only for the **print-job
   queue and previews**. Everything else (printing, calibration, profiling)
   works without it. It can be added later in **Settings → Printers**.

The app stores the printer; no file to edit.

> The admin password is shown on the printer screen:
> **Menu → Connectivity → EWS configuration → show password**.

With no printer available, launch with `--mock` (§6) to explore the interface.

---

## 8. Data locations

| Path | Contents |
|---|---|
| `%USERPROFILE%\Documents\freeglaz\` | printers, color profiles, backups, sessions |
| `%USERPROFILE%\.freeglazrc.toml` | optional settings |

Uninstalling = delete the extracted folder (and `%USERPROFILE%\Documents\freeglaz\`
to also remove the profiles).

---

## 9. Troubleshooting

**`freeglaz check` says Argyll is missing.**
Set `FREEGLAZ_ARGYLL_ROOT` to the folder that contains `bin\` and `ref\` (§2.2),
then **open a new terminal** (`setx` only affects future terminals).

**“pywebview missing” when launching the desktop app.**
The `--extra desktop` was dropped. Use exactly:
`uv run --extra desktop python -m webapp.desktop`.

**The native window is blank or won't open.**
Install the **WebView2 Runtime** (§2.3), or use the browser mode
(`uv run python freeglaz web`) — it does not depend on WebView2.

**SmartScreen blocks `launch_freeglaz.bat` on first run.**
Click **More info → Run anyway**. This is the standard warning for unsigned,
downloaded scripts.

**Job previews fail with an authorization error.**
That feature needs the printer's admin password — add it in
**Settings → Printers** (§7).

**“Z9 unreachable”.**
Check the entered IP, that the printer is on and on the same network. To explore
without a printer, launch with `--mock`.

---

## 10. Advanced — Argyll bin/ref override (optional)

freeglaz resolves Argyll from `FREEGLAZ_ARGYLL_ROOT` (a folder with `bin\` and
`ref\`). If your binaries and reference data live in separate places, set them
individually instead:

```powershell
setx FREEGLAZ_ARGYLL_BIN "C:\tools\argyll\bin"
setx FREEGLAZ_ARGYLL_REF "C:\tools\argyll\ref"
```

Open a new terminal, then re-run `uv run python freeglaz check` to confirm.

---

## Quick reference

```powershell
# One-time setup
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
setx FREEGLAZ_ARGYLL_ROOT "C:\Argyll"     # folder with bin\ and ref\ — reopen terminal
tar -xf freeglaz-<version>.tar.gz
cd freeglaz-<version>
uv sync
uv run python freeglaz check              # verify Argyll

# Launch
uv run python freeglaz web                          # in the browser (:8765) — recommended
uv run --extra desktop python -m webapp.desktop     # or the native desktop window (needs --extra desktop + WebView2)
```
