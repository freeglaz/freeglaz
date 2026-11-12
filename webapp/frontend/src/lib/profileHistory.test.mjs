/**
 * Tests of lib/profileHistory.js — profile status v2 and version history (phase 4D).
 * Run: node webapp/frontend/src/lib/profileHistory.test.mjs
 */
import { historyRow, orderPair, statusBadges } from './profileHistory.js';

let pass = 0, fail = 0;
function assert(cond, msg) { if (cond) { pass++; console.log(`  ✓ ${msg}`); } else { fail++; console.error(`  ✗ ${msg}`); } }
const codes = (s) => statusBadges(s).map((b) => b.code).join(',');

console.log('Test 1 — status badges follow the backend structure (not mutually exclusive)');
assert(codes({ state: 'up_to_date', toolchain_changed: false }) === 'up_to_date', 'up to date');
assert(codes({ state: 'stale', toolchain_changed: true }) === 'stale,toolchain_changed', 'stale AND tool changed');
assert(codes({ state: 'up_to_date', toolchain_changed: null }) === 'up_to_date,toolchain_unknown', 'tool version uncertain shown separately');
assert(codes({ state: 'missing_input', toolchain_changed: null }) === 'missing_input,toolchain_unknown', 'missing input');
assert(codes({ state: 'unknown', toolchain_changed: null }) === 'unknown', 'unknown: never presented as up to date');
assert(statusBadges({ state: 'stale' })[0].kind === 'warn' && statusBadges({ state: 'missing_input' })[0].kind === 'danger', 'stale is a warning, not a defect');
assert(statusBadges(null).length === 0, 'no status → nothing');

console.log('Test 2 — history rows never invent missing data');
const pres = historyRow({ build_id: '20260101T000000Z-preserved-abcd1234', kind: 'preserved', provenance: 'unknown', intact: true, n_excluded: null });
assert(pres.preserved && pres.provenanceUnknown && pres.nExcluded === null && pres.label === null, 'earlier profile: provenance unknown, counts unknown');
const b = historyRow({ build_id: '20261111T120000123Z-k3f9qa', kind: 'build', current: true, intact: true, label: 'p', base_requested: 'average',
  base_effective: 'last', n_scans: 1, n_excluded: 2, n_holes: 1, colprof_flags: ['-v', '-qh'], manifest_available: true });
assert(b.current && b.baseFallback && b.flags === '-v -qh' && b.shortId === 'k3f9qa' && b.comparable, 'build row');
assert(!historyRow({ build_id: 'x', kind: 'build', intact: false }).comparable, 'altered version not comparable');

console.log('Test 3 — comparison pair is chronological (A = reference)');
const p = orderPair(['20261111T130000000Z-bbbbbb', '20261111T120000000Z-aaaaaa']);
assert(p.a === '20261111T120000000Z-aaaaaa' && p.b === '20261111T130000000Z-bbbbbb', 'older = A, newer = B');
assert(orderPair(['x']) === null && orderPair(null) === null, 'needs exactly two');

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
