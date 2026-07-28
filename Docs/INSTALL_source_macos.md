# Installing freeglaz on macOS — from source (contributors)

This guide builds freeglaz from the Git repository: clone it and **build the web
UI locally**. It targets contributors and the development setup (hot reload,
latest `main`).

> For a plain install with no Node/build step, see **`INSTALL_tarball_macos.md`**.

freeglaz drives an **HP DesignJet Z9** printer: printing, calibration, and
custom ICC color profiling (CLI + web/desktop app over one engine).

---

## 1. Prerequisites

| Tool | Why |
|---|---|
| **Homebrew** | installs the tools below |
| **uv** | Python environment & dependency manager |
| **Argyll CMS** | color engine (chart gen, profile build, spectral→XYZ) |
| **Node.js ≥ 22 LTS** | builds the web UI (source path only) |
| **HP DesignJet Z9** on the network | the printer (optional — `--mock` mode exists) |

Python ≥ 3.13 is required but installed automatically by `uv`. Argyll is an
**external** dependency (never bundled): freeglaz calls its binaries (`colprof`,
`targen`, `spec2cie`, `profcheck`) and reference data (`ref/`).

---

## 2. Install the prerequisites

### 2.1 Xcode Command Line Tools

Provides `git` and the compilers Homebrew relies on:

```bash
xcode-select --install
```

### 2.2 Homebrew

If absent (`brew --version` to check):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**Apple Silicon only** — add Homebrew to the `PATH` (once):

```bash
echo >> ~/.zprofile
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

### 2.3 uv, Argyll CMS, Node.js

```bash
brew install uv argyll-cms node
```

Verify (Node **≥ 22** is required to build the front end; an older Node fails the
build with a clear message, pinned via `.nvmrc` / `engines`):

```bash
uv --version
colprof --version     # Argyll
node --version        # v22 or newer
```

On **Apple Silicon** Argyll lands under `/opt/homebrew` (binaries) +
`/opt/homebrew/opt/argyll-cms/ref` (reference data); on **Intel**, `/usr/local`.
Both are auto-detected (see §9 for a custom install).

Homebrew is one installation method; other installations (manual build,
downloaded binaries) are described in the ArgyllCMS documentation —
https://www.argyllcms.com/. A non-standard location is resolved through the
override in §9.

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
needed). For the **native desktop app**, install the optional extra instead:

```bash
uv sync --extra desktop
```

> **uv gotcha.** A plain `uv sync` (without `--extra desktop`) *removes* the
> desktop dependency, and `uv run <cmd>` re-syncs before running — so it strips
> the extra too. To launch the desktop app, pass the extra **on the run
> command** (§7).

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

```bash
uv run --extra desktop python -m webapp.desktop
```

Flags: `--mock`, `--port <n>`. Keep `--extra desktop` on the run command (§4
gotcha).

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

Argyll auto-detection covers Homebrew. Override only for a non-standard install,
via env (highest priority) or `~/.freeglazrc.toml`:

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

**`freeglaz check` reports Argyll missing.** `brew install argyll-cms`; for a
non-standard location set `FREEGLAZ_ARGYLL_ROOT` (§9).

**Web app shows a blank page / 404.** Build the front first (§5):
`cd webapp/frontend && npm ci && npm run build`.

**`npm run build` fails on an old Node.** Install Node ≥ 22 (§2.3); the project
pins it via `.nvmrc` / `engines` and fails fast otherwise.

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
# One-time setup
xcode-select --install                       # if not already present
brew install uv argyll-cms node
git clone https://github.com/freeglaz/freeglaz && cd freeglaz
uv sync --extra desktop                      # (or `uv sync` for CLI/web only)
cd webapp/frontend && npm ci && npm run build && cd ../..
uv run ./freeglaz check                      # verify Argyll

# Everyday use
uv run ./freeglaz status                          # CLI
uv run ./freeglaz web                             # web app (browser, :8765)
uv run --extra desktop python -m webapp.desktop   # desktop app (native window)
```
