/**
 * Consistency checks of the measurement-method UI (external instrument charts, measurement
 * identity):
 *  1. the `measurement` catalog has the same keys in fr.json and en.json;
 *  2. every static `measurement.*` / `errors.*` key used by the touched components exists in both;
 *  3. every code the UI translates dynamically mirrors the backend (identity refusals,
 *     differences, remedies, conditions, record errors, validation warnings, strip sides,
 *     instruments of the printtarg whitelist) and has a translation;
 *  4. static guarantees: an external chart never reaches the SOL scan in the wizard nor in the
 *     Measurements tab.
 *
 * Run: node webapp/frontend/src/lib/measurementMethod.i18n.test.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { IDENTITY_CODES, MEDIA_BLOCKING } from './measurementMethod.js';

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, '..');
const repo = join(src, '..', '..', '..');
const read = (p) => readFileSync(p, 'utf-8');
const fr = JSON.parse(read(join(src, 'i18n/locales/fr.json')));
const en = JSON.parse(read(join(src, 'i18n/locales/en.json')));

let pass = 0, fail = 0;
function assert(cond, msg) { if (cond) { pass++; console.log(`  ✓ ${msg}`); } else { fail++; console.error(`  ✗ ${msg}`); } }

function flat(o, prefix = '') {
  return Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? flat(v, `${prefix}${k}.`) : [`${prefix}${k}`]));
}
const has = (cat, key) => {
  if (key.split('.').reduce((o, k) => (o == null ? undefined : o[k]), cat) !== undefined) return true;
  const parts = key.split('.'); const last = parts.pop();
  const parent = parts.reduce((o, k) => (o == null ? undefined : o[k]), cat);
  return !!parent && parent[`${last}_one`] !== undefined && parent[`${last}_other`] !== undefined;   // plural
};
const both = (key) => has(fr, key) && has(en, key);

console.log('Test 1 — fr / en parity of the measurement catalog');
const kf = flat(fr.measurement).sort(), ke = flat(en.measurement).sort();
assert(JSON.stringify(kf) === JSON.stringify(ke), `same ${kf.length} keys in fr and en`);
assert(flat(fr.measurement).every((k) => String(k.split('.').reduce((o, x) => o[x], fr.measurement)).trim()), 'no empty fr string');

console.log('Test 2 — static keys used by the components');
const files = ['components/Charts/ArgyllProfileWizard.jsx', 'components/Charts/MeasurementBadge.jsx',
  'components/Charts/IdentityRefusal.jsx', 'components/ExternalMeasure/ExternalMeasurePanel.jsx',
  'components/Charts/ScanKeepList.jsx', 'components/Charts/BuildPreviewSummary.jsx',
  'components/Charts/ProfileBuildPanel.jsx', 'components/Mesures/MesuresPage.jsx', 'lib/measurementMethod.js'];
const used = new Set();
for (const f of files) {
  for (const m of read(join(src, f)).matchAll(/t\(\s*'((?:measurement|errors)\.[a-z0-9_.]+)'/gi)) used.add(m[1]);
}
const missing = [...used].filter((k) => !both(k));
assert(used.size > 40, `${used.size} static keys found`);
assert(missing.length === 0, `all present in fr and en ${missing.length ? JSON.stringify(missing) : ''}`);

console.log('Test 3 — dynamic codes mirror the backend and are translated');
const be = (p) => read(join(repo, p));
const ident = be('lib/z9_client/measurement_identity.py');
const binputs = be('lib/z9_client/build_inputs.py');
const routes = be('webapp/backend/routes/charts.py');
const ptc = be('lib/z9_client/printtarg_chart.py');
for (const c of IDENTITY_CODES) {
  assert((binputs + routes).includes(`"${c}"`) || binputs.includes(c), `backend emits ${c}`);
  assert(both(`errors.${c}`), `errors.${c} translated`);
}
const diffCodes = ['method_differs', 'instrument_differs', 'condition_differs', 'spectral_differs',
  'calibration_standard_differs', 'method_unknown', 'instrument_unknown', 'condition_unknown', 'calibration_standard_unknown'];
assert(ident.includes('f"{code}_differs"') && ident.includes('f"{code}_unknown"') && ident.includes('"condition_differs"')
       && ident.includes('"spectral_differs"') && ident.includes('"calibration_standard_differs"')
       && ident.includes('"condition_unknown"') && ident.includes('"calibration_standard_unknown"'), 'difference codes produced by compare_identities');
assert(diffCodes.every((c) => both(`measurement.difference.${c}`)), 'every difference translated');
const remedies = new Set([...binputs.matchAll(/"((?:exclude_scan|use_base_last|remove_extra_chart|remove_or_fix_the_record))"/g)].map((m) => m[1]));
assert(remedies.size === 4, `4 remedies in build_inputs (${[...remedies]})`);
assert([...remedies, 'exclude_scan'].every((c) => both(`measurement.remedy.${c}`)), 'every remedy translated');
const mconds = /M_CONDITIONS = \(([^)]*)\)/.exec(ident)[1].match(/"([^"]+)"/g).map((s) => s.replaceAll('"', ''));
const conds = [...mconds, 'D65', 'fixed', 'unknown'];
assert(ident.includes('CONDITION_FIXED = "fixed"') && ident.includes('CONDITION_UNKNOWN = "unknown"'), 'fixed / unknown conditions in the backend');
assert(conds.every((c) => both(`measurement.condition.${c}`)), `conditions translated (${conds})`);
for (const c of ['measurement_record_malformed', 'measurement_record_unsupported', 'measurement_record_mismatch']) {
  assert(ident.includes(`"${c}"`) && both(`measurement.record_error.${c}`), `record error ${c}`);
}
for (const w of ['validation_identity_differs', 'validation_identity_unverified', 'build_source_profiles_not_tracked']) {
  assert(routes.includes(`"${w}"`) && both(`measurement.validation.${w}`), `validation warning ${w}`);
}
for (const side of ['right', 'left', 'top']) assert(ptc.includes(`"${side}"`) && both(`measurement.side.${side}`), `strip side ${side}`);
for (const st of ['incompatible', 'insufficient', 'record_invalid', 'unreadable']) assert(both(`measurement.included.${st}`), `included.${st}`);
assert(routes.includes('"patches_not_located"') && both('measurement.generator_warning.patches_not_located'), 'generator warning code');
for (const st of ['incompatible', 'insufficient', 'record_invalid']) assert(both(`measurement.source_compat.${st}`), `source_compat.${st}`);
const whitelist = [...ptc.matchAll(/^\s+"([a-z0-9]+)": InstrumentSpec\(/gm)].map((m) => m[1]);
assert(whitelist.length >= 2 && whitelist.every((k) => both(`measurement.instrument.${k}`)), `display name for every whitelisted instrument (${whitelist})`);
// every quoted value written after `note:` (ternaries included) in methodAvailability
const notes = [...new Set(read(join(src, 'lib/measurementMethod.js')).split('\n')
  .filter((l) => l.includes('note:')).flatMap((l) => [...l.slice(l.indexOf('note:')).matchAll(/'([a-z0-9_]+)'/g)].map((m) => m[1])))];
assert(notes.length === 7 && notes.every((n) => both(`measurement.note.${n}`)), `availability notes translated (${notes})`);
for (const st of ['unknown', ...MEDIA_BLOCKING]) {
  assert(both(`measurement.media.${st}`), `media.${st}`);
}
// the media messages name formats given by the backend, never an instrument
const instNames = [...new Set([...ptc.matchAll(/InstrumentSpec\("[a-z0-9]+", "([^"]+)"/g)].map((m) => m[1].replace(/^X-Rite /, '')))];
const mediaText = JSON.stringify([fr.measurement.media, en.measurement.media]);
assert(instNames.length >= 2 && instNames.every((n) => !mediaText.includes(n)), `no instrument name in measurement.media (${instNames})`);

console.log('Test 4 — an external chart never reaches the SOL scan');
const wiz = read(join(src, 'components/Charts/ArgyllProfileWizard.jsx'));
assert(/phase === 'printed' && !selectedExternal && \(\s*<PrintedView/.test(wiz), 'wizard: the "go to scan" screen only for honeycomb charts');
assert(/phase === 'printed' && selectedExternal && \(\s*<PrintedExternalView/.test(wiz), 'wizard: external charts get the external printed screen');
assert(/phase === 'scanready' && selected && !selectedExternal && \(/.test(wiz) && (wiz.match(/onClick=\{doScan\}/g) || []).length === 1,
       'wizard: the single scan button lives in the honeycomb footer');
assert(/if \(chartMeasurement\(selected\)\.external\) return;/.test(wiz), 'wizard: no SOL status poll for an external chart');
assert(/!chartMeasurement\(target\)\.external\) api\.abandonScan/.test(wiz), 'wizard: no SOL session release for an external chart');
assert(!/PrintedExternalView[\s\S]{0,4000}?scanChart/.test(wiz.slice(wiz.indexOf('function PrintedExternalView'), wiz.indexOf('function PrintedView'))),
       'PrintedExternalView never calls the scan API');
const mes = read(join(src, 'components/Mesures/MesuresPage.jsx'));
assert(/if \(!detail \|\| chartMeasurement\(detail\.identity\)\.external\) return;/.test(mes), 'Measurements: openScan refuses an external chart');
assert(/idMeasurement\.external \? \(\s*\/\/[^\n]*\n[^\n]*\n\s*<ExternalMeasurePanel/.test(mes), 'Measurements: the external measurement panel instead of the scan button');

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
