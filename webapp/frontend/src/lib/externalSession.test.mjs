/**
 * Tests of lib/externalSession.js (external measurement interface states). Snapshots have
 * the backend's shape. Run: node webapp/frontend/src/lib/externalSession.test.mjs
 */
import {
  STEPS, allowed, canCreate, commandBody, conditionChoices, createPresenceWatch, createSnapshotReader, durability,
  isStale, newer, presenceState, problem, progress, promptView, publishChecks, recentEvents, startBlock, stepOf,
  stripMap, watchesPresence,
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

// ─── instrument presence while the wizard is open (P1b, note 147 §16) ──────
const flush = () => new Promise((r) => setTimeout(r, 0));          // lets every pending promise callback run
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
};
function fakeTimers() {                                              // the watch's clock, advanced by hand
  let now = 0, id = 0;
  const q = new Map();
  return {
    setTimeout(fn, ms) { const k = ++id; q.set(k, { at: now + ms, fn }); return k; },
    clearTimeout(k) { q.delete(k); },
    pending: () => q.size,
    async advance(ms) {
      now += ms;
      for (const [k, x] of [...q]) if (x.at <= now) { q.delete(k); x.fn(); }
      await flush();
    },
  };
}
const withPresence = (status, extra = {}) => ({ ...ctx, presence: { status, family_only: false, instruments: [], ...extra } });
const fresh = (status, over = {}) => snap({ actions: ['start', 'abandon'], context: withPresence(status),
                                            session: session({ passes_saved: 0, patches_saved: 0, passes: [] }), ...over });
const shown = (s, check) => presenceState(s, check).status;

console.log('Test 10 — presence as shown, and when "Start" waits');
assert(shown(fresh('present'), null) === 'checking' && startBlock(presenceState(fresh('present'), null)) === 'checking',
       'opening: "checking" until the first answer, even if an older snapshot said present; start waits');
assert(shown(null, { ok: true }) === 'checking', 'no snapshot yet: checking');
assert(shown(fresh('absent'), { ok: true }) === 'absent' && startBlock(presenceState(fresh('absent'), { ok: true })) === 'absent',
       'absent confirmed: start disabled, with its reason');
for (const s of ['other', 'several']) assert(startBlock(presenceState(fresh(s), { ok: true })) === s, `${s}: start disabled (the backend refuses it too)`);
assert(shown(fresh('present'), { ok: true }) === 'present' && startBlock(presenceState(fresh('present'), { ok: true })) === null,
       'present: start offered');
const fam = presenceState(fresh('present', { context: withPresence('present', { family_only: true }) }), { ok: true });
assert(fam.status === 'present' && fam.familyOnly && startBlock(fam) === null, 'i1Pro 3 family: present (family only), start offered');
assert(shown(fresh('error'), { ok: true }) === 'error' && startBlock(presenceState(fresh('error'), { ok: true })) === null,
       'detection error: shown as such, not as present; start left to the backend');
assert(shown(fresh('present'), { ok: false }) === 'error', 'a failed read is an error, never a presence (even over a "present" snapshot)');
assert(shown(fresh('checking'), { ok: true }) === 'checking' && startBlock(presenceState(fresh('checking'), { ok: true })) === 'checking',
       'the backend has no verdict yet (listing running): checking, start waits');
assert(shown(fresh('present', { context: withPresence('plugged') }), { ok: true }) === 'error', 'an unknown status is an error');
assert(shown(fresh('present', { context: { ...ctx, presence: null } }), { ok: true }) === 'error', 'no presence in the context: error');

console.log('Test 11 — the presence is watched only where it matters');
assert(watchesPresence(fresh('absent')) && watchesPresence(snap()), 'a session to start or to resume: watched');
assert(watchesPresence(snap({ state: 'none', session: null, actions: ['create'] })), 'no session yet: watched');
assert(!watchesPresence(snap({ state: 'ready', running: true, actions: ['pause'] })), 'live run: not watched (the engine watches the instrument)');
assert(!watchesPresence(snap({ state: 'complete', actions: ['finalize', 'correct', 'abandon'] })), 'complete: not watched');
assert(!watchesPresence(null), 'no snapshot: nothing to watch');

