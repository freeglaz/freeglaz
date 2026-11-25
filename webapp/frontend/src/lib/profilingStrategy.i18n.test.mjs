/**
 * Consistency checks of the two-pass adaptive profiling UI:
 *  1. the `strategy` catalog has the same keys in fr.json and en.json;
 *  2. every static `strategy.*` key used by the touched components exists in both;
 *  3. every code the UI translates dynamically mirrors the backend (next steps, FWA refusals,
 *     print condition checks, creation refusals) and has a translation;
 *  4. static guarantees: the client never composes a -c nor a targen line for a strategy pass,
 *     the first pass never shows the installable build panel, the one-pass path keeps its line.
 *
 * Run: node webapp/frontend/src/lib/profilingStrategy.i18n.test.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { CREATE_ERROR_CODES, FINAL_QUALITIES, FWA_REASONS, PRINT_CHECK_CODES, STEPS } from './profilingStrategy.js';

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, '..');
const repo = join(src, '..', '..', '..');
const read = (p) => readFileSync(p, 'utf-8');
const fr = JSON.parse(read(join(src, 'i18n/locales/fr.json')));
const en = JSON.parse(read(join(src, 'i18n/locales/en.json')));

let pass = 0, fail = 0;
function assert(cond, msg) { if (cond) { pass++; console.log(`  ✓ ${msg}`); } else { fail++; console.error(`  ✗ ${msg}`); } }
const flat = (o, prefix = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? flat(v, `${prefix}${k}.`) : [`${prefix}${k}`]));
const has = (cat, key) => key.split('.').reduce((o, k) => (o && typeof o === 'object' ? o[k] : undefined), cat) !== undefined;

console.log('1. catalog parity');
const kf = new Set(flat(fr.strategy)), ke = new Set(flat(en.strategy));
const onlyFr = [...kf].filter((k) => !ke.has(k)), onlyEn = [...ke].filter((k) => !kf.has(k));
assert(onlyFr.length === 0, `no key only in fr ${onlyFr.join(', ')}`);
assert(onlyEn.length === 0, `no key only in en ${onlyEn.join(', ')}`);

console.log('2. static keys used by the components');
const files = ['components/Charts/ArgyllProfileWizard.jsx', 'components/Charts/TwoPassCompose.jsx',
  'components/Charts/StrategyRunPanel.jsx', 'components/Charts/StrategyBadge.jsx', 'components/Mesures/MesuresPage.jsx'];
const used = new Set();
for (const f of files) {
  for (const m of read(join(src, f)).matchAll(/t\('((?:strategy)\.[A-Za-z0-9_.]+)'/g)) used.add(m[1]);
}
// keys chosen by a ternary
for (const f of files) {
  for (const m of read(join(src, f)).matchAll(/'(strategy\.[A-Za-z0-9_.]+)'/g)) used.add(m[1]);
}
const missing = [...used].filter((k) => !has(fr, k) || !has(en, k));
assert(used.size > 60, `${used.size} static strategy keys found in the components`);
assert(missing.length === 0, `every static key exists in fr and en ${missing.join(', ')}`);

console.log('3. dynamic codes mirror the backend and are translated');
const lib = read(join(repo, 'lib/z9_client/profiling_strategies.py'));
const charts = read(join(repo, 'webapp/backend/routes/charts.py'));
const backendSteps = [...lib.match(/STEPS = \(([^)]*)\)/s)[1].matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
assert(backendSteps.every((s) => STEPS.includes(s)), `every backend step is known to the UI (${backendSteps.length})`);
const backendFwa = [...lib.slice(lib.indexOf('def fwa_gate')).matchAll(/"reason": "([a-z0-9_]+)"/g)].map((m) => m[1]);
assert(backendFwa.length >= 7 && backendFwa.every((r) => FWA_REASONS.includes(r)), `every FWA refusal is known (${new Set(backendFwa).size})`);
const checkFn = charts.slice(charts.indexOf('def _strategy_print_check'), charts.indexOf('@router.post("/{chart_id}/print")'));
const backendPrint = new Set([...checkFn.matchAll(/"code": "([a-z0-9_]+)"/g)].map((m) => m[1])
  .concat([...checkFn.matchAll(/f"\{key\}_differs"/g)].length ? ['quality_differs', 'rendermode_differs'] : []));
assert([...backendPrint].every((c) => PRINT_CHECK_CODES.includes(c)), `every print check code is known (${backendPrint.size})`);
const ctxFn = charts.slice(charts.indexOf('def _precondition_version'), charts.indexOf('def _strategy_block'));
const backendCreate = new Set([...ctxFn.matchAll(/_refuse\(\d+, "([a-z0-9_]+)"/g)].map((m) => m[1]));
const uiCreate = new Set(CREATE_ERROR_CODES);
const notWorded = [...backendCreate].filter((c) => !uiCreate.has(c) && !['strategy_not_for_validation', 'strategy_pass_not_supported',
  'strategy_composes_the_chart', 'precondition_is_the_strategy', 'strategy_run_not_expected', 'strategy_line_needs_count'].includes(c));
assert(notWorded.length === 0, `creation refusals reachable from the UI are worded ${notWorded.join(', ')}`);
for (const [group, codes] of [['step', STEPS], ['fwa', FWA_REASONS], ['print.code', PRINT_CHECK_CODES],
  ['error', CREATE_ERROR_CODES], ['quality', FINAL_QUALITIES], ['variant', ['pass2', 'pass1_pass2', 'custom']],
  ['intermediate.state', ['none', 'stale', 'other_settings', 'ready']], ['intermediate.phase', ['preparing', 'building']],
  ['created', ['pass1', 'pass2']], ['notice', ['resident_changed_since_first_pass']]]) {
  const miss = codes.filter((c) => !has(fr.strategy, `${group}.${c}`) || !has(en.strategy, `${group}.${c}`));
  assert(miss.length === 0, `strategy.${group}.* translated ${miss.join(', ')}`);
}
// the shipped definition's lines are the ones the UI documents
assert(lib.includes('ROLE_PRECONDITIONING = "preconditioning"') && lib.includes('ROLE_FINAL = "final"'), 'roles of the backend');
const toml = read(join(repo, 'lib/z9_client/data/profiling_strategies_builtin.toml'));
assert(/patches = 400/.test(toml) && /patches = 2000/.test(toml) && /neutral_emphasis = 0\.75/.test(toml)
  && /smoothing = 2\.0/.test(toml) && /source_gamut = "AdobeRGB"/.test(toml) && /viewcond_in = "mt"/.test(toml)
  && /viewcond_out = "pp"/.test(toml), 'the shipped two-pass definition matches the accepted decisions');

console.log('4. static guarantees');
const wizard = read(join(src, 'components/Charts/ArgyllProfileWizard.jsx'));
const compose = read(join(src, 'components/Charts/TwoPassCompose.jsx'));
const panel = read(join(src, 'components/Charts/StrategyRunPanel.jsx'));
const mesures = read(join(src, 'components/Mesures/MesuresPage.jsx'));
const create2 = wizard.slice(wizard.indexOf('if (twoPassActive) {\n      const form'), wizard.indexOf('    try {\n      const body = {\n        media_key: effMediaKey,   // cols'));
assert(create2.includes('strategyCreateRequest(') && !create2.includes('targen_flags') && !create2.includes('c_profile'),
  'a strategy pass is created without a targen line nor a -c (the server composes them)');
const cUses = [...compose.matchAll(/-c \$\{([^}]*)\}/g)].map((m) => m[1]);
assert(cUses.length === 1 && cUses[0] === "t('strategy.lines.intermediate_file')" && !/'-c'/.test(compose),
  'the composition never builds a -c with a file: only the placeholder');
assert(compose.includes('composeStrategy') === false && wizard.includes('api.composeStrategy('), 'lines shown come from the compose route');
assert(!/install/i.test(panel.replace(/installable|installed|install\b(?=ed)/g, '')), 'the run panel offers no install action');
assert(mesures.includes("id.strategy?.role !== 'preconditioning'"), 'Measurements: the first pass never shows the installable build panel');
assert(wizard.includes('{!scannedIsFirstPass && <ProfileBuildPanel'), 'wizard: same for a scanned first pass');
assert(wizard.includes("onClick={() => doCreate()}"), 'the create button never passes its click event as a form');
assert(wizard.includes("else body.targen_flags = `-d2 ${flags}`.trim();"), 'the one-pass path keeps its free targen line');
assert(wizard.includes('confirm_condition_warnings: confirmWarnings') && wizard.includes("pc?.kind === 'confirm'"),
  'print warnings of a later pass need an explicit confirmation');

console.log('5. general -c menu: profiles built by freeglaz');
const chartsRoute = charts.slice(charts.indexOf('def _freeglaz_precondition_profiles'), charts.indexOf('@router.get("/precondition-profiles")'));
assert(chartsRoute.includes('"kind": "freeglaz"') && wizard.includes("p.kind === 'freeglaz'"), 'the kind the server returns is the one the wizard lists');
assert(wizard.includes("if (cChoice === 'freeglaz') return cFreeglaz || 'none';"), 'choosing one sends its path:… id as -c');
for (const k of ['start_built', 'start_built_desc']) {
  assert(has(fr, `chart_create.${k}`) && has(en, `chart_create.${k}`), `chart_create.${k} translated`);
}

console.log('6. printing an existing chart (never printed, or again)');
const cpFr = new Set(flat(fr.chart_print)), cpEn = new Set(flat(en.chart_print));
assert(cpFr.size > 0 && [...cpFr].every((k) => cpEn.has(k)) && [...cpEn].every((k) => cpFr.has(k)), 'chart_print: same keys fr/en');
for (const k of ['printed_at_one', 'printed_at_other', 'scans_kept_one', 'scans_kept_other']) {
  assert(cpFr.has(k), `chart_print.${k}: plural form present`);
}
const summaryFn = charts.slice(charts.indexOf('def _chart_print_summary'), charts.indexOf('# ─── POST print (hardware act'));
const whys = [...summaryFn.matchAll(/why = "([a-z_]+)"/g)].map((m) => m[1]);
assert(whys.length === 3 && whys.every((w) => has(fr, `chart_print.not_printable.${w}`) && has(en, `chart_print.not_printable.${w}`)),
  `every reason a chart cannot be printed is worded (${whys.join(', ')})`);
for (const k of ['print_chart', 'reprint_chart', 'print_chart_hint']) {
  assert(has(fr, `mesures.${k}`) && has(en, `mesures.${k}`), `mesures.${k} translated`);
}
assert(mesures.includes('printChartId={printWizard.chartId}') && wizard.includes('api.getChartPrintSummary('),
  'Measurements prints through the wizard (same preview, checks and confirmation)');
assert(wizard.includes("action === 'print_first' || action === 'print_last'") && wizard.includes('printExisting(toPrint)'),
  'the resume card prints a pass never printed (no dead end)');
assert(wizard.includes('(!!result?.existing && !result.existing.printable)'), 'a chart that cannot be printed keeps the print button disabled');

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
