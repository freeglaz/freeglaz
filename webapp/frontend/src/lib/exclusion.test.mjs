/**
 * Tests of lib/exclusion.js — presentation of the explicit exclusion of a reading.
 * Impacts shaped like the backend's (POST …/exclusion, phase 4C).
 * Run: node webapp/frontend/src/lib/exclusion.test.mjs
 */
import { EXCLUSION_LEVELS, classifyExclusionError, impactView, maxLevel, reasonLabel, reasonPayload, riskView } from './exclusion.js';

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log(`  ✓ ${msg}`); } else { fail++; console.error(`  ✗ ${msg}`); }
}
const impact = (over = {}) => ({
  reading: { position: 'interior', corner: null, rgb: [50, 50, 50] }, other_compatible_readings_in_chart: 0,
  chart_level_refusal: null, level: 'info',
  bases: { last: { reading_used: true, hole: true, level: 'info', refusals: [], recommend_remeasure: false, reasons: [] } },
  ...over,
});

console.log('Test 1 — reasons are informative strings');
assert(reasonPayload('doubtful') === 'doubtful' && reasonPayload('visible_defect') === 'visible_defect', 'known codes');
assert(reasonPayload('other', '  trace de doigt ') === 'other: trace de doigt' && reasonPayload('other', '') === 'other', 'other + free text');
assert(reasonPayload('', '') === null && reasonPayload('x'.repeat(0)) === null, 'no reason → null');
assert(reasonPayload('other', 'y'.repeat(500)).length <= 7 + 180, 'free text bounded');
assert(reasonLabel('visible_defect').key === 'exclusion.reason.visible_defect' && reasonLabel('other: abc').text === 'abc', 'labels');
assert(reasonLabel(null) === null && reasonLabel('free text').text === 'free text', 'unknown text kept');

console.log('Test 2 — backend errors are classified, a refusal is not a bug');
const err = (status, detail) => Object.assign(new Error('x'), { status, detail });
const r = classifyExclusionError(err(409, { error: 'exclusion_refused', code: 'WHITE_LAST_READING', impact: impact() }));
assert(r.kind === 'refused' && r.code === 'WHITE_LAST_READING' && r.impact, '409 exclusion_refused carries the impact');
assert(classifyExclusionError(err(409, { error: 'scan_excluded' })).kind === 'scan_excluded', '409 scan_excluded');
assert(classifyExclusionError(err(404, { error: 'unknown_reading' })).kind === 'not_found', '404');
assert(classifyExclusionError(err(422, { error: 'unreadable' })).kind === 'unreadable', '422');
assert(classifyExclusionError(new TypeError('Failed to fetch')).kind === 'network', 'network failure');
assert(classifyExclusionError(err(500, undefined)).kind === 'error', '5xx');

console.log('Test 3 — impact view follows the backend levels');
assert(impactView(impact()).headline === 'exclusion.impact.interior' && impactView(impact()).canConfirm, 'interior: information, confirmable');
const dup = impactView(impact({ other_compatible_readings_in_chart: 2, level: 'ok' }));
assert(dup.headline === 'exclusion.impact.duplicate' && dup.others === 2 && dup.level === 'ok', 'duplicate: simple exclusion');
assert(impactView(impact({ reading: { position: 'edge' }, level: 'warning' })).headline === 'exclusion.impact.edge', 'edge');
const corner = impactView(impact({ reading: { position: 'corner', corner: 'red' }, level: 'strong_warning' }));
assert(corner.headline === 'exclusion.impact.corner' && corner.level === 'strong_warning' && corner.canConfirm, 'corner: strong warning, still confirmable');
const white = impactView(impact({ reading: { position: 'corner', corner: 'white' }, chart_level_refusal: 'WHITE_LAST_READING', level: 'refused' }));
assert(white.headline === 'exclusion.impact.refused_white' && !white.canConfirm && white.level === 'refused', 'last white refused, no confirm');
const black = impactView(impact({ reading: { position: 'corner', corner: 'black' }, chart_level_refusal: 'BLACK_LAST_READING' }));
assert(black.headline === 'exclusion.impact.refused_black' && !black.canConfirm, 'last black refused');
const two = impactView(impact({ level: 'refused', bases: {
  last: { reading_used: true, hole: true, level: 'refused', refusals: ['WHITE_LAST_READING'] },
  average: { reading_used: true, hole: false, level: 'ok', refusals: [], recommend_remeasure: true } } }));
assert(two.basesDiffer && two.bases.length === 2 && two.recommend, 'last vs average consequences differ, recommendation surfaced');
assert(two.canConfirm === false, 'a per-base refusal blocks the confirmation shown here (backend level refused)');
assert(impactView(null) === null, 'no impact → null');

console.log('Test 4 — risk cells and severity');
assert(riskView({ level: 'warning' }).key === 'exclusion.risk.warning' && riskView(null) === null, 'risk view');
assert(maxLevel(['ok', 'info', 'strong_warning', 'warning']) === 'strong_warning' && maxLevel([]) === 'ok', 'max level');
assert(EXCLUSION_LEVELS.join() === 'ok,info,warning,strong_warning,refused', 'levels mirror the backend severity order');

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
