# Flatpak packaging — freeglaz (work in progress)

A self-contained Flatpak so Linux users install freeglaz from **Flathub / GNOME
Software / KDE Discover** in one click — no terminal, no dependencies (ArgyllCMS
is bundled). The GNOME runtime provides GTK + WebKitGTK (what pywebview needs).

> **Status: scaffold.** A Flatpak builds only on Linux (`flatpak-builder`). The
> files here are the structure + metadata; three parts still need to be filled
> and the whole thing iterated on a real Linux build (§2–§4). The manifest has
> `TODO`/placeholder `sha256` on purpose.

## Files

- `io.github.freeglaz.freeglaz.yaml` — the manifest.
- `io.github.freeglaz.freeglaz.desktop` — menu entry + `image/tiff` association.
- `io.github.freeglaz.freeglaz.metainfo.xml` — AppStream metadata (Flathub-required).
- `freeglaz-launcher.sh` — in-sandbox launcher (env + `python -m webapp.desktop`).
- `python3-deps.yaml` — **generated** (§2), not committed until it builds.

## 1. Prerequisites (Linux)

```bash
flatpak install flathub org.gnome.Platform//47 org.gnome.Sdk//47
# flatpak-builder + the pip generator:
sudo dnf install flatpak-builder            # Fedora  (or: apt install flatpak-builder)
pip install req2flatpak                     # or use flatpak-pip-generator (below)
```

Confirm the current stable GNOME runtime version and that it ships WebKitGTK,
then set `runtime-version` in the manifest accordingly.

## 2. Generate the Python dependencies (on Linux — resolves for Linux)

```bash
# From the repo root, export the desktop deps resolved for THIS (Linux) platform:
uv export --frozen --no-emit-project --extra desktop --format requirements-txt \
    > packaging/flatpak/requirements-linux.txt

# Turn them into a Flatpak module (flatpak-pip-generator from flatpak-builder-tools):
python flatpak-pip-generator --runtime=org.gnome.Sdk//47 \
    --requirements-file packaging/flatpak/requirements-linux.txt \
    --output packaging/flatpak/python3-deps
```

This writes `python3-deps.yaml` with every wheel pinned (url + sha256), so the
build stays offline. **libvips**: `pyvips[binary]` ships a manylinux wheel with
libvips inside — if it fails to load in the runtime, build libvips as its own
module instead (it is in most repos) and drop the `[binary]` extra.

## 3. Pin the sources

In `io.github.freeglaz.freeglaz.yaml`, replace the two placeholder `sha256`:

- **freeglaz** — the release tarball being packaged (it already contains the
  built frontend). Get the hash: `sha256sum freeglaz-<version>.tar.gz`.
- **ArgyllCMS** — the current `Argyll_V<x>_linux_x86_64_bin.tgz` from
  argyllcms.com. Bundling it (GPL) makes the app dependency-free.

## 4. Build & run

```bash
flatpak-builder --user --install --force-clean build-dir \
    packaging/flatpak/io.github.freeglaz.freeglaz.yaml
flatpak run io.github.freeglaz.freeglaz
```

Expect iteration. Likely first hurdles:

- **WebKitGTK / the window** — pywebview must find WebKitGTK in the runtime;
  `desktop.py` forces `GDK_BACKEND=x11`, so `--socket=fallback-x11` (XWayland)
  must be present (it is).
- **libvips** — see §2.
- **Argyll binaries** — the prebuilt ones need libtiff/libjpeg/libpng (and X11
  for a couple of tools) from the runtime; if a tool fails to load, add the lib
  as a module or build Argyll from source.
- **Data dirs** — the launcher points `FREEGLAZ_DATA_DIR` at the XDG data dir;
  user profiles stay in `~/Documents/freeglaz` via `--filesystem=xdg-documents`.

## 5. Validate the metadata (before any Flathub submission)

```bash
flatpak run org.freedesktop.appstream-glib validate \
    packaging/flatpak/io.github.freeglaz.freeglaz.metainfo.xml
desktop-file-validate packaging/flatpak/io.github.freeglaz.freeglaz.desktop
```

## 6. Flathub (later)

Once it builds and runs locally: submit `io.github.freeglaz.freeglaz` to
Flathub (a PR to `flathub/flathub` with this manifest). Flathub then builds and
hosts it, and it appears in every desktop's software center.

---

Open items tracked here so nothing is silently dropped: file "Open With" is
wired (`desktop.py` accepts a path → boots on it via `?file_id=`); the `.desktop`
`StartupWMClass` may need adjusting so the taskbar groups the window under the
freeglaz icon.
