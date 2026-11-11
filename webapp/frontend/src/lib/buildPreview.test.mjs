/**
 * Tests of lib/buildPreview.js — the single build contract of the interface (phase 4D).
 * Run: node webapp/frontend/src/lib/buildPreview.test.mjs
 */
import { buildRequestBody, chartStateStamp, classifyBuildError, previewKey, previewRequest, summarizePreview } from './buildPreview.js';

let pass = 0, fail = 0;
function assert(cond, msg) { if (cond) { pass++; console.log(`  ✓ ${msg}`); } else { fail++; console.error(`  ✗ ${msg}`); } }

console.log('Test 1 — one request for every build path');
const sel = { nKept: 2, profileBase: 'average', colprofFlags: ' -v -qh ', extraSources: { extra_chart_ids: ['CHT-B'], source_profiles: [{ path: '/p.icc' }], name: 'my-prof' } };
const body = buildRequestBody(sel);
assert(body.profile_base === 'average' && body.colprof_flags === '-v -qh' && body.extra_chart_ids[0] === 'CHT-B' && body.name === 'my-prof', 'body');
assert(buildRequestBody({ ...sel, nKept: 1 }).profile_base === 'last', 'fewer than 2 kept scans → last');
const pr = previewRequest({ ...body, on_conflict: 'replace' });
assert(pr.policy === 'apply_all' && !('name' in pr) && !('on_conflict' in pr), 'preview request: apply_all, without name / collision intent');

console.log('Test 2 — any change invalidates the preview (and its confirmation)');
const k = previewKey('CHT-A', body, 's1');
assert(k === previewKey('CHT-A', { ...body, name: 'other', on_conflict: 'keep_both' }, 's1'), 'name / intent do not change the inputs');
assert(k !== previewKey('CHT-A', { ...body, profile_base: 'last' }, 's1'), 'base');
assert(k !== previewKey('CHT-A', { ...body, colprof_flags: '-v -qm' }, 's1'), 'colprof flags');
assert(k !== previewKey('CHT-A', { ...body, extra_chart_ids: [] }, 's1'), 'sources');
const d1 = { scans: [{ ti3: 'a.ti3', role: 'included', rejected_readings: ['2'] }] };
const d2 = { scans: [{ ti3: 'a.ti3', role: 'included', rejected_readings: ['2', '9'] }] };
const d3 = { scans: [{ ti3: 'a.ti3', role: 'excluded', rejected_readings: ['2'] }] };
assert(chartStateStamp(d1) !== chartStateStamp(d2) && chartStateStamp(d1) !== chartStateStamp(d3), 'exclusions and roles are in the stamp');

console.log('Test 3 — preview summary (numbers from the backend)');
const p = { base_requested: 'average', base_effective: 'last', inputs_fingerprint: 'sha256:new', current_inputs_fingerprint: 'sha256:old',
  current_build_id: 'b1', rejections_applied: [{ sids: ['1', '2'] }], rejections_not_applied: [{ sids: ['7'], reason: 'not_used_by_base_last' }],
  rejections_unmatched: [], holes: [{}], guards: [{ code: 'HOLE_EDGE', level: 'warning' }], refusals: [], would_be_refused: false,
  recommend_remeasure: false, reasons: [], newly_applied_vs_legacy: [{ sids: ['2'] }], qc_strong_included: [{ sids: ['5'] }] };
const s = summarizePreview(p);
assert(s.baseFallback && s.nExcluded === 2 && s.nInactive === 1 && s.nHoles === 1 && s.warnings.length === 1, 'counts and base fallback');
assert(s.nNewlyApplied === 1 && s.nStrongIncluded === 1 && s.inputsChangedSinceCurrent && s.needsConfirmation, 'rule change, strong suspects, differs from current → confirmation');
const plain = summarizePreview({ ...p, base_requested: 'last', rejections_applied: [], rejections_not_applied: [], holes: [], guards: [],
                                  newly_applied_vs_legacy: [], qc_strong_included: [], current_inputs_fingerprint: 'sha256:new' });
assert(!plain.needsConfirmation && !plain.inputsChangedSinceCurrent, 'nothing special → no extra confirmation');
const refused = summarizePreview({ ...p, would_be_refused: true, refusals: ['WHITE_LAST_READING'], guards: [{ code: 'WHITE_LAST_READING', level: 'refused' }] });
assert(refused.refused && !refused.needsConfirmation && refused.warnings.length === 0, 'refusal: no confirmation possible, refusals not counted as warnings');
assert(summarizePreview(null) === null, 'no preview → null');

console.log('Test 4 — build errors, never an automatic retry');
const e = (status, detail) => Object.assign(new Error('x'), { status, detail });
assert(classifyBuildError(e(409, { error: 'inputs_changed' })).kind === 'inputs_changed', 'inputs changed');
assert(classifyBuildError(e(409, { error: 'coverage_refused' })).kind === 'coverage_refused', 'coverage refused');
assert(classifyBuildError(e(409, { error: 'name_conflict' })).kind === 'name_conflict', 'name conflict');
assert(classifyBuildError(e(422, { error: 'fingerprint_required' })).kind === 'fingerprint_required', 'fingerprint required');
assert(classifyBuildError(e(422, 'too long')).kind === 'name_length' && classifyBuildError(e(400, 'x')).kind === 'name_invalid', 'name errors');
assert(classifyBuildError(e(500, undefined)).kind === 'error', 'other');

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
