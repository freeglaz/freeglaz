# freeglaz — Convert (experimental)

Convert turns an image into a **device file** for the paper loaded in the Z9,
through an ArgyllCMS DeviceLink. It is a research bench, not a production path:
functional and proven down to the PRN, with one A4 print validated, but not
hardened. This page describes what it does today, how it is built, and where the
code and benches live.

> **Status: experimental — research only.** The Convert tab is hidden by default.
> Nothing in Print depends on it; Print works exactly the same with Convert off.

---

## Why it exists

The normal freeglaz workflow expects a TIFF **already converted** to the paper's
profile in an editor (see the [User Guide](USER_GUIDE.md#how-color-is-handled)):
freeglaz never converts colours on the print path. Convert is an optional stage
**upstream** of Print that does this conversion inside freeglaz, with the full
set of Argyll gamut-mapping controls exposed, so that conversion strategies can
be compared on real images and real prints.

Design principles (kept from the design notes):

- **A bench, not a preset picker.** No intent is declared "best" or chosen
  automatically; each one shows its command and its cost. The user picks a route
  and judges it — the print, measured, is the final arbiter (a screen lies in the
  shadows).
- **Orthogonal axes.** Intent, image-aware gamut, quality, viewing conditions and
  gloss enhancer are independent controls, so a comparison can change **one axis
  at a time** and attribute a difference to its cause.
- **Reproducible.** The full collink command is logged and the output file name
  encodes the settings.
- **Detection, not assumption.** The source space is the image's embedded ICC;
  without one, Convert refuses. The destination is the loaded paper's resident
  profile; there is no destination selector.

## Enabling it

**Settings → Show the Convert tab.** The choice is stored in the browser
(`localStorage`, key `freeglaz.showConvertTab`), so it is per browser / per
desktop install, and off by default.

Requirements:

- a configured Z9, reachable, **with a paper loaded** (the destination profile is
  read from it);
- ArgyllCMS with `collink` and `cctiff` (and `tiffgamut` for the image-aware
  option). They are optional binaries: the rest of freeglaz runs without them,
  and Convert answers 503 with an explicit message if they are missing. The
  Flatpak bundles Argyll; the macOS app and the tarballs use a separate install
  (`brew install argyll-cms` on macOS).

## How it works

```
image.tif (embedded ICC = SOURCE)
   │
   ├─ refuse if no embedded ICC (400) or if a profile uses mAB/mBA LUTs (422)
   │
   ├─ DEST = resident profile of the LOADED paper, for the chosen GE state
   │         (same primitive as Print: fetch_resident_icc)
   │
   ├─ normalise both profiles for Argyll (drop v4 mluc text tags, header → v2.4;
   │  colorimetric tags copied byte-for-byte)
   │
   ├─ collink  → link.icc     (command depends on the gamut intent, below)
   └─ cctiff -N -e paper.icc  → converted_<settings>.tif   (16-bit, uncompressed,
                                tagged with the paper's resident profile)
```

The TIFF is dropped on the tab or picked with **Browse…** (**Change file…**
swaps it). The output is a **device TIFF**: its pixel values
are meant for the paper's inks as they are. It is either saved — native save
dialog in the desktop app, plain download in a browser — or handed to Print
(see below).

### Gamut intents

The selector is labelled "collink gamut intent" (these are collink's own gamut
intents, not an ICC rendering intent). `relative` is the default and matches the
original Convert behaviour.

| UI label | `gamut_intent` | collink | What it does / costs |
|---|---|---|---|
| Relative | `relative` | `-G -ir` | Default. Slightly lightens saturated colours; separates the deepest shadows the least. |
| Shadow detail | `luminance_matched` | `-G -ila` | Matches the white→black luminance axis: keeps deep-shadow separation. Lightens saturated colours. |
| Perceptual | `perceptual` | `-G -ip` | 3-D perceptual compression. Lightens and desaturates saturated colours. |
| Perceptual, shadow detail | `luminance_preserving` | `-G -ilp` | Perceptual compression favouring lightness. Keeps deep-shadow separation; lightens saturated colours. |
| Saturated-colour lightness *(freeglaz)* | `luminance_priority` | `-s -ir -p <abstract τ>` | Custom abstract: chroma compression at constant luminance. Keeps the lightness of saturated colours, desaturates them in exchange; separates the deepest shadows less. |
| Relative + BPC *(freeglaz)* | `relative_bpc` | `-s -ir -p <abstract_bpc>` | Relative colorimetric with black point compensation: lifts the dark range above the paper's printable black. Washes the blacks in exchange; only useful if the file holds detail below that black. |

The two freeglaz entries do **not** use `-G`: they insert a hand-authored Lab→Lab
abstract profile (lut16/mft2, 33³ grid) between source and destination with
`collink -s -ir -p`.

- **Luminance priority** is radial at constant L: (L, a, b) → (L, a·s, b·s).
  Chroma is compressed toward the destination boundary measured with `xicclu`,
  scaled by **τ** (0.5 → 2.0; low τ protects luminance and sacrifices chroma,
  high τ keeps chroma). τ = 1 is a reference, not an optimum, and the range is
  the characterised one — do not widen it without a dedicated bench.
- **BPC** is a linear L scaling from the source black to the destination black
  (both measured from the profiles), a and b unchanged, no parameter.
- Both are checked at runtime by a **neutral guard**: the neutral axis must stay
  neutral (a, b ≈ 0). For BPC only chroma is checked, since L changes by design.

### Other controls

| Control | Values | Applies to | Effect |
|---|---|---|---|
| Quality | Low / Med / **High** / Ultra (`-ql/m/h/u`) | native intents | Resolution of the link's 3-D grid. Finer = smoother gradients, slower build. Does not change inks or profile. |
| Gloss enhancer | GE OFF / GE ON | all | Which resident characterisation of the loaded paper is the destination. Pick the one you will print with. |
| Image-aware gamut | off / on (`-G <image.gam>`) | native intents only | Maps only the gamut the image actually occupies (from `tiffgamut -pj`, CIECAM02 Jab), leaving printable colours alone. Useful when perceptual shifts the whole image. |
| Viewing conditions (destination) | Default / `pp` / `pc` / `pe` / `pm` (`-d`) | native intents only | CIECAM02 print presets. A refinement with a subtle-to-marginal effect, mostly on perceptual intents. |

With an abstract intent (`luminance_priority`, `relative_bpc`), image-aware and
viewing conditions are ignored: they belong to collink's gamut-mapping mode.

### Output and traceability

- File name: `converted_<intent>[_tau<τ>]_<quality>[_ia][_<viewcond>].tif`.
- The backend log records the full command, e.g.
  `collink -v -qh -G -ir source.icc dest.icc link.icc`, with the paper id and GE
  state of the resident that was used.
- The device TIFF is **uncompressed** (`cctiff -N`): cctiff's default LZW output
  is a single huge strip that neither the Print pipeline (tifffile) nor the
  preview (libvips) can read here. Pixels are identical either way.

### Hand-off to Print

**Convert & send to Print** converts, then loads the device file into the Print
tab and switches to it. Nothing prints automatically: geometry and confirmation
happen in Print as usual. Print is unchanged — the device TIFF embeds the
resident profile, so source == resident and the job is a **device passthrough**:
the values reach the inks untouched. `test_convert_print_integrity.py` checks
that the pixels reach the PDF/X-4 bit-for-bit (16-bit, no transform, no
resample).

## Limits

- **mAB/mBA profiles are refused** (true ICC v4 `lutAToBType`, e.g. i1Profiler
  output): the Argyll engine cannot read them. mft1/mft2 profiles are fine, even
  inside a v4 header (Z9-native and freeglaz-built profiles). The check is on
  the LUT tag type, never on the ICC version.
- The source must be a TIFF with an **embedded ICC**. Matrix working spaces
  (sRGB, Adobe RGB, ProPhoto…) work, including v4 ones after normalisation.
- The destination is always the loaded paper's resident profile; there is no
  way to convert to another profile or with no Z9.
- Only one A4 print has been validated end to end. The intents were
  characterised on one paper (Canson witness); their behaviour on other papers
  is not established.
- No side-by-side comparison view yet: comparing routes means converting
  several times and viewing the files in an editor.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/convert/source-info?file_id=…` | Embedded profile of an uploaded file: `has_profile`, colour space, PCS, TRC summary. |
| `POST /api/convert` | Body: `file_id`, `gamut_intent`, `tau`, `quality`, `gloss_enhancer`, `image_aware`, `dest_viewcond`. Returns the device TIFF. Errors: 400 no source profile, 404 file, 409 no Z9 / no paper, 422 unknown intent or unsupported LUT, 502 Z9 unreachable, 503 Argyll binaries missing, 500 conversion failed. |

## Code map

| Where | What |
|---|---|
| `webapp/backend/routes/convert.py` | Routes, intent table, guards, command logging. |
| `lib/z9_client/devicelink.py` | `collink` / `cctiff` wrappers, ICC normalisation for Argyll, LUT support check, embedded-ICC extraction. |
| `lib/z9_client/tiffgamut.py` | `tiffgamut` wrapper (image-aware gamut). |
| `webapp/backend/services/luminance_priority.py` | The two freeglaz abstracts (luminance priority, BPC), boundary measurement, neutral guards, `collink -s -p` link. |
| `webapp/frontend/src/components/Convert/ConvertPage.jsx` | The Convert tab. |
| `webapp/frontend/src/hooks/useShowConvertTab.js`, `components/Settings/ConvertTabSetting.jsx` | The opt-in setting. |
| `webapp/backend/tests/test_convert_*.py` | Smoke (guards), Print readability of the output, Convert → Print integrity. |

Not to be confused with `lib/z9_client/strategies.py`, which holds the
**colprof profiling presets** — an unrelated notion. Convert speaks of "gamut
intents", never "strategies".

## Research benches

The algorithms were characterised in standalone scripts under `scripts/` before
being ported. They are measurement tools, not wired to the app; each documents
its protocol in its docstring. The main ones:

- `convert_variants.py` — the assembled Convert chain on a real image, four
  variants compared, TIFF checked against the predicted link output.
- `abstract_poc.py`, `rebaseline_tau1.py`, `cmeasured_refine.py`,
  `cmeasured_criterion.py` — the luminance-priority abstract and its τ boundary.
- `bpc_phase1.py`, `bpc_phase2.py` — black point compensation.
- `argyll_native_audit.py`, `deep_shadow_tradeoff.py`, `axis2_primitive.py`,
  `saturated_deltaL_audit.py`, `encoding_invariance.py`, `neutral_axis_diag.py`
  — behaviour of the native intents in the shadows and on saturated colours,
  encoding invariance, neutral axis.
- `v4_confront.py` — which ICC LUT types the installed Argyll can read.
