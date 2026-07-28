/**
 * Qualitative palette for comparing N ICC profiles
 * (profile-compare — visual view).
 *
 * Choice: **Okabe-Ito 8-color palette**, designed to stay
 * distinguishable by color-blind users (deuteranopia/protanopia) — it's
 * the canonical palette for multi-series scientific visualization.
 * Ref. Okabe & Ito 2008, « Color Universal Design ».
 *
 * We avoid pure black (hard to read in dark mode on a dark background):
 * the palette starts with the saturated hues and the « dark gray »
 * only comes in 8th position if there really are 8 profiles.
 *
 * Not via the design-system CSS tokens: the tokens cover
 * semantics (accent, danger, success, icc-warn) — not a qualitative
 * palette with 8 entries. This palette is a SPECIFIC TOOL
 * for multi-series viz, isolated in this module so as not to pollute
 * the other components. Documented, not arbitrary.
 */

const OKABE_ITO = [
  { name: 'orange',          hex: '#E69F00' },
  { name: 'sky_blue',        hex: '#56B4E9' },
  { name: 'bluish_green',    hex: '#009E73' },
  { name: 'vermillion',      hex: '#D55E00' },
  { name: 'reddish_purple',  hex: '#CC79A7' },
  { name: 'blue',            hex: '#0072B2' },
  { name: 'yellow',          hex: '#F0E442' },
  { name: 'dark_gray',       hex: '#595959' },
];


/**
 * Returns the color (hex) of the i-th profile in a series of N.
 * Cyclic if i >= 8 (should not happen — _COMPARE_MAX_PROFILES=8).
 */
export function colorForIndex(i) {
  return OKABE_ITO[i % OKABE_ITO.length].hex;
}


/**
 * Variant: array of colors for N profiles, in order.
 */
export function paletteForN(n) {
  return Array.from({ length: n }, (_, i) => colorForIndex(i));
}


export { OKABE_ITO };
