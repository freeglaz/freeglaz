# Installing freeglaz on macOS — the app

The simplest install: download the app, drag it to Applications, launch it. **No
Homebrew, no `uv`, no Python, no Terminal** for everyday printing.

freeglaz drives an **HP DesignJet Z9** printer: printing, calibration, and
custom ICC color profiling.

> This app is **Apple Silicon** (M1/M2/M3…), **macOS 14 (Sonoma) or later**. On an Intel
> Mac or an older macOS, use **`INSTALL_tarball_macos.md`** instead.

---

## 1. Download

From the **Releases** page — https://github.com/freeglaz/freeglaz/releases —
download the file named:

```
freeglaz-<version>-macos-arm64.dmg
```

## 2. Install

1. Open the downloaded `.dmg`.
2. Drag **freeglaz** onto the **Applications** folder.
3. Eject the disk image.

## 3. First launch

The app is not signed with an Apple Developer certificate (freeglaz is a free,
open-source project). macOS therefore blocks it on the first open. Clear the flag
once, in Terminal:

```bash
xattr -dr com.apple.quarantine /Applications/freeglaz.app
```

Then open freeglaz normally from Applications or Launchpad. (Alternative, without
Terminal: right-click the app → **Open** → **Open** in the dialog. This is only
needed once.)

---

## 4. Connect the printer

On first launch the app opens **directly on the “add printer” screen**:

1. Enter the Z9's **IP address or hostname** (e.g. `192.168.1.50`).
2. Optionally, enter the **admin password** — needed only for the **print-job
   queue and previews**. Everything else (printing, calibration, profiling)
   works without it. It can be added later in **Settings → Printers**.

The app stores the printer; no file to edit.

> The admin password is shown on the printer screen:
> **Menu → Connectivity → EWS configuration → show password**.

With no printer available, the interface can still be explored.

---

## 5. Printing

Load a **TIFF** (RGB, 8- or 16-bit, embedded ICC profile, already converted to
the paper profile) by dragging it into the window — or **onto the freeglaz icon
in the Dock**. Other formats are rejected.

---

## 6. Custom ICC profiling with ArgyllCMS

The app prints and runs the printer's **built-in** profiling without any extra
install. The **open profiling path** (chart defined with ArgyllCMS, ICC computed
by ArgyllCMS) needs ArgyllCMS.

From **0.1.17-rc.2**, the app ships the official **ArgyllCMS 3.5.0** tools for
Apple Silicon (unmodified, with their licence and source, see
`freeglaz.app/Contents/Frameworks/argyll/ARGYLL_SOURCE.txt`): nothing to install,
and the app uses its own copy even when Homebrew has another one.

An app without them (an older release, or one built without
`FREEGLAZ_BUNDLE_ARGYLL`) needs ArgyllCMS from Homebrew, once:

```bash
brew install argyll-cms
```

(If Homebrew is absent: https://brew.sh/.) freeglaz then auto-detects Argyll
under `/opt/homebrew`. The **Profiles** and **Measurements** screens surface a
clear notice while Argyll is missing.

### Measuring with a handheld spectrophotometer (experimental)

From **0.1.17-rc.2**, the app also ships **chartread-fgK**, the ArgyllCMS 3.5.0
`chartread` with a checkpoint after each strip (source in
`freeglaz.app/Contents/Frameworks/chartread-fgK/source/`). freeglaz uses only
this copy, never another `chartread`, and publishes measurements only when it
is the copy shipped in the app. ArgyllCMS needs no extra driver for these
instruments on macOS. Only one program can use the instrument at a time: quit
i1Profiler and other X-Rite software before measuring.

---

## 7. Data locations

| Path | Contents |
|---|---|
| `~/Documents/freeglaz/` | printers, color profiles, backups, sessions, charts and their measurements |
| `~/Library/Application Support/freeglaz/` | internal app state (logs, job history) |

Uninstalling = move **freeglaz** from Applications to the Trash (and the two
folders above to also remove profiles and state).

---

## 8. Troubleshooting

**“freeglaz is damaged / can’t be opened.”**
The quarantine flag is still set. Run the `xattr` line in §3.

**“Z9 unreachable.”**
Check the entered IP, that the printer is on and on the same network.

**The open profiling path is unavailable / a notice mentions Argyll.**
Install ArgyllCMS (§6).

**Sending diagnostics.** freeglaz never sends anything by itself. To report a
problem, attach what is useful, after reading it:
- the app log: `~/Library/Application Support/freeglaz/freeglaz.log` (and
  `freeglaz.log.1`… if present). In Finder: **Go → Go to Folder…** and paste the
  folder path;
- for a measurement: the session folder
  `~/Documents/freeglaz/charts/<printer>/<chart>/external/<session>/`, its
  `session.json` and `logs/`.

These files can contain the printer's address and serial number, paper and file
names, and the measured values. Remove what you do not want to share.

---

## Building the app yourself

The app is produced from source by `packaging/macos/build_app.command` (see
`packaging/macos/README.md`). Building it is not required to use a release.
