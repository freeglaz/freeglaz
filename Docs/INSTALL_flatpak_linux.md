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

## 5. Data locations

| Path | Contents |
|---|---|
| `~/Documents/freeglaz/` | printers, color profiles, backups, sessions |
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

## Coming: Flathub

Once published on **Flathub**, freeglaz will install in one click directly from
GNOME Software / KDE Discover / your distribution's software centre — no download,
no terminal, with automatic updates.
