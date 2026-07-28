/**
 * Smoke test for the roll-placement helpers (lib/placement.js).
 *
 * These back the roll-mode horizontal centering: the "Center" button, its
 * availability, the X-only drag commit, and the honest "centered" indicator.
 * Only the X axis is defined on a free-length roll — there is no rollCenterY.
 *
 * Run: node webapp/frontend/src/lib/placement.test.mjs
 */
import { rollCenterX, isCenteredX, CENTER_TOL_MM } from './placement.js';

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

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
