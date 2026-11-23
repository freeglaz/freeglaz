/**
 * Tests of lib/externalSession.js (external measurement interface states). Snapshots have
 * the backend's shape. Run: node webapp/frontend/src/lib/externalSession.test.mjs
 */
import {
  STEPS, allowed, canCreate, commandBody, conditionChoices, durability, isStale, newer, problem, progress,
  promptView, publishChecks, recentEvents, stepOf, stripMap,
} from './externalSession.js';

let pass = 0, fail = 0;
function assert(cond, msg) { if (cond) { pass++; console.log(`  ✓ ${msg}`); } else { fail++; console.error(`  ✗ ${msg}`); } }

const labels = Array.from({ length: 77 }, (_, i) => (i < 26 ? String.fromCharCode(65 + i)
  : String.fromCharCode(64 + Math.floor(i / 26)) + String.fromCharCode(65 + (i % 26))));
const session = (over = {}) => ({
  passes_total: 77, useful_patches: 2000, passes_saved: 30, patches_saved: 780, complete: false,
  passes: labels.slice(0, 30), pass_labels: labels, durability: 'full', durability_warning: null, suspended: null,
  last_stop: null, identity: { target_instrument: 'X-Rite ColorMunki', devcalstd: 'XRGA', n_bands: 36, bands: [380, 730] },
  engine_origin: 'approved', accepted_warnings: 0, corrections: [], ...over,
});
const ctx = { model: 'colormunki', filter_options: [], filter_required: false, chart: { patch_count: 2000, n_strips: 77 },
              engine: { ok: true, origin: 'approved', publishable: true }, presence: { status: 'present' } };
const snap = (over = {}) => ({ seq: 10, state: 'idle', state_seq: 8, running: false, actions: ['start', 'abandon', 'correct'],
                               prompt: null, session: session(), context: ctx, notices: [], ...over });

console.log('Test 1 — the step follows the backend state');
assert(stepOf(null) === 'prepare', 'nothing yet: prepare');
assert(stepOf(snap({ state: 'none', session: null, actions: ['create'] })) === 'prepare', 'no session: prepare');
assert(stepOf(snap({ session: session({ passes_saved: 0, patches_saved: 0, passes: [] }) })) === 'prepare', 'empty session: prepare');
assert(stepOf(snap()) === 'read', 'paused session with saved strips: read (resume)');
for (const s of ['starting', 'calibration', 'calibrating', 'calibrated']) assert(stepOf(snap({ state: s, running: true })) === 'calibrate', `running ${s}: calibrate`);
for (const s of ['ready', 'saving', 'saved', 'warning', 'finishing', 'stopping']) assert(stepOf(snap({ state: s, running: true })) === 'read', `running ${s}: read`);
assert(stepOf(snap({ state: 'complete' })) === 'publish' && stepOf(snap({ state: 'published', session: null })) === 'publish', 'complete / published: publish');
assert(STEPS.length === 4, 'four steps');

console.log('Test 2 — commands carry the state and seq they were decided on');
assert(JSON.stringify(commandBody(snap(), { command: 'retry' })) === JSON.stringify({ expect_state: 'idle', seq: 8, command: 'retry' }),
       'body = {expect_state, seq (state_seq), …}');
assert(isStale({ status: 409, detail: { code: 'stale_command' } }) && !isStale({ status: 409, detail: { code: 'run_active' } }), 'stale refusal recognised');
const a = snap({ seq: 12 }), b = snap({ seq: 11 });
assert(newer(a, b) === a && newer(b, a) === a && newer(null, b) === b, 'an older snapshot never replaces a newer one');

console.log('Test 3 — only the actions the backend allows');
assert(allowed(snap(), 'start') && !allowed(snap(), 'finalize'), 'allowed() reads the snapshot');
assert(!allowed(snap({ actions: undefined }), 'start') && !allowed(null, 'start'), 'nothing allowed without actions');
const ready = snap({ state: 'ready', running: true, actions: ['prev', 'next', 'next_unread', 'pause'],
                     prompt: { kind: 'ready', pass: 'AE', read_before: false, all_read: false } });
assert(!['start_reading', 'trigger', 'read'].some((x) => allowed(ready, x)), 'no action starts a reading');

console.log('Test 4 — counts are the saved ones, never inferred');
const p = progress(snap());
assert(p.passesSaved === 30 && p.passesTotal === 77 && p.patchesSaved === 780 && p.patchesTotal === 2000 && p.pct === 39, '30/77 strips, 780/2000 patches, 39 %');
const pNone = progress(snap({ state: 'none', session: null }));
assert(pNone.passesSaved === 0 && pNone.passesTotal === 77 && pNone.patchesTotal === 2000, 'no session: totals from the chart');

console.log('Test 5 — durability kinds stay apart');
assert(durability(snap()).kind === 'full', 'full');
const fullAfterReduced = durability(snap({ session: session({ durability: 'full', durability_warning: { count: 1 } }) }));
assert(fullAfterReduced.kind === 'full' && fullAfterReduced.tone === 'success' && fullAfterReduced.lasting,
       'last save full after an earlier reduced one: full (green), the earlier one reported apart');
