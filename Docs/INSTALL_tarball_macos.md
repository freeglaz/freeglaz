# Installing freeglaz on macOS — from a release

The recommended install: a prebuilt release, three tools from Homebrew, then run
the app. **No Node, no `git`, no build step.**

freeglaz drives an **HP DesignJet Z9** printer: printing, calibration, and
custom ICC color profiling. A graphical app (browser or native window) and a
command line share one engine.

> To build from the Git repository, see **`INSTALL_source_macos.md`** instead.

---

## 1. Prerequisites

| Tool | Why |
|---|---|
| **Homebrew** | installs the two tools below |
| **uv** | runs freeglaz and its Python environment |
| **Argyll CMS** | the color engine (calibration & profiling) |
| An **HP DesignJet Z9** on the network | the printer (optional — a demo `--mock` mode exists) |

Python is not installed by hand — `uv` fetches the right version. Argyll is a
normal Homebrew package (never bundled).

---

## 2. Install the tools (once)

### 2.1 Xcode Command Line Tools

```bash
xcode-select --install
```

A dialog opens — click **Install**. (If already installed, the command reports so.)

### 2.2 Homebrew

If absent (`brew --version` to check):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**Apple Silicon (M1/M2/M3…):** the installer prints two `Next steps` lines to add
Homebrew to the `PATH`. Run them once:

```bash
echo >> ~/.zprofile
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

### 2.3 uv and Argyll CMS

```bash
brew install uv argyll-cms
```

Verify:

```bash
uv --version
colprof --version     # prints the Argyll version
```

Homebrew is one installation method; other installations (manual build,
downloaded binaries) are described in the ArgyllCMS documentation —
https://www.argyllcms.com/. A non-standard location is resolved through the
override in §10.

---

## 3. Download and extract freeglaz

Download the latest `freeglaz-<version>.tar.gz` from the **Releases** page —
https://github.com/freeglaz/freeglaz/releases — then in a terminal (assuming it
landed in `~/Downloads`):

```bash
cd ~/Downloads
tar -xzf freeglaz-<version>.tar.gz
cd freeglaz-<version>
```

The graphical UI is **already built inside** — so Node is not needed. **Run every
command below from this folder.**

---

## 4. Set up the environment

```bash
uv sync --extra desktop
```

Creates a local environment and installs everything, including the native
desktop window. (For browser-only use, `uv sync` alone is enough.)

> **uv gotcha.** Keep `--extra desktop` on the commands that launch the desktop
> app (§6): a plain `uv sync` / `uv run` removes the desktop piece; the extra on
> the run command keeps it.

---

## 5. Check the color engine

```bash
uv run ./freeglaz check
```

Expected: `✅ Argyll CMS is fully available.` Otherwise the command reports
exactly what is missing and how to fix it.

---

## 6. Launch freeglaz

### Web app (browser) — recommended

```bash
uv run ./freeglaz web
```

The default browser opens at **http://127.0.0.1:8765** once the app is ready.
Options: `--port 9000` (different port), `--mock` (explore with no real printer).

### Or the desktop app (native window)

```bash
uv run --extra desktop python -m webapp.desktop
```

A freeglaz window opens once the app is ready.

For launching without the command line (double-click, and a Finder right-click
action to open a TIFF in freeglaz), see **`install_bonus_macos.md`**.

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
| `~/Documents/freeglaz/` | printers, color profiles, backups, sessions |
| `~/.freeglazrc.toml` | optional settings |

Uninstalling = delete the extracted folder (and `~/Documents/freeglaz/` to also
remove the profiles).

---

## 9. Troubleshooting

**`freeglaz check` says Argyll is missing.**
Run `brew install argyll-cms`. For an unusual location, see §10.

**“pywebview missing” when launching the desktop app.**
The `--extra desktop` was dropped. Use exactly:
`uv run --extra desktop python -m webapp.desktop`.

**The desktop app won't start / a component seems half-installed** (after mixing
commands). Rebuild the environment cleanly:

```bash
uv sync --extra desktop --reinstall
```

**Job previews fail with an authorization error.**
That feature needs the printer's admin password — add it in
**Settings → Printers** (§7).

**“Z9 unreachable”.**
Check the entered IP, that the printer is on and on the same network. To explore
without a printer, launch with `--mock`.

---

## 10. Advanced — Argyll in a non-standard location (optional)

freeglaz auto-detects Homebrew's Argyll (Apple Silicon under `/opt/homebrew`,
Intel under `/usr/local`). Only for an install elsewhere, point to it with an
environment variable:

```bash
# A self-contained Argyll install (with bin/ and ref/ inside)
export FREEGLAZ_ARGYLL_ROOT=/opt/argyll
```

Then re-run `uv run ./freeglaz check` to confirm.

---

## Quick reference

```bash
# One-time setup
xcode-select --install                       # if not already present
brew install uv argyll-cms
tar -xzf freeglaz-<version>.tar.gz && cd freeglaz-<version>
uv sync --extra desktop
uv run ./freeglaz check                      # verify Argyll

# Launch
uv run ./freeglaz web                             # in the browser (:8765) — recommended
uv run --extra desktop python -m webapp.desktop   # or the native desktop window
```
