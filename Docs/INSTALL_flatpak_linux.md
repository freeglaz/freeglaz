# Installing freeglaz on Linux — the Flatpak

The simplest install on Linux: one self-contained package. Everything is bundled
— Python, libvips, and **ArgyllCMS** — so there is nothing else to install and
the full profiling path works out of the box.

Works on any distribution with Flatpak (Fedora, Debian/Ubuntu, Mint, Arch…) and
integrates with GNOME, KDE, Cinnamon and XFCE.

## 1. Prerequisites

Flatpak is preinstalled on Fedora and most GNOME/KDE systems. If missing:

```bash
sudo dnf install flatpak     # Fedora   (Debian/Ubuntu: sudo apt install flatpak)
```

Flathub must be configured so Flatpak can fetch the GNOME runtime the app runs on:

```bash
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
```

## 2. Install

Download `freeglaz-<version>-x86_64.flatpak` from the **Releases** page —
https://github.com/freeglaz/freeglaz/releases — then:

```bash
flatpak install --user freeglaz-<version>-x86_64.flatpak
```

The first install also pulls the GNOME runtime (a few hundred MB, shared with
other Flatpak apps and fetched only once).

## 3. Run

```bash
flatpak run io.github.freeglaz.freeglaz
```

freeglaz also appears in your **application menu** (with its icon) — launchable in
one click. A TIFF can be opened with freeglaz from the file manager ("Open With").

## 4. Connect the printer

On first launch the app opens on the **"add printer"** screen: enter the Z9's IP
or hostname. The admin password is optional (only the print-queue previews need
it) and can be added later in Settings → Printers.

To explore without a printer, use **"Try a demo"** on that screen.

### Measuring with a handheld spectrophotometer (experimental)

From **0.1.17-rc.2**, the Flatpak also ships **chartread-fgK**, the ArgyllCMS
3.5.0 `chartread` with a checkpoint after each strip, built from source (its
source is in `/app/chartread-fgK/source/`). freeglaz uses only this copy, never
another `chartread`, and publishes measurements only when it is the copy shipped
in the package.

The app asks for access to **USB devices only** (`--device=usb`). That needs:

- **Flatpak 1.15.11 or later** (`flatpak --version`). Older versions have not
  been tested: they may not give the instrument to the app.
- **udev rules** on your system that let your user open the instrument. Fedora
  already covers some instruments (the ColorMunki), not the i1Pro 3. ArgyllCMS's
  rules cover both; the Flatpak ships them. To install them, once:

  ```bash
  flatpak run --command=cat io.github.freeglaz.freeglaz /app/argyll/usb/55-Argyll.rules \
      | sudo tee /etc/udev/rules.d/55-Argyll.rules > /dev/null
  sudo udevadm control --reload-rules
  ```

  then unplug and plug the instrument back in.

Only one program can use the instrument at a time: close other colour software
while freeglaz measures.

## 5. Data locations

| Path | Contents |
|---|---|
| `~/Documents/freeglaz/` | printers, color profiles, backups, sessions, charts and their measurements |
| `~/.var/app/io.github.freeglaz.freeglaz/` | the app's private state (logs, settings) |

## 6. Update

Download the newer `.flatpak` and install it the same way (it replaces the old
version). Once freeglaz is on Flathub, `flatpak update` will handle it.

## 7. Uninstall

```bash
flatpak uninstall --user io.github.freeglaz.freeglaz
```

(Delete `~/Documents/freeglaz/` too to also remove the color profiles.)

## Notes

- **ArgyllCMS is bundled** in the Flatpak (unlike the tarball install, where it is
  a separate system dependency) — nothing to install for the profiling path.
- The bundle install needs **Flathub configured** (§1) so the GNOME runtime can be
  fetched. On Flathub proper (coming), that is automatic.
- **Checking a download**: each release lists the sha256 of its files in
  `SHA256SUMS` (`sha256sum -c SHA256SUMS --ignore-missing`).

## Sending diagnostics

freeglaz never sends anything by itself. To report a problem, attach what is
useful, after reading it:

- the app log: `~/.var/app/io.github.freeglaz.freeglaz/data/freeglaz/freeglaz.log`
  (and `freeglaz.log.1`… if present);
- for a measurement: the session folder
  `~/Documents/freeglaz/charts/<printer>/<chart>/external/<session>/`, its
  `session.json` and `logs/`.

These files can contain the printer's address and serial number, paper and file
names, and the measured values. Remove what you do not want to share.

## Coming: Flathub

Once published on **Flathub**, freeglaz will install in one click directly from
GNOME Software / KDE Discover / your distribution's software centre — no download,
no terminal, with automatic updates.