const red = durability(snap({ session: session({ durability: 'reduced', durability_warning: { count: 3 } }) }));
assert(red.kind === 'reduced' && red.tone === 'warn' && red.lasting, 'reduced: a warning, lasting');
const nd = durability(snap({ session: session({ durability: 'not_durable' }) }));
assert(nd.kind === 'not_durable' && nd.tone === 'danger', 'not durable: danger, not the same as reduced');
const ref = durability(snap({ session: session({ suspended: { reason: 'checkpoint_refused' } }) }));
assert(ref.kind === 'refused' && ref.tone === 'danger', 'refused');
assert(durability(snap({ session: session({ durability: 'recovered' }) })).kind === 'recovered', 'recovered after an interruption');

console.log('Test 6 — strip map');
const m = stripMap({ ...ready, acquired_not_saved: null });
assert(m.length === 77 && m.filter((c) => c.status === 'saved').length === 30, '77 cells, 30 saved');
assert(m.find((c) => c.label === 'AE').status === 'current', 'the strip chartread asks for is current');
const m2 = stripMap(snap({ state: 'saving', running: true, acquired_not_saved: 'AE', pass: 'AE' }));
assert(m2.find((c) => c.label === 'AE').status === 'acquired', 'read but not saved yet: acquired');

console.log('Test 7 — problems always come with the saved counts');
const lost = problem(snap({ state: 'stopping', running: true, error: { kind: 'instrument_lost' } }));
assert(lost.code === 'instrument_lost' && lost.passes === 30 && lost.patches === 780, 'instrument lost, 30 strips / 780 patches saved');
const after = problem(snap({ session: session({ last_stop: { reason: 'error:instrument_lost', error: { kind: 'instrument_lost' } } }) }));
assert(after.code === 'instrument_lost', 'still shown once the run has ended');
assert(problem(snap({ session: session({ suspended: { reason: 'checkpoint_refused' } }) })).code === 'checkpoint_refused', 'refused save');
assert(problem(snap({ session: session({ suspended: { reason: 'checkpoint_not_durable' } }) })).code === 'checkpoint_not_durable', 'not durable');
assert(problem(snap({ session: session({ last_run_end: 'interrupted', last_stop: { reason: 'error:instrument_lost', error: { kind: 'instrument_lost' } } }) })).code === 'interrupted',
       'a run closed at start-up: interrupted (the older last_stop is not shown)');
assert(problem(snap({ session: session({ last_stop: { reason: 'backend_stop' } }) })).tone === 'warn', 'paused by a backend stop: a warning');
assert(problem(snap({ session: session({ last_stop: { reason: 'exited' } }) })).code === 'exited', 'chartread exited by itself');
assert(problem(snap({ session: session({ last_stop: { reason: 'user_pause' } }) })) === null, 'a pause is not a problem');
assert(problem(snap({ running: true, error: { kind: 'something_new' } })).code === 'engine_error', 'unknown error kind: generic');

console.log('Test 8 — prompts, conditions, creation');
assert(promptView(ready).type === 'ready' && promptView(ready).pass === 'AE', 'strip prompt');
const cal = promptView(snap({ prompt: { kind: 'calibration_prompt', calibration: 'sensor_position', instruction: 'Set instrument sensor…' } }));
assert(cal.type === 'calibration' && cal.calibration === 'sensor_position' && !cal.retry, 'calibration prompt');
assert(promptView(snap({ prompt: { kind: 'wrong_strip', read: 'H', expected: 'C' } })).kind === 'wrong_strip', 'warning prompt');
const i1 = snap({ state: 'none', session: null, actions: ['create'],
                  context: { ...ctx, model: 'i1pro3plus', filter_required: true,
                             filter_options: [{ option: 'n', condition: 'M0' }, { option: '5', condition: 'M1' }, { option: 'u', condition: 'M2' }] } });
assert(conditionChoices(i1).map((c) => c.condition).join() === 'M0,M1,M2', 'i1Pro 3 Plus: M0, M1, M2 offered');
assert(!canCreate(i1, null) && canCreate(i1, 'u'), 'no default condition: creation waits for an explicit choice');
assert(!canCreate(snap({ state: 'none', session: null, actions: ['create'], context: { ...ctx, engine: { ok: false, code: 'engine_missing' } } }), null),
       'no engine: no creation');

console.log('Test 9 — publication checks');
const done = snap({ state: 'complete', actions: ['finalize', 'correct', 'abandon'],
                    session: session({ passes_saved: 77, patches_saved: 2000, complete: true, passes: labels }) });
assert(publishChecks(done).every((c) => c.ok), 'complete, spectral, approved: every check passes');
const dev = snap({ ...done, session: { ...done.session, engine_origin: 'development' } });
assert(!publishChecks(dev).find((c) => c.key === 'provenance').ok, 'development engine: provenance check fails');
const noSpec = snap({ ...done, session: { ...done.session, identity: { ...done.session.identity, n_bands: 0 } } });
assert(!publishChecks(noSpec).find((c) => c.key === 'spectral').ok, 'no spectra: spectral check fails');
assert(recentEvents(snap({ notices: [{ kind: 'ready', seq: 1 }, { kind: 'checkpoint', seq: 2 }] })).length === 1, 'events: only readable ones');

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
