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
