/**
 * Placement helpers — roll-mode horizontal centering (X only).
 *
 * A free-length (economical) roll has a FIXED width but no reference height,
 * so only the horizontal axis can be centered. These helpers compute the
 * horizontal center and test whether an effective X sits on it. Vertical
 * centering is undefined on a roll and deliberately absent here (the roll
 * length grows with the image → no "center" to aim at).
 *
 * Single source of truth for both the "Center" button (App.handlePositionReset
 * + button availability) and the honest "centered" indicator (PaperPreview
 * info strip, via Viewer).
 */

// Tolerance (mm) for "is X on the horizontal center". Absorbs float noise
// from the mm↔px drag conversions and the backend delta round-trip.
export const CENTER_TOL_MM = 0.1;

// Economical-roll auto margin (mm). Mirrors the backend ROLL_ECON_MARGIN_MM
// (print_geometry.py): image flush at top-left + 5 mm. Used for the optimistic
// front render so a roll's auto anchor matches the backend without a round-trip.
export const ROLL_ECON_MARGIN_MM = 5;

/**
 * Horizontal center (absolute mm from the left edge) for an image of width
 * `imageWidthMm` on a sheet/roll of width `sheetWidthMm`.
 * Returns null if either dimension is missing (dims not ready yet).
 */
export function rollCenterX(sheetWidthMm, imageWidthMm) {
  if (typeof sheetWidthMm !== 'number' || typeof imageWidthMm !== 'number') return null;
  return (sheetWidthMm - imageWidthMm) / 2;
}

/** True if `effX` (absolute mm) sits on `centerX` within CENTER_TOL_MM. */
export function isCenteredX(effX, centerX) {
  return typeof effX === 'number' && typeof centerX === 'number'
    && Math.abs(effX - centerX) <= CENTER_TOL_MM;
}

// ─── Z9 Pro on a roll ──────────────────────────────────────────────────────
// The backend lays a Z9 Pro roll print out with page_layout "z9pro_roll": same
// PJL as the economical roll (LEFTMARGIN 5 mm), the image drawn under the PJL
// window the Pro centres on the roll → the image is centred and its horizontal
// position cannot be moved (a non-zero X delta is refused by the backend).

/** True when the horizontal position is fixed: a Z9 Pro printing on a roll.
 *  Unknown family (null) or any other printer: false (historical behaviour). */
export function horizontalLocked(family, isRoll) {
  return family === 'z9pro' && Boolean(isRoll);
}

// Mirrors lib/z9_client/printing.py (PJL units of 1/720 in, RIGHTMARGIN 142).
const PJL_PER_MM = (72 / 25.4) * 10;
const RIGHTMARGIN_PJL = 142;
const pjl = (mm) => Math.round(mm * PJL_PER_MM);

/** Where the image lands on a Z9 Pro roll (mm from the left edge), as the
 *  backend's z9pro_roll_image_x_mm: inside the centred PJL window. Used only
 *  before the first preview; the backend's auto_x_mm is the source of truth.
 *  Returns null if a dimension is missing. */
export function z9proRollImageX(rollWidthMm, imageWidthMm, leftMarginMm = ROLL_ECON_MARGIN_MM) {
  if (typeof rollWidthMm !== 'number' || typeof imageWidthMm !== 'number') return null;
  const pageMm = (pjl(leftMarginMm) + pjl(imageWidthMm) + RIGHTMARGIN_PJL) / PJL_PER_MM;
  return (rollWidthMm - pageMm) / 2 + pjl(leftMarginMm) / PJL_PER_MM;
}
