/**
 * Lab chromaticity → screen map for the a*b* slice background
 * (profile-compare — 2D Visual tab).
 *
 * Paints a canvas pixel by pixel: for each (a*, b*) of the plane, we
 * compute the color Lab(L0, a*, b*) projected into a **configurable
 * reference space** (sRGB / AdobeRGB / Display P3 / Rec2020 /
 * ProPhoto), and we clip to transparent the pixels outside that
 * space.
 *
 * CAUTION — this background is NOT soft-proof:
 *   - It's an INFORMATIVE REFERENCE (« this contour overflows into which
 *     color family? »).
 *   - We aim for COVERAGE (that all compared gamuts land
 *     on colored background) and CHROMATIC INDICATION (correct
 *     red/green/yellow/blue orientation), not display fidelity.
 *   - The right space depends on what's being compared: sRGB for
 *     common screens; AdobeRGB (default) covers most of the
 *     usual paper gamuts without wasted background; Display P3 for
 *     wide-gamut screens; Rec2020 for wide working
 *     spaces; ProPhoto for very wide (at the cost of a « low-
 *     density » background in the useful area).
 *   - The final display is re-encoded in sRGB gamma for the canvas
 *     (the browser interprets the RGB bytes as sRGB): the
 *     out-of-sRGB colors are « crushed » but their chromatic
 *     orientation (hue, relative order) stays correct. Accepted
 *     trade-off for the readability of an informative reference.
 *
 * Color pipeline:
 *   Lab D50 → XYZ D50 → [Bradford D50→D65 if the space is D65] →
 *   linear RGB of the space → clip [0,1] (transparent if outside) →
 *   sRGB gamma encoding → canvas
 *
 * ProPhoto is natively D50: no chromatic adaptation needed,
 * we skip Bradford and use XYZ D50 → linear
 * ProPhoto directly.
 */

// Whitepoint D50 (Lab ICC reference).
const Xn = 0.96422;
const Yn = 1.0;
const Zn = 0.82521;

// Bradford D50 → D65. Standard coefficients. Used for the
// spaces whose native whitepoint is D65 (sRGB, AdobeRGB, P3,
// Rec2020). For ProPhoto (native D50), we skip this step.
const M_D50_TO_D65 = [
  [0.9555766, -0.0230393, 0.0631636],
  [-0.0282895, 1.0099416, 0.0210077],
  [0.0122982, -0.0204830, 1.3299098],
];

// ─── XYZ → linear RGB matrices per reference space ──────────────
//
// All computed from the standard primaries and the native
// whitepoint of each space, as the inverse of the canonical
// linear-RGB → XYZ matrix.
//
// Sources:
//   - sRGB    : IEC 61966-2-1 (D65, sRGB primaries)
//   - AdobeRGB: Adobe RGB (1998) (D65, Adobe primaries)
//   - P3      : Display P3 (D65, P3 primaries)
//   - Rec2020 : BT.2020 (D65, Rec2020 primaries R:0.708/0.292,
//               G:0.170/0.797, B:0.131/0.046)
//   - ProPhoto: ProPhoto RGB / ROMM RGB (native D50, ROMM primaries)

const M_XYZ_TO_LIN_SRGB = [
  [3.2404542, -1.5371385, -0.4985314],
  [-0.9692660, 1.8760108, 0.0415560],
  [0.0556434, -0.2040259, 1.0572252],
];

const M_XYZ_TO_LIN_ADOBERGB = [
  [2.0413690, -0.5649464, -0.3446944],
  [-0.9692660, 1.8760108, 0.0415560],
  [0.0134474, -0.1183897, 1.0154096],
];

const M_XYZ_TO_LIN_REC2020 = [
  [1.7166511880, -0.3556707838, -0.2533662814],
  [-0.6666843518, 1.6164812366, 0.0157685458],
  [0.0176398574, -0.0427706133, 0.9421031212],
];

// ProPhoto RGB / ROMM RGB is computed in native D50.
const M_XYZ_TO_LIN_PROPHOTO = [
  [1.3459433, -0.2556075, -0.0511118],
  [-0.5445989, 1.5081673, 0.0205351],
  [0.0000000, 0.0000000, 1.2118128],
];

