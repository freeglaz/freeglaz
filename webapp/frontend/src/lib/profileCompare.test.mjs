/**
 * Tests of lib/profileCompare.js — presentation of a comparison of two ICC versions (phase 4D).
 * Run: node webapp/frontend/src/lib/profileCompare.test.mjs
 */
import { focusFinding, statRows, topColours, whiteBlackRows } from './profileCompare.js';

let pass = 0, fail = 0;
function assert(cond, msg) { if (cond) { pass++; console.log(`  ✓ ${msg}`); } else { fail++; console.error(`  ✗ ${msg}`); } }
// shape of the backend result (values from the real bench comparison, patch 172 excluded)
const res = {
  intents: {
    relative: { measured: { n: 464, mean: 0.0066, p95: 0.0126, max: 0.9932, top: [{ rgb255: [128, 128, 128], de00: 0.9932, lab_a: [1], lab_b: [2] }] },
                grid: { n: 729, mean: 0.0099, p95: 0.0181, max: 0.9944, top: [{ rgb255: [128, 128, 128], de00: 0.9944, lab_a: [1], lab_b: [2] },
                                                                          { rgb255: [96, 128, 128], de00: 0.4, lab_a: [1], lab_b: [2] }] } },
    perceptual: { measured: { n: 464, mean: 0.0066, p95: 0.0126, max: 0.9932, top: [] } },
  },
  focus: [{ label: 'CHT/a.ti3/172', excluded_in: 'b', de00_at_point: 0.9932, near: { n: 9, mean: 0.34, max: 0.9944 },
            elsewhere: { n: 1184, mean: 0.0061, p95: 0.0154, max: 0.1156 } }],
  white_black: { white_point_tag: { de00: 0 }, device_black: { de00: 0.0019 } },
};

console.log('Test 1 — statistics rows and most displaced colours');
const rows = statRows(res);
assert(rows.length === 3 && rows[0].intent === 'relative' && rows[0].set === 'measured' && rows[2].intent === 'perceptual', 'one row per intent × set present');
const top = topColours(res);
assert(top.length === 2 && top[0].rgb255.join() === '128,128,128' && top[0].de00 === 0.9944 && top[1].de00 === 0.4, 'deduplicated, sorted by ΔE00');
console.log('Test 2 — "mainly this region" only when the numbers say so');
const f = focusFinding(res.focus[0]);
assert(f.localized && f.key === 'profile_compare.focus_localized' && f.params.at === '0.99' && f.params.side === 'B', 'patch 172: localized');
const diffuse = focusFinding({ ...res.focus[0], near: { n: 9, max: 0.3 }, elsewhere: { p95: 0.2, max: 0.5 } });
assert(!diffuse.localized && diffuse.key === 'profile_compare.focus_diffuse', 'small or spread change: not called localized');
assert(!focusFinding({ near: { n: 0 }, elsewhere: {} }).localized, 'no neighbourhood data → not localized');
console.log('Test 3 — white and black');
const wb = whiteBlackRows(res);
assert(wb.map((w) => w.key).join() === 'white_point_tag,device_black' && wb[0].de00 === 0, 'present rows only, backend values');
console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
