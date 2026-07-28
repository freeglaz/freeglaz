# Installing freeglaz on Linux — from source (contributors)

This guide builds freeglaz from the Git repository: clone it and **build the web
UI locally**. It targets contributors and the development setup (hot reload,
latest `main`).

> For a plain install with no Node/build step, see **`INSTALL_tarball_linux.md`**.

freeglaz drives an **HP DesignJet Z9** printer: printing, calibration, and
custom ICC color profiling (CLI + web/desktop app over one engine).

---

## 1. Prerequisites

| Tool | Why |
|---|---|
| **git** | clone the repository |
| **uv** | Python environment & dependency manager |
| **Argyll CMS** | color engine (chart gen, profile build, spectral→XYZ) |
| **Node.js ≥ 22 LTS** | builds the web UI (source path only) |
| **HP DesignJet Z9** on the network | the printer (optional — `--mock` mode exists) |

Python ≥ 3.13 is required but installed automatically by `uv`. Argyll is an
**external** dependency (never bundled): freeglaz calls its binaries (`colprof`,
`targen`, `spec2cie`, `profcheck`) and reference data (`ref/`).

---

## 2. Install the prerequisites

### 2.1 Argyll CMS (the color engine)

The package name differs by distribution:

- **Debian / Ubuntu / Linux Mint:**

  ```bash
  sudo apt update
  sudo apt install argyll fonts-dejavu-core git
  ```

  The package is **`argyll`** (not `argyllcms`); `fonts-dejavu-core` renders the
  test charts.

- **Fedora:**

  ```bash
  sudo dnf install argyllcms git
  ```

  Here the package is **`argyllcms`**.

Argyll is auto-detected: binaries in `/usr/bin`, reference data in
`/usr/share/color/argyll/ref`. No path to configure (see §9 for a custom
install).

The distribution package is one installation method; other installations (manual
build, downloaded binaries) are described in the ArgyllCMS documentation —
https://www.argyllcms.com/. A non-standard location is resolved through the
override in §9.

### 2.2 uv

