/**
 * Smoke test for the roll-placement helpers (lib/placement.js).
 *
 * These back the roll-mode horizontal centering: the "Center" button, its
 * availability, the X-only drag commit, and the honest "centered" indicator.
 * Only the X axis is defined on a free-length roll — there is no rollCenterY.
 *
 * Run: node webapp/frontend/src/lib/placement.test.mjs
 */
import { rollCenterX, isCenteredX, CENTER_TOL_MM, horizontalLocked, z9proRollImageX } from './placement.js';

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log(`  ✓ ${msg}`); }
  else { fail++; console.error(`  ✗ ${msg}`); }
}

console.log('Test 1 — rollCenterX');
{
  assert(rollCenterX(610, 200) === 205, '610mm roll, 200mm image → 205mm center');
  assert(rollCenterX(300, 300) === 0, 'image as wide as the roll → 0');
  assert(rollCenterX(200, 300) === -50, 'image wider than the roll → negative (clamped elsewhere)');
  // Missing dims (dims not ready yet) → null, never a bogus number.
  assert(rollCenterX(undefined, 200) === null, 'missing sheet width → null');
  assert(rollCenterX(610, null) === null, 'missing image width → null');
}

console.log('Test 2 — isCenteredX');
{
  const center = 205;
  assert(isCenteredX(205, center) === true, 'exactly on center → true');
  assert(isCenteredX(205 + CENTER_TOL_MM, center) === true, 'within tolerance → true');
  assert(isCenteredX(205 + CENTER_TOL_MM + 0.01, center) === false, 'just past tolerance → false');
  assert(isCenteredX(5, center) === false, 'default 5mm margin vs center → false (button stays enabled)');
  // Null/undefined center (dims not ready) must never read as "centered".
  assert(isCenteredX(5, null) === false, 'null center → false');
  assert(isCenteredX(undefined, center) === false, 'undefined effX → false');
}

console.log('Test 3 — Z9 Pro roll: horizontal position fixed, image centred');
{
  assert(horizontalLocked('z9pro', true) === true, 'Z9 Pro on a roll → locked');
  assert(horizontalLocked('z9pro', false) === false, 'Z9 Pro on a sheet → free');
  assert(horizontalLocked('z9', true) === false, 'Z9 on a roll → free (unchanged)');
  assert(horizontalLocked(null, true) === false, 'unknown family → free (historical behaviour)');
  assert(horizontalLocked('designjet', true) === false, 'other DesignJet → free');
  // Reference case: image 1230 mm on a 1268.8 mm roll → ~19.4 mm each side, like
  // the backend (19.4025: PAPERWIDTH and LEFTMARGIN rounded to PJL units).
  const x = z9proRollImageX(1268.8, 1230);
  assert(Math.abs(x - 19.4025) < 1e-9, `reference case x = ${x.toFixed(4)} (backend: 19.4025)`);
  assert(Math.abs((1268.8 - 1230 - x) - x) < 0.05, 'same margin on both sides');
  assert(isCenteredX(x, rollCenterX(1268.8, 1230)), 'reads as centred for the indicator');
  assert(Math.abs(z9proRollImageX(1240.2, 1230) - 5.1) < 0.04, 'image almost as wide as the roll → ~5 mm');
  assert(z9proRollImageX(null, 1230) === null && z9proRollImageX(1268.8, undefined) === null, 'missing dims → null');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
