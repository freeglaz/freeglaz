# Flatpak packaging — freeglaz

A self-contained Flatpak: **all Python deps, libvips, and ArgyllCMS are bundled**
— zero external dependency. Linux users install from **Flathub / GNOME Software /
KDE Discover** in one click. The GNOME runtime provides GTK + WebKitGTK (pywebview).

> **Status: builds and runs.** Validated on Fedora Workstation / GNOME 50: the
> window opens, the backend serves, libvips loads, ArgyllCMS is detected, the demo
> mode works. A Flatpak still builds only on Linux (`flatpak-builder`).

## Files

- `io.github.freeglaz.freeglaz.yaml` — the manifest.
- `python3-deps.yaml` — pinned Python deps (generated, committed; regenerate per §2).
- `io.github.freeglaz.freeglaz.desktop` — menu entry + `image/tiff` association.
- `io.github.freeglaz.freeglaz.metainfo.xml` — AppStream metadata (Flathub-required).
- `freeglaz-launcher.sh` — in-sandbox launcher (env + `python -m webapp.desktop`).

## 1. Prerequisites (Linux)

```bash
flatpak install flathub org.gnome.Platform//50 org.gnome.Sdk//50 org.flatpak.Builder
curl -O https://raw.githubusercontent.com/flatpak/flatpak-builder-tools/main/pip/flatpak-pip-generator
```

(No sudo: `org.flatpak.Builder` is the flatpak-builder flatpak; `--user` installs.)

## 2. Regenerate the Python deps (only when uv.lock changes)

Two flag families are **required** — learned the hard way:

- `--prefer-wheels=<compiled pkgs>` — the generator prefers *sdists* by default;
  compiled packages must use wheels (else numpy/scipy/lxml/pydantic-core try to
  build, and pydantic-core needs maturin, which is unavailable offline).
- `--ignore-installed=<all pkgs>` — packages present in the **SDK** (e.g. lxml,
  markupsafe, pygments) are otherwise skipped by pip and end up missing from
  `/app` at runtime (the Platform lacks them).

```bash
cd packaging/flatpak
# Resolve for Linux (run on Linux):
uv export --frozen --no-emit-project --extra desktop --no-hashes --format requirements-txt \
    > requirements-linux.txt

COMPILED=numpy,scipy,lxml,pillow,pikepdf,pypdfium2,uvloop,httptools,pyyaml,pydantic-core,pyvips-binary,cffi,watchfiles,charset-normalizer,scikit-image
ALL=$(grep -oE '^[A-Za-z0-9._-]+' requirements-linux.txt | tr '[:upper:]' '[:lower:]' | sort -u | paste -sd,)

uv run --no-project --with requirements-parser --with PyYAML \
    python3 flatpak-pip-generator --runtime='org.gnome.Sdk//50' \
        --requirements-file requirements-linux.txt \
        --prefer-wheels="$COMPILED" --ignore-installed="$ALL" \
        --yaml --output python3-deps
```

(The "Unresolved dependencies" message the generator prints is benign — it refers
to the macOS-only pyobjc packages, which are correctly skipped on Linux.)

## 3. Build & run

```bash
flatpak run org.flatpak.Builder --user --install --force-clean \
    build-dir packaging/flatpak/io.github.freeglaz.freeglaz.yaml
flatpak run io.github.freeglaz.freeglaz
```

freeglaz then also appears in the application menu (with its icon). If you edited
`python3-deps.yaml`, wipe `.flatpak-builder/` before rebuilding to avoid a stale
module cache.

### Notes / gotchas that are already handled in the manifest

- **Wayland-native.** `desktop.py` forces `GDK_BACKEND=x11` (a WebKitGTK/NVIDIA
  Wayland-crash workaround for the tarball install). The launcher overrides it to
  Wayland and sets `WEBKIT_DISABLE_DMABUF_RENDERER=1`, which fixes that crash in
  the GNOME runtime — so the manifest needs only `--socket=fallback-x11`
  (Flathub-preferred), not the broad `--socket=x11`.
- **libvips** comes from the `pyvips-binary` wheel — loads fine in the runtime.
- **ArgyllCMS V3.5.0** prebuilt binaries: all shared-lib deps resolve against the
  GNOME runtime; `FREEGLAZ_ARGYLL_ROOT=/app/argyll` (set by the launcher).
- **Writable data**: the launcher points `FREEGLAZ_DATA_DIR` at the XDG data dir
  (the package dir is read-only); user profiles stay in `~/Documents/freeglaz`.

## 4. Bump the packaged release

In the manifest, update the freeglaz `sources` URL + `sha256`
(`sha256sum freeglaz-<version>.tar.gz`) to the release being packaged, and
regenerate `python3-deps.yaml` (§2) if the lock changed.

## 5. Validate metadata (before Flathub)

```bash
flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest \
    packaging/flatpak/io.github.freeglaz.freeglaz.yaml
flatpak run --command=flatpak-builder-lint org.flatpak.Builder appstream \
    packaging/flatpak/io.github.freeglaz.freeglaz.metainfo.xml
```

## 6. Flathub (later)

Submit `io.github.freeglaz.freeglaz` to Flathub (a PR to `flathub/flathub` with
this manifest). Flathub then builds and hosts it, and it shows up in every
desktop's software center.