// Space table: XYZ→linear matrix + native whitepoint. The
// pipeline skips Bradford D50→D65 when the space is in D50.
const _SPACE_TABLE = {
  sRGB:     { matrix: M_XYZ_TO_LIN_SRGB,     whitepoint: 'D65' },
  AdobeRGB: { matrix: M_XYZ_TO_LIN_ADOBERGB, whitepoint: 'D65' },
  Rec2020:  { matrix: M_XYZ_TO_LIN_REC2020,  whitepoint: 'D65' },
  ProPhoto: { matrix: M_XYZ_TO_LIN_PROPHOTO, whitepoint: 'D50' },
};

/** Ordered list for the selector (from smallest to widest gamut).
 *  P3 removed (screen/video oriented, out of scope). */
export const REFERENCE_SPACES = ['sRGB', 'AdobeRGB', 'Rec2020', 'ProPhoto'];

/** Default: sRGB (neutral, guaranteed; consistent with the overlay reference default). */
export const DEFAULT_REFERENCE_SPACE = 'sRGB';

// Lab f^-1 threshold constants.
const KAPPA = 24389 / 27;  // 903.296...
const EPSILON = 216 / 24389;  // 0.008856...


function _fInv(t) {
  // Inverse of the Lab transform: f^-1(t) = t³ if t > 6/29, otherwise
  // linear slope 3 (6/29)² (t − 4/29). Equivalent in κ/ε notation:
  //   if t³ > ε: t³; otherwise: (116 t − 16) / κ
  const t3 = t * t * t;
  return t3 > EPSILON ? t3 : (116 * t - 16) / KAPPA;
}


function _labToXyzD50(L, a, b) {
  const fy = (L + 16) / 116;
  const fx = a / 500 + fy;
  const fz = fy - b / 200;
  return [Xn * _fInv(fx), Yn * _fInv(fy), Zn * _fInv(fz)];
}


function _mulMat3Vec(M, v) {
  return [
    M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2],
    M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2],
    M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2],
  ];
}


// sRGB gamma encoding applied to the linear-Rec2020 components for
// canvas display. Accepted approximation: the browser
// will interpret the RGB bytes as sRGB, so the
// out-of-sRGB colors will be displayed « crushed » but their chromatic
// orientation (hue, relative order) stays correct — which is enough
// for an informative reference. Strict BT.2020 gamma would be more
// correct but for canvas compositing / a common sRGB screen,
// the visual difference is minimal and readability is better.
function _linearToSrgb(v) {
  if (v <= 0.0031308) return 12.92 * v;
  return 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
}


/**
 * Lab(L, a, b) → [r, g, b, valid] in the reference space
 * `space` (defaults to `DEFAULT_REFERENCE_SPACE` = AdobeRGB).
 *
 * `valid=false` if the color falls outside the linear gamut of the space
 * (before gamma). This is the « out-of-reference » signal used by
 * `paintLabChromaBackground` to render the pixel transparent.
 *
 * Pipeline:
 *   - Lab(D50) → XYZ(D50);
 *   - if the space is in D65 → Bradford D50→D65;
 *     if the space is in D50 (ProPhoto) → XYZ D50 directly;
 *   - XYZ(native WP) → linear RGB of the space via its matrix;
 *   - each component → sRGB gamma for canvas display
 *     (readable approximation, cf. header).
 */
