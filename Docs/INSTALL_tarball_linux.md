# Installing freeglaz on Linux — from a release

The recommended install: a prebuilt release, two tools from the package manager,
then run the app. **No Node, no `git`, no build step.**

freeglaz drives an **HP DesignJet Z9** printer: printing, calibration, and
custom ICC color profiling. A graphical app (browser or native window) and a
command line share one engine.

> To build from the Git repository, see **`INSTALL_source_linux.md`** instead.

---

## 1. Prerequisites

| Tool | Why |
|---|---|
| **uv** | runs freeglaz and its Python environment |
| **Argyll CMS** | the color engine (calibration & profiling) |
| An **HP DesignJet Z9** on the network | the printer (optional — a demo `--mock` mode exists) |

Python is not installed by hand — `uv` fetches the right version. Argyll comes
from the distribution's package manager.

---

## 2. Install the tools (once)

### 2.1 Argyll CMS (the color engine)

Via the distribution's package manager. The package name differs:

- **Debian / Ubuntu / Linux Mint:**

  ```bash
  sudo apt update
  sudo apt install argyll fonts-dejavu-core
  ```

  The package is **`argyll`** (not `argyllcms`); `fonts-dejavu-core` renders the
  test charts.

- **Fedora:**

  ```bash
  sudo dnf install argyllcms
  ```

  Here the package is **`argyllcms`**.

Argyll is auto-detected: binaries in `/usr/bin`, reference data in
`/usr/share/color/argyll/ref`. No path to configure.

The distribution package is one installation method; other installations (manual
build, downloaded binaries) are described in the ArgyllCMS documentation —
https://www.argyllcms.com/. A non-standard location is resolved through the
override in §10.

### 2.2 uv (runs freeglaz)

Install uv with the official script, then load it into the current shell:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env      # or open a new terminal
uv --version                     # verify
```

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
uv sync
```

Creates a local environment and installs everything for **browser mode** (the
recommended mode — see §6).

> **Optional — native desktop window.** The native window (§6) needs the desktop
> extra and the system WebKitGTK libraries: `uv sync --extra desktop`. Browser
> mode does not need the extra.

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

Requires the desktop extra (§4). Then:

```bash
uv run --extra desktop python -m webapp.desktop
```

> If the native window opens blank (GPU/WebKitGTK mismatch), use the browser mode
> above — the interface is identical.

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
Install it for the distribution (§2.1): `sudo apt install argyll` (Debian/Ubuntu/Mint)
or `sudo dnf install argyllcms` (Fedora).

**The native desktop window is blank.**
Use the browser mode (`uv run ./freeglaz web`, §6) — it does not depend on
WebKitGTK.

**Job previews fail with an authorization error.**
That feature needs the printer's admin password — add it in
**Settings → Printers** (§7).

**“Z9 unreachable”.**
Check the entered IP, that the printer is on and on the same network. To explore
without a printer, launch with `--mock`.

---

## 10. Advanced — Argyll in a non-standard location (optional)

freeglaz auto-detects the distribution's Argyll (`/usr/bin` +
`/usr/share/color/argyll/ref`). Only for an install elsewhere, point to it with
an environment variable:

```bash
# A self-contained Argyll install (with bin/ and ref/ inside)
export FREEGLAZ_ARGYLL_ROOT=/opt/argyll
```

Then re-run `uv run ./freeglaz check` to confirm.

---

## Quick reference

```bash
# One-time setup (Debian/Ubuntu/Mint; Fedora: dnf install argyllcms)
sudo apt install argyll fonts-dejavu-core
curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env
tar -xzf freeglaz-<version>.tar.gz && cd freeglaz-<version>
uv sync
uv run ./freeglaz check                      # verify Argyll

# Launch
uv run ./freeglaz web                             # in the browser (:8765) — recommended
uv run --extra desktop python -m webapp.desktop   # or the native window (needs --extra desktop)
```
