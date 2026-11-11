/**
 * Consistency checks of the measurement-QC UI:
 *  1. every i18n key used exists in BOTH fr.json and en.json;
 *  2. the codes mirrored in lib/measureQc.js match the backend (lib/z9_client/measure_qc.py);
 *  3. static read-only guarantees: the QC views never call a mutating API function, and the
 *     wizard and the Measurements tab share the same components.
 *
 * Run: node webapp/frontend/src/lib/measureQc.i18n.test.mjs
 */
import { EXCLUSION_LEVELS, REASON_CHOICES } from './exclusion.js';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import {
  STATUSES, POSITIONS, MODELS, REASON_CODES, FINDING_CODES, WARNING_CODES, ERROR_CODES, FILTERS,
} from './measureQc.js';

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, '..');
const repo = join(src, '..', '..', '..');
const read = (p) => readFileSync(p, 'utf-8');
let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log(`  ✓ ${msg}`); }
  else { fail++; console.error(`  ✗ ${msg}`); }
}

const locales = { fr: JSON.parse(read(join(src, 'i18n/locales/fr.json'))),
                  en: JSON.parse(read(join(src, 'i18n/locales/en.json'))) };
const get = (obj, key) => key.split('.').reduce((o, k) => (o && typeof o === 'object' ? o[k] : undefined), obj);
const has = (loc, key) => typeof get(loc, key) === 'string'
  || (typeof get(loc, `${key}_one`) === 'string' && typeof get(loc, `${key}_other`) === 'string');

