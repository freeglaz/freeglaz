# freeglaz — User Guide

Day-to-day use of freeglaz: printing, managing papers, calibration, and building
custom ICC profiles for the HP DesignJet Z9. Installation is covered by the
`INSTALL_*` guides in this folder; the [README](../README.md) describes the
project and its limitations.

---

## The interface

freeglaz runs in a browser, or in a native window (desktop app). The graphical
app and the command line share one engine; this guide covers the app.

- **Top bar** — the pages: Print, Papers, Measurements, Profiles, Logs, Settings.
- **Bottom bar** — the printer state (ready / no paper / printing), the ten ink
  levels, and the print queue.

On first launch with no printer configured, the app opens on the add-printer
screen. It requires the Z9's IP address or hostname. The admin password is
optional: it is used only for the job queue and job previews — printing,
calibration and profiling work without it, and it can be added later in
**Settings → Printers**. Launching with `--mock` opens the interface with no
printer connected.

---

## How color is handled

freeglaz sends the file to the printer unchanged: the 16-bit data, in the paper
profile assigned in the editing software, with no rendering-intent change, no
black-point compensation and no conversion. Color decisions belong upstream, in
the soft-proof against the paper profile.

As a safety net, freeglaz compares the file's embedded profile with the profile
of the paper loaded in the printer, and reports a warning if they differ. It is a
warning, not a block.

---

## 1. Printing an image

The **Print** page.

Accepted input: an RGB TIFF, 8- or 16-bit, with an embedded ICC profile, already
converted to the target paper profile. Other formats and color spaces are
rejected.

A file is loaded by drag-and-drop onto the viewer, or via **Browse**. Once loaded:

- The viewer shows the image placed on the sheet; the sidebar shows the loaded
  paper.
- The **ICC badge** is green when the file's profile matches the loaded paper,
  and a warning when it differs (printing remains possible).
- The print size is computed from the file's **DPI**; freeglaz does not rescale.
  An image larger than the printable area is flagged, and **Print** is disabled.

Parameters (sidebar): Gloss enhancer (*on the image* / *off*, unavailable on
papers without the kit), Quality (*High / Normal / Fast*), Copies, and — under
**Advanced** — position X/Y, Center, Rotate 90°, Max Detail, Dry time.

The **Print** button sends the job. Its progress appears in the bottom bar and
the queue.

---

## 2. Papers

The **Papers** page lists every paper the printer knows (factory and custom),
with filters (finish, calibration state), favorites and notes.

- **Create custom paper** clones a factory "donor" paper. Creation does not start
  profiling; the steps are separate. The sequence is: create the paper, load it
  physically, then run a color calibration (§3).
- **Mechanical properties**: thickness and cutter settings, per paper.
- **Resident ICC profile**: each paper slot (GE on / GE off) holds a resident
  profile that can be exported, imported, restored to factory, or rolled back. A
  backup is taken before any overwrite.

---

## 3. Color calibration (CLC)

The **Calibrate** action, on a paper's detail panel, runs the printer's color
calibration. The paper must be loaded. Calibration takes several minutes and
reports progress live.

---

## 4. Profiling a paper

Profiling builds an ICC profile describing how a paper reproduces color. Two
paths exist, both measured by the Z9's embedded spectrophotometer.

### HP built-in

The profiling wizard offers three modes:
- *Print and scan (auto mode)* — the full workflow in one session (one A3 sheet,
  or about 18 cm of a 24″ roll).
- *Print only* / *Scan only* — the print and the measurement are separated in
  time (print, dry, reload, scan).

The firmware generates the chart, prints it, measures it, and computes and
installs the profile — including on Linux, where the vendor tool is unavailable.

### ArgyllCMS (open path)

From the **Measurements** page:
1. *Print a chart* — the patch set comes from ArgyllCMS (`targen`); freeglaz lays
   it out as a honeycomb chart and prints it. The gloss enhancer covers the image
   area only.
2. *Scan* — the printed chart is measured. Before each scan, the app states the
   reload (sheet) or rewind (roll) step. A chart can be scanned several times; a
   QC view reports the per-patch agreement between scans, and a reading can be
   rejected (reversibly).
3. *Build* — the profile is computed by ArgyllCMS (`colprof`). The firmware
   performs no color computation on this path.
4. *Check* — a self-consistency report (ΔE) on the profile. It re-uses the
   building data, so it is optimistic by nature.
5. *Validate* — compares the profile's prediction against an independent set of
   measurements.
6. *Install* — sets the profile as the paper's resident profile; the previous one
   is backed up.

---

## 5. Comparing and inspecting profiles

The **Profiles** page.

- **Compare** — 2 to 8 ICC profiles side by side: identity, content signature,
  gamut volumes, white and black points, ΔE self-consistency.
- **Inspect** — one profile: header and tags, a 3D gamut view, a 2D a\*b\* slice,
  the tone reproduction curves, and an ICC conformance check.
- **Check** — the self-consistency report also used in profiling.

The profile store — a read-only mirror of the printer's profiles, plus a personal
repository — is browsable offline, with tagging and reuse.

---

## 6. The print queue

The queue, reached from the bottom bar, shows live job states and provides:
pause and resume of the queue; cancel, remove or reprint of a job (reprint
applies to finished or cancelled jobs); clearing the queue; and a job's
thumbnail.

---

## 7. Settings

The **Settings** page:
- **Language** — English or French, stored in the browser.
- **Theme** — light themes, stored in the browser.
- **Default gamut reference** — the reference space shown by default in the 3D
  gamut view.
- **ArgyllCMS** — the color engine's status; auto-detected, with an optional path
  override.
- **Printers** — add, edit, activate or remove printers.

---

## Troubleshooting

- **"Z9 unreachable"** — check the IP, the printer's power state and the network.
  The Wake button (bottom bar) wakes a sleeping printer. `--mock` opens the
  interface with no printer.
- **A file is rejected on load** — the web app accepts only a single-page RGB TIFF
  (8- or 16-bit) with an embedded ICC profile.
- **"Image too large"** — freeglaz prints at the file's DPI and does not rescale;
  a larger paper, or a re-export at the intended size, is required.
- **Job queue or previews unavailable** — these require the printer's admin
  password (Settings → Printers).
- **Native window issues** (desktop app) — the browser mode is available as a
  fallback; see the install guide.

Install problems and platform notes: the `INSTALL_*` guides in this folder.
Coverage and limitations: the **Limitations** section of the [README](../README.md).
