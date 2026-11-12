/**
 * Smoke test for lib/naturalSort.js (patch-id ordering in the measurement tables).
 *
 * Run: node webapp/frontend/src/lib/naturalSort.test.mjs
 */
import { compareNatural } from './naturalSort.js';

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log(`  ✓ ${msg}`); }
  else { fail++; console.error(`  ✗ ${msg}`); }
}
const sorted = (arr) => [...arr].sort(compareNatural).join(',');

console.log('Test 1 — numeric ids');
assert(sorted(['10', '2', '1', '100', '21']) === '1,2,10,21,100', 'numeric strings in numeric order');
console.log('Test 2 — non-numeric ids');
assert(sorted(['A10', 'A2', 'B1', 'A1']) === 'A1,A2,A10,B1', 'prefixed ids in natural order');
assert(sorted(['1X1', '1W1', '10A1', '2A1']) === '1W1,1X1,2A1,10A1', 'SAMPLE_LOC-like ids');
console.log('Test 3 — numbers and missing values');
assert(sorted([3, 20, 1]) === '1,3,20', 'numbers are accepted');
assert([...['2', null, '1']].sort(compareNatural).join(',') === '1,2,', 'null sorted last');

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
