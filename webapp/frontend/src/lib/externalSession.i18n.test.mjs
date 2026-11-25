/**
 * i18n of the external measurement interface:
 *  1. fr / en parity of the external_measure catalog, no empty string;
 *  2. every static key used by the ExternalMeasure components and the hook exists in both;
 *  3. every code the interface translates dynamically mirrors the backend and is translated:
 *     run states, problems, durability kinds, calibration kinds, warning prompts, presence,
 *     events, engine origins, and every error code the service and the engine resolver send;
 *  4. the components never send a key to chartread nor offer a reading trigger;
 *  5. the presence watch runs only while the wizard is open, and "Start" waits for the instrument:
 *     every reason it may wait for has its text, and so has every refusal of the backend.
 * Run: node webapp/frontend/src/lib/externalSession.i18n.test.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, '..');
const repo = join(src, '..', '..', '..');
const read = (p) => readFileSync(p, 'utf-8');
const fr = JSON.parse(read(join(src, 'i18n/locales/fr.json')));
const en = JSON.parse(read(join(src, 'i18n/locales/en.json')));

let pass = 0, fail = 0;
function assert(cond, msg) { if (cond) { pass++; console.log(`  ✓ ${msg}`); } else { fail++; console.error(`  ✗ ${msg}`); } }
const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? flat(v, `${p}${k}.`) : [`${p}${k}`]));
const get = (cat, key) => key.split('.').reduce((o, k) => (o == null ? undefined : o[k]), cat);
const has = (cat, key) => get(cat, key) !== undefined || (get(cat, `${key}_one`) !== undefined && get(cat, `${key}_other`) !== undefined);
const both = (key) => has(fr, key) && has(en, key);

console.log('Test 1 — fr / en parity of external_measure');
const kf = flat(fr.external_measure).sort(), ke = flat(en.external_measure).sort();
assert(JSON.stringify(kf) === JSON.stringify(ke), `same ${kf.length} keys in fr and en`);
assert(kf.every((k) => String(get(fr.external_measure, k)).trim() && String(get(en.external_measure, k)).trim()), 'no empty string');
assert(fr.measurement.pending === undefined && en.measurement.pending === undefined, 'the "not integrated yet" texts are gone');

console.log('Test 2 — static keys of the components');
const files = ['components/ExternalMeasure/ExternalMeasureWizard.jsx', 'components/ExternalMeasure/ExternalMeasurePanel.jsx',
  'components/ExternalMeasure/StripMap.jsx'];
const used = new Set();
for (const f of files) for (const m of read(join(src, f)).matchAll(/t\(\s*'((?:external_measure|errors|measurement|common)\.[a-z0-9_.]+)'/gi)) used.add(m[1]);
const missing = [...used].filter((k) => !both(k));
assert(used.size > 40, `${used.size} static keys found`);
assert(missing.length === 0, `all present ${missing.length ? JSON.stringify(missing) : ''}`);

console.log('Test 3 — dynamic codes mirror the backend');
const service = read(join(repo, 'webapp/backend/services/external_measure.py'));
const engine = read(join(repo, 'lib/z9_client/chartread_fgk/engine.py'));
const session = read(join(repo, 'lib/z9_client/chartread_fgk/session.py'));
const run = read(join(repo, 'lib/z9_client/chartread_fgk/run.py'));
const lib = read(join(src, 'lib/externalSession.js'));
const phases = /_PUBLIC_PHASES = \{([^}]*)\}/.exec(service)[1].match(/"([a-z_]+)"/g).map((s) => s.replaceAll('"', ''));
const states = [...phases, 'stopping', 'none', 'idle', 'complete', 'published', 'abandoned'];
assert(states.every((s) => both(`external_measure.state.${s}`)), `every state translated (${states.length})`);
const svcCodes = [...service.matchAll(/ExternalMeasureError\(\d{3}, "([a-z_]+)"/g)].map((m) => m[1]);
const engCodes = [...engine.matchAll(/_fail\("([a-z_]+)"/g)].map((m) => m[1]);
const sessCodes = [...session.matchAll(/SessionError\("([a-z_]+)"/g)].map((m) => m[1]).filter((c) => c !== 'internal');
// start() refuses with instrument_<status> for every status but present (usb_presence + the probe)
const presStatuses = ['absent', 'other', 'several', 'error'];
const presCodes = presStatuses.map((s) => `instrument_${s}`);
const errs = [...new Set([...svcCodes, ...engCodes, ...sessCodes, ...presCodes, 'measurement_empty', 'measurement_not_spectral'])];
const untranslated = errs.filter((c) => !both(`errors.${c}`));
assert(svcCodes.length > 10 && engCodes.length > 8, `${svcCodes.length} service and ${engCodes.length} engine codes found`);
assert(untranslated.length === 0, `every error code translated ${untranslated.length ? JSON.stringify(untranslated) : ''}`);
const runErrors = [...run.matchAll(/_fail\("([a-z_]+)"/g)].map((m) => m[1]);
const libProblems = [...lib.matchAll(/'([a-z_]+)'/g)].map((m) => m[1]);
assert(runErrors.every((k) => libProblems.includes(k)), `every Run error kind known to the interface (${runErrors})`);
const problems = [...new Set([...runErrors, 'checkpoint_refused', 'checkpoint_not_durable', 'checkpoint_invalid', 'session_write_failed',
  'start_failed', 'fatal', 'driver_failure', 'engine_error', 'suspended', 'interrupted', 'exited'])];
assert(problems.every((k) => both(`external_measure.problem.${k}.title`) && both(`external_measure.problem.${k}.body`)),
       `every problem has a title and a body (${problems.length})`);
for (const d of ['none', 'full', 'reduced', 'reduced_lasting', 'not_durable', 'refused', 'invalid', 'recovered', 'correction']) {
  assert(both(`external_measure.durability.${d}`), `durability.${d}`);
}
for (const c of ['sensor_position', 'white_reference', 'white_calibration', 'other']) assert(both(`external_measure.calibrate.${c}`), `calibration ${c}`);
const promptsSrc = read(join(repo, 'lib/z9_client/chartread_fgk/prompts.py'));
const waitPrompts = /WAIT_PROMPTS = \{([^}]*)\}/s.exec(run)[1].match(/"([a-z_]+)"/g).map((s) => s.replaceAll('"', ''));
assert(waitPrompts.every((k) => both(`external_measure.warning.${k}`)), `every warning prompt translated (${waitPrompts})`);
assert(/def _calibration_kind/.test(promptsSrc) && ['sensor_position', 'white_reference', 'white_calibration'].every((k) => promptsSrc.includes(`"${k}"`)),
       'calibration kinds mirror prompts.py');
const blockReasons = ['checking', 'absent', 'other', 'several', 'error', 'unreachable'];   // every non-present state
for (const s of ['present', 'present_family', ...blockReasons]) assert(both(`external_measure.presence.${s}`), `presence ${s}`);
for (const s of blockReasons) assert(both(`external_measure.start_blocked.${s}`), `start_blocked ${s}`);
const usbPresence = read(join(repo, 'lib/z9_client/chartread_fgk/usb_presence.py'));
const pyStatuses = /PRESENT, ABSENT, OTHER, SEVERAL, ERROR = ([^\n]*)/.exec(usbPresence)[1].match(/"([a-z]+)"/g).map((x) => x.replaceAll('"', ''));
assert(pyStatuses.filter((x) => x !== 'present').every((x) => presStatuses.includes(x)) && /CHECKING = "checking"/.test(usbPresence),
       `every backend presence status known here (${pyStatuses}, checking)`);
assert(/if not pres\.present:\s*raise ExternalMeasureError\(409, f"instrument_\{pres\.status\}"/.test(service),
       'the backend refuses every start without a confirmed presence, with instrument_<status>');
for (const o of ['approved', 'development', 'test']) assert(both(`external_measure.origin.${o}`), `origin ${o}`);

console.log('Test 4 — no key to chartread, no reading trigger');
const wiz = read(join(src, 'components/ExternalMeasure/ExternalMeasureWizard.jsx'));
const hook = read(join(src, 'hooks/useExternalSession.js'));
const commands = [...(wiz + hook).matchAll(/command\('([a-z_]+)'\)/g)].map((m) => m[1]);
const allowedCmds = ['calibrate', 'abort_calibration', 'prev', 'next', 'next_unread', 'retry', 'give_up', 'accept', 'pause'];
assert(commands.length > 5 && commands.every((c) => allowedCmds.includes(c)), `only backend commands are sent (${[...new Set(commands)]})`);
assert(!/keydown[\s\S]{0,200}(command|post)\(/.test(wiz), 'no keyboard event is forwarded to the engine');
assert(!/onUnmount|pause\(\)[\s\S]{0,40}return \(\) =>/.test(hook) && !/command\('pause'\)/.test(hook), 'closing or unmounting never pauses');

console.log('Test 5 — presence watch and start button');
assert(/const watching = open && watchesPresence\(snap\)/.test(wiz), 'the presence is watched only while the window is open');
assert(/return \(\) => \{ watch\.stop\(\);/.test(wiz), 'closing the window (or a run starting) stops the watch');
assert((wiz.match(/can\('start'\) && !block \?/g) || []).length === 2, 'both start buttons (start, resume) wait while the instrument is not there');
assert(/if \(startingRef\.current\) return null;/.test(hook), 'a second start while the first is on its way is not sent');
assert(/poll = useCallback[\s\S]{0,120}reader\(\)\.shared\(\)[\s\S]{0,40}catch \{\s*return null;/.test(hook),
       'a failed presence check is not shown as a refused action');

console.log('Test 6 — a new measurement after a published one');
const svc = read(join(repo, 'webapp/backend/services/external_measure.py'));
assert(/state = last\[-1\]\.data\["status"\] if last else "none"\s*return state, \["create"\]/.test(svc),
       'backend: a chart whose last session is published allows "create"');
assert(/const step = again \? 'prepare' : stepOf\(snap\);/.test(wiz), 'wizard: a new measurement shows the prepare step');
assert(/if \(open && snap\?\.state === 'published'\) setAgainSeq\(snap\.state_seq\);/.test(wiz),
       'opening the window on a published chart prepares a new session (the panel says "New measurement")');
assert(/snap\?\.state === 'published' && allowed\(snap, 'create'\)[\s\S]{0,80}act\('again'/.test(wiz),
       'right after publishing, the window offers "New measurement"');
assert(both('external_measure.publish.measure_again') && both('external_measure.prepare.again_title') && both('external_measure.prepare.again'),
       'its texts exist in fr and en');

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
