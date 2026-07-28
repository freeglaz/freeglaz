# Reference ICC profiles — provenance & license

The **gamut-reference** ICC profiles bundled in this folder (`lib/z9_client/assets/`)
come from **Elle Stone's ICC pack** (the set used, notably, by Krita).

- **Author**: Elle Stone — <http://ninedegreesbelow.com/>
- **Repository**: <https://github.com/ellelstone/elles_icc_profiles>
- **License (ICC files)**: Creative Commons Attribution-ShareAlike 3.0 Unported (**CC-BY-SA 3.0**)
  — <https://creativecommons.org/licenses/by-sa/3.0/legalcode>
- **Copyright** (`cprt` tag of each file, **preserved**):
  "Copyright 2016, Elle Stone (http://ninedegreesbelow.com/), CC-BY-SA"

## Included files (UNMODIFIED)

Variant **ICC V2, standard TRC of the space**, one per space:

| Space | File | TRC | ICC version |
|---|---|---|---|
| sRGB | `sRGB-elle-V2-srgbtrc.icc` | sRGB | 2.2.0 |
| AdobeRGB-compatible (**ClayRGB**) | `ClayRGB-elle-V2-g22.icc` | gamma 2.2 | 2.2.0 |
| ProPhoto (**LargeRGB**) | `LargeRGB-elle-V2-g18.icc` | gamma 1.8 | 2.2.0 |
| Rec.2020 | `Rec2020-elle-V2-rec709.icc` | Rec.709 | 2.2.0 |
| Rec.709 | `Rec709-elle-V2-rec709.icc` | Rec.709 | 2.2.0 |

Primaries verified as conforming to each space (iccdump). Elle Stone's original names
are **kept** (traceability). "ClayRGB" / "LargeRGB" are Elle Stone's free reconstructions
(from the primaries/specs) — they are **not** the proprietary AdobeRGB1998® / ProPhoto®
files.

## Use in freeglaz

**Indicative / comparative references only**:
- `-S` colprof: source gamut of the perceptual mapping (at the user's **choice**, not a default);
- "reference" overlay of the **3D** / **2D slice** views of the profile inspector.

⚠️ **No effect on the profiles freeglaz produces**: these files only serve to
compare/display.

## CC-BY-SA 3.0 compliance

Inclusion as **data resources** (aggregation): does **not** affect the freeglaz code
license. CC-BY-SA conditions met: **attribution** (this file + preserved `cprt` tags),
**unmodified files**, **documented provenance**. The pack's full license is in Elle
Stone's repository (link above).