console.log('Test 12 — the watch: one check at a time, closing stops it');
{
  const tm = fakeTimers();
  const results = [];
  let calls = 0, d = deferred();
  const w = createPresenceWatch({ check: () => { calls++; return d.promise; }, onResult: (ok) => results.push(ok), timers: tm });
  w.start();
  assert(calls === 1, 'opening: one check at once');
  await tm.advance(10_000);
  assert(calls === 1 && tm.pending() === 0, 'no second check while the first is unanswered');
  d.resolve({ seq: 1 });
  await flush();
  assert(results.join() === 'true' && tm.pending() === 1, 'answered: reported, the next check scheduled');
  d = deferred();
  await tm.advance(1999);
  assert(calls === 1, 'not before 2 s');
  await tm.advance(1);
  assert(calls === 2, 'then again 2 s after the answer');
  d.reject(new Error('network'));
  await flush();
  assert(results.join() === 'true,false', 'a failed read is reported as not ok');
  d = deferred();
  await tm.advance(2000);
  assert(calls === 3, 'and the watch goes on after a failure');
  w.stop();
  d.resolve({ seq: 2 });
  await flush();
  assert(results.length === 2 && tm.pending() === 0, 'closed while a check is on its way: its answer is dropped, nothing scheduled');
  await tm.advance(10_000);
  assert(calls === 3, 'closed: no more checks');
}
{
  const tm = fakeTimers();
  let calls = 0;
  const w = createPresenceWatch({ check: async () => { calls++; return {}; }, onResult: () => {}, timers: tm });
  w.start();
  await flush();
  assert(tm.pending() === 1, 'between two checks: one timer');
  w.stop();
  assert(tm.pending() === 0, 'closing clears it');
  await tm.advance(10_000);
  assert(calls === 1, 'no check after closing');
}

console.log('Test 13 — snapshot reads: a late answer never puts back an older presence');
{
  const asked = [];
  const applied = [];
  const r = createSnapshotReader(() => { const d = deferred(); asked.push(d); return d.promise; }, (s) => applied.push(s.tag));
  const p1 = r.read();
  const p2 = r.read();
  await flush();
  assert(asked.length === 2, 'two reads asked (a refresh after a command, a watch check)');
  asked[1].resolve({ seq: 5, tag: 'present' });
  await p2;
  asked[0].resolve({ seq: 5, tag: 'absent' });
  await p1;
  assert(applied.join() === 'present', 'the earlier read answering last is dropped (same seq, older presence)');
  const p3 = r.shared(), p4 = r.shared();
  await flush();
  assert(asked.length === 3 && p3 === p4, 'the watch joins the read in flight: one request, not two');
  asked[2].reject(new Error('down'));
  let rejected = false;
  try { await p3; } catch { rejected = true; }
  assert(rejected && applied.length === 1, 'a failed read is reported to the caller, nothing applied');
  r.shared();
  await flush();
  assert(asked.length === 4, 'after a failure, the next check is a new request');
  asked[3].resolve({ seq: 6, tag: 'x' });
  await flush();
}

console.log('Test 14 — plug, unplug, plug again while the wizard is open');
{
  const tm = fakeTimers();
  let plugged = 'absent', current = null, check = null, reads = 0;
  const reader = createSnapshotReader(async () => { reads++; return fresh(plugged); }, (s) => { current = newer(current, s); });
  const w = createPresenceWatch({ check: () => reader.shared(), onResult: (ok) => { check = { ok }; }, timers: tm });
  const button = () => (startBlock(presenceState(current, check)) ? 'disabled' : 'enabled');
  w.start();
  assert(shown(current, check) === 'checking' && button() === 'disabled', 'opening: checking');
  await flush();
  assert(shown(current, check) === 'absent' && button() === 'disabled', '1. absent at opening: start disabled');
  plugged = 'present';
  await tm.advance(2000);
  assert(shown(current, check) === 'present' && button() === 'enabled', '2. plugged in: present and start offered within 2 s, no reload');
  plugged = 'absent';
  await tm.advance(2000);
  assert(shown(current, check) === 'absent' && button() === 'disabled', '3. unplugged at rest: start disabled again');
  plugged = 'present';
  await tm.advance(2000);
  assert(button() === 'enabled', '4. plugged in again: start comes back by itself');
  w.stop();
  await tm.advance(20_000);
  assert(reads === 4, `closed: the reads stop (${reads} in all, one per 2 s while open)`);
}

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