Install uv with the official script, then load it into the current shell:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env      # or open a new terminal
uv --version                     # verify
```

### 2.3 Node.js ≥ 22

Node **≥ 22** builds the front end. The distribution's package may be older;
install a current release from [NodeSource](https://github.com/nodesource/distributions),
[nvm](https://github.com/nvm-sh/nvm), or the distribution if it ships ≥ 22:

```bash
node --version     # must be v22 or newer
```

An older Node fails the build with a clear message (pinned via `.nvmrc` /
`engines`).

---

## 3. Clone the repository

```bash
git clone https://github.com/freeglaz/freeglaz
cd freeglaz
```

All commands below run **from the repository root**.

---

## 4. Create the Python environment

```bash
uv sync
```

Creates `.venv/` and installs the locked deps (uv also fetches Python 3.13 if
needed). For the **native desktop app**, install the optional extra instead —
it also needs the system WebKitGTK libraries:

```bash
# Debian/Ubuntu/Mint: sudo apt install gir1.2-webkit2-4.1 libgirepository1.0-dev
# Fedora:             sudo dnf install webkit2gtk4.1 gobject-introspection-devel
uv sync --extra desktop
```

> **uv gotcha.** A plain `uv sync` (without `--extra desktop`) *removes* the
> desktop dependency, and `uv run <cmd>` re-syncs before running — so it strips
> the extra too. To launch the desktop app, pass the extra **on the run
> command** (§7). Browser mode does not need the extra.

---

## 5. Build the web UI

The front end is **not** pre-built in the repo — build it once (and after any
front-end change). Needs **Node ≥ 22** (§2.3):

```bash
cd webapp/frontend
npm ci             # reproducible install from the committed lockfile
npm run build      # emits webapp/frontend/dist/, served by the backend
cd ../..
```

The CLI does **not** need this step; only the web/desktop UI does.
`freeglaz web --build` also runs this build.

---

## 6. Verify the color engine

```bash
uv run ./freeglaz check
```

Expected: `✅ Argyll CMS is fully available.` (exit 0). Otherwise the command
reports exactly what is missing. See §9 for custom Argyll paths.

---

## 7. Running freeglaz

### Command-line (CLI)

```bash
uv run ./freeglaz status           # printer + paper + ink overview
uv run ./freeglaz check            # Argyll availability
uv run ./freeglaz --help           # all commands
```

Or activate the environment for the session:

```bash
source .venv/bin/activate
./freeglaz status
```

### Web app (browser) — recommended

```bash
uv run ./freeglaz web
```

Opens the default browser at **http://127.0.0.1:8765** once the server is ready.
Flags:

| Flag | Effect |
|---|---|
| `--port 9000` | different port (default `8765`) |
| `--no-browser` | start the server without opening a browser |
| `--mock` | mock the printer — no real Z9 |
| `--reload` | uvicorn auto-reload on change (backend dev) |
| `--build` | run `npm run build` before launching |

For **front-end hot reload** during development, run Vite separately
(`cd webapp/frontend && npm run dev`) against a running backend.

### Desktop app (native window)

Requires the desktop extra + WebKitGTK (§4):

```bash
uv run --extra desktop python -m webapp.desktop
```

Flags: `--mock`, `--port <n>`. Keep `--extra desktop` on the run command (§4
gotcha). If the native window opens blank (GPU/WebKitGTK mismatch), use the
browser mode above — the interface is identical.

---

## 8. Connect the printer

### In the web/desktop app

On first launch the app opens on the **add-printer screen**: enter the Z9's **IP
address or hostname** and, optionally, the **admin password** (needed only for
the print-job queue/previews and admin settings). Editable later in
**Settings → Printers**. The admin password is shown on the printer:
**Menu → Connectivity → EWS configuration → show password**.

### For the CLI — `.env`

The CLI resolves the address from `--host` > `Z9_HOST` (env) > the printer store
the app writes. A `.env` at the repo root is the simplest:

```bash
cp .env.example .env
```

```dotenv
Z9_HOST=192.168.1.50
Z9_TIMEOUT=10
# Optional — only for the job queue / admin endpoints (env takes precedence
# over the value stored via the app):
Z9_ADMIN_PWD=admin-password
```

`.env` is git-ignored. Verify: `uv run ./freeglaz ping` then `… status`.

---

## 9. Data locations & custom Argyll

| Path | Contents |
|---|---|
| `~/Documents/freeglaz/` | printer registry, profile mirror, per-paper profiles, backups, sessions |
| `~/.freeglazrc.toml` | user configuration (optional) |
| `.env` (repo root) | CLI printer connection |

Argyll auto-detection covers the distribution packages. Override only for a
non-standard install, via env (highest priority) or `~/.freeglazrc.toml`:

```bash
export FREEGLAZ_ARGYLL_ROOT=/opt/argyll          # expects <root>/bin and <root>/ref
# or, separately (win over ROOT):
export FREEGLAZ_ARGYLL_BIN=/opt/argyll/bin
export FREEGLAZ_ARGYLL_REF=/opt/argyll/ref
```

```toml
[argyll]
root    = "/opt/argyll"
# or: bin_dir = "…" / ref_dir = "…"
```

Resolution order: **env → `[argyll]` config → per-platform auto-detect**. Re-run
`uv run ./freeglaz check` to confirm.

---

## 10. Troubleshooting

**`freeglaz check` reports Argyll missing.** Install it for the distribution
(§2.1): `sudo apt install argyll` (Debian/Ubuntu/Mint) or `sudo dnf install
argyllcms` (Fedora). For a non-standard location set `FREEGLAZ_ARGYLL_ROOT` (§9).

**Web app shows a blank page / 404.** Build the front first (§5):
`cd webapp/frontend && npm ci && npm run build`.

**`npm run build` fails on an old Node.** Install Node ≥ 22 (§2.3); the project
pins it via `.nvmrc` / `engines` and fails fast otherwise.

**The native desktop window is blank.** Use the browser mode
(`uv run ./freeglaz web`, §7) — it does not depend on WebKitGTK.

**“pywebview missing” (desktop).** Keep `--extra desktop` on the run command:
`uv run --extra desktop python -m webapp.desktop`.

**A dependency looks installed but import fails** (after mixing `uv run`/`uv
sync`): `uv sync --extra desktop --reinstall`.

**Job previews fail with 401.** Set the admin password (Settings → Printers, or
`Z9_ADMIN_PWD` in `.env`).

**“Z9 unreachable”.** Check the address (`Z9_HOST` / add-printer screen), network
and printer power; `uv run ./freeglaz ping`. Use `--mock` to explore without a Z9.

---

## Quick reference

```bash
# One-time setup (Debian/Ubuntu/Mint; Fedora: dnf install argyllcms git)
sudo apt install argyll fonts-dejavu-core git
curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env
# Node ≥ 22 from NodeSource / nvm / the distribution
git clone https://github.com/freeglaz/freeglaz && cd freeglaz
uv sync                                      # (or `uv sync --extra desktop` + WebKitGTK)
cd webapp/frontend && npm ci && npm run build && cd ../..
uv run ./freeglaz check                      # verify Argyll

# Everyday use
uv run ./freeglaz status                          # CLI
uv run ./freeglaz web                             # web app (browser, :8765)
uv run --extra desktop python -m webapp.desktop   # desktop app (native window)
```
