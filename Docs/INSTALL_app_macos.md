# Installing freeglaz on macOS — the app

The simplest install: download the app, drag it to Applications, launch it. **No
Homebrew, no `uv`, no Python, no Terminal** for everyday printing.

freeglaz drives an **HP DesignJet Z9** printer: printing, calibration, and
custom ICC color profiling.

> This app is **Apple Silicon** (M1/M2/M3…), **macOS 11 or later**. On an Intel
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

## 6. Custom ICC profiling with ArgyllCMS (optional)

The app prints and runs the printer's **built-in** profiling without any extra
install. The **open profiling path** (chart defined with ArgyllCMS, ICC computed
by ArgyllCMS) additionally needs ArgyllCMS, which is never bundled. Install it
once with Homebrew:

```bash
brew install argyll-cms
```

(If Homebrew is absent: https://brew.sh/.) freeglaz auto-detects Argyll under
`/opt/homebrew`. The **Profiles** and **Measurements** screens surface a clear
notice while Argyll is missing.

---

## 7. Data locations

| Path | Contents |
|---|---|
| `~/Documents/freeglaz/` | printers, color profiles, backups, sessions |
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

---

## Building the app yourself

The app is produced from source by `packaging/macos/build_app.command` (see
`packaging/macos/README.md`). Building it is not required to use a release.
