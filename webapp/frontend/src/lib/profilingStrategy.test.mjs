/**
 * Pure helpers of the two-pass adaptive profiling UI (lib/profilingStrategy.js).
 * Run: node webapp/frontend/src/lib/profilingStrategy.test.mjs
 */
import {
  STRATEGY_TWO_PASS, activeRuns, currentPatches, formFromParams, hasFwa, intermediateState, lastLater,
  offersQuasiRandomFallback, paperTotal, paramsFromForm, printCheck, stepAction, strategyBadge,
  strategyCreateRequest, variantSources, withFwa,
} from './profilingStrategy.js';

let pass = 0, fail = 0;
function assert(cond, msg) { if (cond) { pass++; console.log(`  ✓ ${msg}`); } else { fail++; console.error(`  ✗ ${msg}`); } }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// the server's default parameters (GET /api/profiling-strategies → default_params)
const DEFAULTS = {
  pass1: { chart: { patches: 400, ofps: 'G', grey_steps: 0 }, profile: { quality: 'm', smoothing: 2.0, b2a: 'l' } },
  pass2: { chart: { patches: 2000, ofps: 'G', neutral_emphasis: 0.75, neutral_steps: 0 },
           profile: { quality: 'h', source_gamut: 'AdobeRGB', viewcond_in: 'mt', viewcond_out: 'pp', fwa: false } },
};

console.log('form ⇄ parameters');
const form = formFromParams(DEFAULTS);
assert(form.pass1Patches === 400 && form.pass2Patches === 2000 && form.neutral === 0.75 && form.smoothing === 2.0,
  'the form starts from the definition');
assert(form.finalQuality === 'h' && form.gamut === 'AdobeRGB' && form.vcIn === 'mt' && form.vcOut === 'pp',
  'final profile settings from the definition');
const p1 = paramsFromForm({ ...form, pass1Patches: '350', smoothing: '1.2' }, 'pass1');
assert(p1.pass1.chart.patches === 350 && p1.pass1.profile.smoothing === 1.2, 'pass 1 freezes its own values (numbers)');
assert(p1.pass2.chart.patches === 2000 && p1.pass2.profile.source_gamut === 'AdobeRGB', 'pass 1 also freezes pass 2 and the final profile');
assert(!('quality' in p1.pass1.profile) && !('b2a' in p1.pass1.profile), 'only the parameters the UI offers are sent');
const p2 = paramsFromForm(form, 'pass2');
assert(eq(Object.keys(p2), ['pass2']), 'a later pass sends its own parameters only (an earlier pass is frozen)');
assert(currentPatches(form, 'pass1') === 400 && currentPatches(form, 'pass2') === 2000, 'patch count of the pass being composed');
assert(eq(formFromParams(null).pass2Patches, 2000), 'sane defaults before the definitions are loaded');

console.log('create request');
const r1 = strategyCreateRequest({ passId: 'pass1', form, run: 'X', buildId: 'Y' });
assert(r1.id === STRATEGY_TWO_PASS && r1.pass_id === 'pass1' && r1.run === null && r1.precondition_build_id === null,
  'pass 1 starts a run: no run, no precondition');
assert(!('targen_flags' in r1) && !JSON.stringify(r1).includes('-c'), 'no targen line and no -c: the server composes them');
const r2 = strategyCreateRequest({ passId: 'pass2', form, run: 'CHT-20261126-1012-AB', buildId: '20261126T101500Z-abc123' });
assert(r2.run === 'CHT-20261126-1012-AB' && r2.precondition_build_id === '20261126T101500Z-abc123',
  'pass 2 names its run and the exact intermediate version');

console.log('FWA flag');
assert(withFwa('-v -qh -S AdobeRGB', true) === '-v -qh -S AdobeRGB -f', '-f added');
assert(withFwa('-v -qh -f -S AdobeRGB', false) === '-v -qh -S AdobeRGB', '-f removed');
assert(withFwa('-v -qh -fD50', true) === '-v -qh -f', 'one -f only');
assert(hasFwa('-v -qh -f') && !hasFwa('-v -qh'), 'detected');

console.log('badge, steps, runs');
assert(eq(strategyBadge({ id: STRATEGY_TWO_PASS, pass: 'pass2', role: 'final', run: 'R' }),
  { id: STRATEGY_TWO_PASS, pass: 'pass2', role: 'final', index: 2, total: 2, run: 'R' }), 'pass 2 of 2');
assert(strategyBadge(null) === null, 'nothing for a one-pass chart');
assert(stepAction('pass2_create') === 'create_pass2', 'creating pass 2 opens its composition');
assert(stepAction('intermediate_build') === 'open_first', 'the intermediate profile is built on the first chart');
assert(stepAction('final_build') === 'open_last' && stepAction('pass2_measure') === 'open_last', 'later steps go to the last chart');
assert(stepAction('pass1_print') === 'print_first' && stepAction('pass2_print') === 'print_last',
       'a pass created but never printed is printed from the resume card (no dead end)');
assert(eq(activeRuns([{ next_step: 'done' }, { next_step: 'pass2_print', run: 'A' }, { next_step: 'first_pass_missing' }]).map((r) => r.run), ['A']),
  'resume card: runs still waiting only');
assert(lastLater({ later: [{ chart_id: 'a' }, { chart_id: 'b' }] }).chart_id === 'b' && lastLater({}) === null, 'newest later chart');

console.log('paper');
assert(paperTotal(840, 1720) === 2560, 'total of the two passes');
assert(paperTotal(840, null) === null && paperTotal() === null, 'no total when one is unknown');

console.log('intermediate profile');
const first = (state, flags) => ({ intermediate: { built: true, state, colprof_flags: flags } });
assert(intermediateState({ intermediate: { built: false } }) === 'none', 'not built');
assert(intermediateState(first('stale', ['-v', '-qm', '-r', '2.0', '-bl'])) === 'stale', 'stale');
assert(intermediateState(first('up_to_date', ['-v', '-qm', '-r', '2.0', '-bl']), '-v -qm -r 2.0 -bl') === 'ready', 'ready for these settings');
assert(intermediateState(first('up_to_date', ['-v', '-qm', '-r', '2.0', '-bl']), '-v -qm -r 1.2 -bl') === 'other_settings',
  'another smoothing asked: rebuilt, never reused silently');

console.log('final variant');
assert(eq(variantSources('pass2', 'R'), { extra_chart_ids: [], nameSuffix: '' }), 'pass 2 alone by default');
assert(eq(variantSources('pass1_pass2', 'R'), { extra_chart_ids: ['R'], nameSuffix: '_p1p2' }), 'passes 1 + 2 through the existing extra sources');

console.log('errors');
assert(offersQuasiRandomFallback({ detail: { code: 'targen_failed', fallback: 'Q' } }), '-Q offered after an OFPS failure');
assert(!offersQuasiRandomFallback({ detail: { code: 'targen_failed', fallback: null } }) && !offersQuasiRandomFallback(new Error('x')),
  'nothing offered otherwise');
const blocked = printCheck({ status: 409, detail: { code: 'strategy_print_conditions_changed', differences: [{ code: 'quality_differs' }] } });
assert(blocked.kind === 'blocked' && eq(blocked.codes, ['quality_differs']), 'a blocking print difference');
const confirm = printCheck({ status: 409, detail: { code: 'strategy_print_warnings', warnings: [{ code: 'resident_changed' }] } });
assert(confirm.kind === 'confirm' && eq(confirm.codes, ['resident_changed']), 'warnings to confirm');
assert(printCheck({ status: 409, detail: { code: 'chart_media_mismatch' } }) === null, 'other print refusals untouched');

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