console.log('Test 1 — i18n keys present in fr AND en');
{
  const files = ['components/Charts/MeasurementsModal.jsx', 'components/Charts/MeasureQcSummary.jsx',
                 'components/Charts/ExclusionDialog.jsx', 'components/Charts/QCTableModal.jsx', 'lib/exclusion.js',
                 'components/Charts/ScanKeepList.jsx', 'components/Charts/ArgyllProfileWizard.jsx', 'lib/measureQc.js'];
  const literal = new Set();
  for (const f of files) {
    for (const m of read(join(src, f)).matchAll(/['"`]((?:measure_qc|scan\.measures|exclusion)\.[A-Za-z0-9_.]+)['"`]/g)) literal.add(m[1]);
  }
  const dyn = [
    ...STATUSES.map((s) => `measure_qc.status.${s}`), ...STATUSES.map((s) => `measure_qc.status_title.${s}`),
    ...POSITIONS.map((p) => `measure_qc.position.${p}`), ...MODELS.map((m) => `measure_qc.model.${m}`),
    ...REASON_CODES.map((c) => `measure_qc.reason.${c}`), ...FINDING_CODES.map((c) => `measure_qc.finding.${c}`),
    ...WARNING_CODES.map((c) => `measure_qc.warning.${c}`), ...ERROR_CODES.map((c) => `measure_qc.error.${c}`),
    ...FILTERS.map((f) => `measure_qc.filter.${f}`), ...FILTERS.map((f) => `measure_qc.filter.${f}_title`),
    ...EXCLUSION_LEVELS.flatMap((l) => [`exclusion.risk.${l}`, `exclusion.risk_title.${l}`, `exclusion.base_level.${l}`,
                                        `exclusion.level_explain.${l}`]),
    ...REASON_CHOICES.map((r) => `exclusion.reason.${r}`), 'exclusion.reason.none',
    ...['refused', 'scan_excluded', 'not_found', 'unreadable', 'network', 'error'].map((k) => `exclusion.error.${k}`),
    ...[...POSITIONS, 'duplicate', 'refused_white', 'refused_black'].map((k) => `exclusion.impact.${k}`),
    ...['last', 'average'].map((b) => `exclusion.base.${b}`),
  ];
  const keys = [...new Set([...literal, ...dyn])];
  for (const [name, loc] of Object.entries(locales)) {
    const missing = keys.filter((k) => !has(loc, k));
    assert(missing.length === 0, `${name}: ${keys.length} keys present${missing.length ? ' — MISSING ' + missing.join(', ') : ''}`);
  }
  const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (typeof v === 'object' ? flat(v, `${p}${k}.`) : [`${p}${k}`]));
  const fr = new Set(flat(locales.fr.measure_qc)); const en = new Set(flat(locales.en.measure_qc));
  const diff = [...fr].filter((k) => !en.has(k)).concat([...en].filter((k) => !fr.has(k)));
  assert(diff.length === 0, `fr and en measure_qc blocks have the same keys${diff.length ? ' — DIFF ' + diff.join(', ') : ''}`);
  const banned = /valid[ée]e?s|validated|garanti|guaranteed/i;
  const okPhrases = [locales.fr.measure_qc.summary.clean, locales.en.measure_qc.summary.clean];
  assert(okPhrases.every((p) => !banned.test(p)), 'the "nothing detected" wording never says validated / guaranteed');
  const limited = [locales.fr.measure_qc.summary.limited_check, locales.en.measure_qc.summary.limited_check];
  assert(limited.every((p) => !/aucune incoh|no inconsisten|incohérence d'ensemble|overall inconsisten/i.test(p)),
         'sparse-chart headline is "limited check": neither "nothing detected" nor an alarm');
  const errs = ['not_found', 'unreadable', 'unavailable'].flatMap((k) => [locales.fr.scan.measures.error[k], locales.en.scan.measures.error[k]]);
  assert(errs.every((m) => typeof m === 'string' && !/unreadable ti3|END_DATA|Traceback/.test(m)),
         'measurements read errors are full sentences in each language (no raw backend text)');
}

console.log('Test 2 — frontend codes match the backend engine');
{
  const py = read(join(repo, 'lib/z9_client/measure_qc.py'));
  const tuple = (name) => {
    const m = py.match(new RegExp(`^${name} = \\(([\\s\\S]*?)\\)`, 'm'));
    return m ? [...m[1].matchAll(/"([A-Za-z0-9_]+)"/g)].map((x) => x[1]) : null;
  };
  const same = (a, b) => a && b && a.length === b.length && a.every((x) => b.includes(x));
  assert(same(tuple('REASON_CODES'), REASON_CODES), 'REASON_CODES identical');
  assert(same(tuple('FINDING_CODES'), FINDING_CODES), 'FINDING_CODES identical');
  assert(same(tuple('WARNING_CODES'), WARNING_CODES), 'WARNING_CODES identical');
  assert(same(tuple('STATUSES'), STATUSES), 'STATUSES identical');
  assert(same(tuple('POSITIONS'), POSITIONS), 'POSITIONS identical');
  const engineErrors = tuple('ERROR_CODES');
  const svc = read(join(repo, 'webapp/backend/services/measure_qc.py'));
  const svcCodes = [...svc.matchAll(/ScanQCError\("([A-Z0-9_]+)"/g)].map((m) => m[1]);
  assert(same([...new Set([...engineErrors, ...svcCodes])], ERROR_CODES), 'ERROR_CODES = engine + service codes');
  const routes = read(join(repo, 'webapp/backend/routes/charts.py'));
  const patchesRoute = routes.match(/def scan_patches[\s\S]*?\n\n\n/)[0];
  assert(/HTTPException\(422, detail=\{"code": "UNREADABLE"/.test(patchesRoute) && /logger\.warning\(/.test(patchesRoute)
         && /'UNREADABLE'/.test(read(join(src, 'lib/measureQc.js'))),
         '/patches: coded 422 "UNREADABLE" understood by the frontend, raw cause logged server-side');
}

console.log('Test 3 — read-only views and shared components');
{
  const modal = read(join(src, 'components/Charts/MeasurementsModal.jsx'));
  const summary = read(join(src, 'components/Charts/MeasureQcSummary.jsx'));
  const lib = read(join(src, 'lib/measureQc.js'));
  const apiCalls = [...modal.matchAll(/api\.([A-Za-z]+)\(/g)].map((m) => m[1]);
  assert(apiCalls.length > 0 && apiCalls.every((f) => ['getScanPatches', 'getScanMeasureQC', 'setReadingExclusion'].includes(f)),
         `MeasurementsModal calls only reads + the explicit exclusion API (${[...new Set(apiCalls)].join(', ')})`);
  assert(!/api\./.test(summary) && !/from '\.\.\/api|client\.js/.test(lib), 'summary component and lib never call the API');
  const mutating = /setReadingRejected|setScanRole|deleteScan|profileChart|buildProfileAndWait|scanChart|lightenChart/;
  assert(!mutating.test(modal) && !mutating.test(summary) && !mutating.test(lib), 'no other mutating API function referenced by the QC views');
  // Phase 4D: one exclusion logic. The dialog always simulates first; no component uses the legacy route.
  const dialogSrc = read(join(src, 'components/Charts/ExclusionDialog.jsx'));
  const qctable = read(join(src, 'components/Charts/QCTableModal.jsx'));
  assert(/setReadingExclusion\(chartId, ti3, sid, \{ dry_run: true/.test(dialogSrc), 'ExclusionDialog runs a dry run before any write');
  assert(/import ExclusionDialog from '\.\/ExclusionDialog\.jsx'/.test(modal) && /import ExclusionDialog from '\.\/ExclusionDialog\.jsx'/.test(qctable)
         && !/api\.setReadingRejected\(/.test(qctable) && /api\.setReadingExclusion\(/.test(qctable),
         'MeasurementsModal and QCTableModal share ExclusionDialog and the /exclusion API (legacy /reject unused)');
  assert(!/auto.?exclu|exclude\(.*report|status === 'strong'.*setReadingExclusion/i.test(modal + qctable),
         'no automatic exclusion from a QC verdict');
  const client = read(join(src, 'api/client.js'));
  const fn = client.match(/export async function getScanMeasureQC[\s\S]*?\n}/)[0];
  assert(/method: 'GET'/.test(fn) && !/POST|PUT|DELETE|PATCH/.test(fn), 'getScanMeasureQC is a GET');
  const wizard = read(join(src, 'components/Charts/ArgyllProfileWizard.jsx'));
  const mesures = read(join(src, 'components/Mesures/MesuresPage.jsx'));
  const keep = read(join(src, 'components/Charts/ScanKeepList.jsx'));
  assert(/import MeasurementsModal from '\.\/MeasurementsModal\.jsx'/.test(wizard)
         && /import MeasurementsModal from '\.\.\/Charts\/MeasurementsModal\.jsx'/.test(mesures),
         'wizard and Measurements tab open the SAME MeasurementsModal');
  assert(/import MeasureQcSummary from '\.\/MeasureQcSummary\.jsx'/.test(wizard)
         && /import MeasureQcSummary from '\.\/MeasureQcSummary\.jsx'/.test(modal), 'same QC summary component in both');
  assert(/_runScanQc\(chartId, job\.ti3\)/.test(wizard) && /setPhase\('scanned'\);\s*\n\s*_runScanQc/.test(wizard),
         'wizard: QC triggered AFTER the scan is recorded as done (never part of the acquisition)');
  assert(/summaryBadge\(s\.qc_summary\)/.test(keep), 'scan list badge comes from the cached qc_summary only');
  assert(/k === 'limited' && state\.limitedByDensity\) headline = t\('measure_qc\.summary\.limited_check'\)/.test(summary),
         'sparse chart: "limited check" headline, gated by the density flag only');
  assert(/\{data && \(\s*\n\s*<div className="overflow-auto">\s*\n\s*<table/.test(modal),
         'measurements table rendered only when measurements were read (no misleading empty table)');
  assert(/t\(err\.key\)/.test(modal) && !/\{err\}/.test(modal) && /classifyPatchesError\(e\)/.test(modal),
         'read error shown as a localized message, never the raw backend text');
  assert(/data \? t\('scan\.measures\.title', \{ count: data\.n_patches \}\) : t\('scan\.measures\.title_plain'\)/.test(modal),
         'title never reads "0 patch" when nothing could be read');
  // A failed check (422 / 404 / error) only mentions the measurements when the parent really shows some,
  // and never claims the scan file is fine (a truncated ti3 is exactly a defective file).
  assert(/\{measurementsShown && \(k === 'invalid'/.test(summary) && /measurementsShown=\{rows\.length > 0\}/.test(modal)
         && !/measurementsShown/.test(wizard), 'failed-check hint shown only when measurements are displayed (never in the wizard)');
  const hints = [locales.fr.measure_qc.state, locales.en.measure_qc.state].map((s) => Object.values(s).join(' '));
  assert(hints.every((h) => !/pas en cause|not at fault/i.test(h)), 'failed-check wording never vouches for the scan');
  assert(/onClose=\{\(\) => \{ setViewTi3\(null\); loadDetail\(selected\); \}\}/.test(mesures),
         'Measurements refreshes the chart detail after viewing (badge appears)');
}

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
