# macOS packaging — standalone `.app` (Phase 1, unsigned)

Builds a self-contained, double-clickable `freeglaz.app` (and a plain `.dmg`)
so a user does not need Homebrew, `uv`, a Python install or a Terminal to run
freeglaz. The interpreter, all Python dependencies, the built frontend and the
reference ICC assets are bundled.

**ArgyllCMS is not bundled.** It stays an external system dependency, installed
separately (`brew install argyll-cms`) and auto-detected at runtime. The
printing path works without it; only the open profiling path needs it.

## Build

```
./packaging/macos/build_app.command
```

The script:

1. `uv sync --extra desktop` (ensures the desktop deps, incl. `pyvips[binary]`),
2. builds the frontend if `webapp/frontend/dist` is missing,
3. runs PyInstaller against `freeglaz.spec`,
4. ad-hoc signs the bundle,
5. leaves `dist/freeglaz.app` and `dist/freeglaz.dmg`.

Requires `uv` and a working `pyvips[binary]` install (the build errors early if
the bundled libvips is missing). PyInstaller is pulled ephemerally
(`uv run --with pyinstaller`) and is not added to the project environment.

## Install

Drag `freeglaz.app` to `/Applications`, then clear the quarantine flag once
(the app is unsigned — this is the equivalent of "Open anyway"):

```
xattr -dr com.apple.quarantine /Applications/freeglaz.app
```

## Why unsigned / no notarization

No Apple Developer account is used. The bundle carries a free **ad-hoc**
signature (`codesign -s -`), which is only what Apple Silicon requires to run a
local binary — it is not notarization. Gatekeeper therefore asks once; the
`xattr` line above answers it. This is a deliberate Phase-1 choice.

## Notes for maintainers

Two macOS-specific details the script handles, worth knowing before editing it:

- **libvips (`pyvips`).** The bundle ships `pyvips_binary`'s self-contained
  `libvips.42.dylib` (its only dependencies are `/usr/lib` and system
  frameworks) and loads it in **ABI mode**. The API extension `_libvips` is
  excluded on purpose: on a machine with Homebrew `vips` it links to the
  Homebrew libvips tree, whose pango/harfbuzz symbols break once bundled.
  `rthook_libvips.py` (a PyInstaller runtime hook) points `DYLD_LIBRARY_PATH`
  at the bundle's support dir so the ABI-mode `dlopen` resolves libvips.

- **Code signing in a synced folder.** If the checkout lives in a File Provider
  folder (iCloud Drive, a sync client — common for `~/Documents`), the provider
  daemon re-applies `com.apple.fileprovider` xattrs to the `.app` the instant
  they are stripped, and `codesign` then fails with *"resource fork … detritus
  not allowed"*. The script builds and signs in `$TMPDIR` (not file-provider
  managed) and copies the signed bundle back; the signature seals the bundle
  contents, so it survives the copy.

## Read-only install and writable data

A bundled `.app` in `/Applications` is read-only, so the webapp's runtime state
(logs, settings, job mapping, previews) is redirected out of the bundle via the
`FREEGLAZ_DATA_DIR` environment variable, set by `entry.py` to
`~/Library/Application Support/freeglaz` when running frozen. User-facing data
(custom profiles, config) already lives in the home directory
(`~/Documents/freeglaz`, `~/.freeglazrc.toml`, `~/Library/ColorSync/Profiles`)
and is unaffected.

## Files

- `build_app.command` — the driver script.
- `freeglaz.spec` — the PyInstaller spec.
- `entry.py` — the `.app` entry point (empty argv, frozen-env setup).
- `rthook_libvips.py` — runtime hook for libvips (see above).