export function labToBackgroundRgb(L, a, b, space = DEFAULT_REFERENCE_SPACE) {
  const cfg = _SPACE_TABLE[space] || _SPACE_TABLE[DEFAULT_REFERENCE_SPACE];
  const xyzD50 = _labToXyzD50(L, a, b);
  const xyzNative = cfg.whitepoint === 'D50'
    ? xyzD50
    : _mulMat3Vec(M_D50_TO_D65, xyzD50);
  const linRGB = _mulMat3Vec(cfg.matrix, xyzNative);

  // Overshoot beyond [0,1] = out of the space's gamut. Instead of a
  // binary clip (which leaves a saturated fringe — typically magenta
  // when green clips negative — then cuts sharply), we degrade
  // smoothly: over an OOG_MARGIN margin beyond the boundary, we
  // pull the color toward gray (desaturation) and decrease
  // the opacity (fade to transparent). Informative reference, not soft-proof.
  let over = 0;
  for (let i = 0; i < 3; i++) {
    if (linRGB[i] < 0) over = Math.max(over, -linRGB[i]);
    else if (linRGB[i] > 1) over = Math.max(over, linRGB[i] - 1);
  }
  const OOG_MARGIN = 0.18;
  const fade = over <= 0 ? 1 : (over >= OOG_MARGIN ? 0 : 1 - over / OOG_MARGIN);
  if (fade <= 0) {
    return [0, 0, 0, 0];
  }
  // Progressive desaturation toward the luma gray of the clamped color,
  // proportional to the overshoot → the boundary fades smoothly instead
  // of turning magenta.
  const cl = [
    Math.max(0, Math.min(1, linRGB[0])),
    Math.max(0, Math.min(1, linRGB[1])),
    Math.max(0, Math.min(1, linRGB[2])),
  ];
  const grey = 0.2126 * cl[0] + 0.7152 * cl[1] + 0.0722 * cl[2];
  const desat = 1 - fade;
  const r = cl[0] + (grey - cl[0]) * desat;
  const g = cl[1] + (grey - cl[1]) * desat;
  const bl = cl[2] + (grey - cl[2]) * desat;
  return [
    _linearToSrgb(r),
    _linearToSrgb(g),
    _linearToSrgb(bl),
    fade,
  ];
}


/**
 * Paints the canvas with the Lab chromaticity → screen map at the given
 * L*, clipping to the gamut of the chosen reference space.
 * `GamutSlice2D` calls this entry point on every slider tick
 * and on every space change.
 *
 * @param {HTMLCanvasElement} canvas — canvas to paint (fully recomputed)
 * @param {number} L0 — Lab luminance of the plane (0..100)
 * @param {object} opts
 * @param {object} opts.range — { aMin, aMax, bMin, bMax } domain of the plane
 * @param {string} [opts.space=DEFAULT_REFERENCE_SPACE] — reference
 *        space ∈ REFERENCE_SPACES. Any unknown value falls back
 *        to the default.
 * @param {number} [opts.alpha=0.65] — global opacity applied to the
 *        in-gamut pixels (attenuation so the contours
 *        stand out). Outside the space's gamut → alpha 0.
 */
export function paintLabChromaBackground(canvas, L0, opts) {
  const range = opts?.range;
  if (!canvas || !range) return;
  const W = canvas.width;
  const H = canvas.height;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const img = ctx.createImageData(W, H);
  const data = img.data;
  const baseAlpha = Math.round((opts?.alpha ?? 0.65) * 255);
  const space = opts?.space || DEFAULT_REFERENCE_SPACE;
  const aMin = range.aMin;
  const aMax = range.aMax;
  const bMin = range.bMin;
  const bMax = range.bMax;
  const aSpan = aMax - aMin;
  const bSpan = bMax - bMin;
  let idx = 0;
  for (let y = 0; y < H; y++) {
    // Plot convention: +b* at the top → we invert y.
    const b = bMax - (y / (H - 1)) * bSpan;
    for (let x = 0; x < W; x++) {
      const a = aMin + (x / (W - 1)) * aSpan;
      const [r, g, bl, fade] = labToBackgroundRgb(L0, a, b, space);
      if (fade > 0) {
        data[idx] = Math.round(r * 255);
        data[idx + 1] = Math.round(g * 255);
        data[idx + 2] = Math.round(bl * 255);
        data[idx + 3] = Math.round(baseAlpha * fade);
      } else {
        data[idx] = 0;
        data[idx + 1] = 0;
        data[idx + 2] = 0;
        data[idx + 3] = 0;
      }
      idx += 4;
    }
  }
  ctx.putImageData(img, 0, 0);
}
