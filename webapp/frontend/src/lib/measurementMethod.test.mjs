/**
 * Tests of lib/measurementMethod.js (measurement method of a chart: built-in HP scan or external
 * instrument) and lib/libraryLink.js. Run: node webapp/frontend/src/lib/measurementMethod.test.mjs
 */
import {
  METHOD_EXTERNAL, METHOD_INTEGRATED, chartBadge, chartMeasurement, defaultMethod, describeValue, estimateRequest,
  externalFormats, externalMediaCheck, externalPrintBlock, identityRefusal, layoutGeometry, layoutOptionsFor,
  methodAvailability, previewCompatibility, printtargSummary, resolveInstrument, resolveLayoutChoice, resolveMethod,
  scanIdentityLine, untrackedSourceProfiles, validationIdentityNotice,
} from './measurementMethod.js';
import { classifyBuildError } from './buildPreview.js';
import { chartIdFromHash, libraryHref } from './libraryLink.js';

let pass = 0, fail = 0;
function assert(cond, msg) { if (cond) { pass++; console.log(`  ✓ ${msg}`); } else { fail++; console.error(`  ✗ ${msg}`); } }

// Shapes returned by the backend (GET /api/charts/printtarg-options, GET /api/charts/formats).
const OPTIONS = {
  layout: 'printtarg', media: ['roll24'],
  instruments: [
    { key: 'i1pro3plus', name: 'X-Rite i1Pro 3 Plus', printtarg_selector: '3p',
      presets: [{ media_key: 'roll24', page_w_mm: 610, page_h_mm: 500, unlimited_strip: true }] },
    { key: 'colormunki', name: 'X-Rite ColorMunki', printtarg_selector: 'CM',
      presets: [{ media_key: 'roll24', page_w_mm: 610, page_h_mm: 450, unlimited_strip: false }] },
  ],
  binaries: { printtarg: { available: true }, chartread: { available: false } },
};
const FORMATS = [{ key: 'a3', width_mm: 297, is_roll: false }, { key: 'roll24', width_mm: 610, is_roll: true },
                 { key: 'roll44', width_mm: 1118, is_roll: true }];

console.log('Test 1 — methods offered per printer family');
const z9 = methodAvailability('z9', OPTIONS);
assert(z9.integrated.available && z9.external.available && !z9.integrated.note, 'Z9: both methods');
const pro = methodAvailability('z9pro', OPTIONS);
assert(!pro.integrated.available && pro.integrated.note === 'z9pro' && pro.external.available, 'Z9 Pro: external only');
const other = methodAvailability('designjet', OPTIONS);
assert(!other.integrated.available && other.integrated.note === 'family_unsupported', 'other DesignJet: built-in not offered');
const unknown = methodAvailability(null, OPTIONS);
assert(unknown.integrated.available && unknown.integrated.note === 'family_unknown', 'unknown family: built-in flagged unverified');
assert(methodAvailability('z9', null).external.note === 'options_loading', 'options loading: external not yet offered');
assert(methodAvailability('z9', false).external.note === 'options_unavailable', 'options unreadable');
assert(methodAvailability('z9', { ...OPTIONS, instruments: [] }).external.note === 'no_instrument', 'no instrument listed');
const noPt = methodAvailability('z9pro', { ...OPTIONS, binaries: { printtarg: { available: false } } });
assert(noPt.external.available && noPt.external.note === 'printtarg_missing', 'printtarg missing: offered, creation blocked');

console.log('Test 2 — defaults and the user\'s choice');
assert(defaultMethod('z9') === METHOD_INTEGRATED && defaultMethod(null) === METHOD_INTEGRATED, 'Z9 / unknown → built-in (unchanged)');
assert(defaultMethod('z9pro') === METHOD_EXTERNAL && defaultMethod('designjet') === METHOD_EXTERNAL, 'Z9 Pro / other → external');
assert(resolveMethod(null, z9, 'z9') === METHOD_INTEGRATED, 'Z9 without choice: built-in');
assert(resolveMethod(METHOD_EXTERNAL, z9, 'z9') === METHOD_EXTERNAL, 'Z9: external choice kept');
assert(resolveMethod(null, pro, 'z9pro') === METHOD_EXTERNAL, 'Z9 Pro without choice: external');
assert(resolveMethod(METHOD_INTEGRATED, pro, 'z9pro') === METHOD_EXTERNAL, 'Z9 Pro: built-in choice impossible → external');
assert(resolveMethod(null, methodAvailability('z9pro', null), 'z9pro') === null, 'Z9 Pro while options load: nothing offered yet');
assert(resolveInstrument('colormunki', OPTIONS) === 'colormunki', 'instrument choice kept');
assert(resolveInstrument(null, OPTIONS) === 'i1pro3plus', 'default instrument = first of the backend list');
assert(resolveInstrument('spyder', OPTIONS) === 'i1pro3plus', 'unknown instrument → backend list');
assert(resolveInstrument(null, null) === null, 'no options → no instrument');

console.log('Test 3 — loaded media for an external chart (24" roll, roll 1)');
const media = (p) => externalMediaCheck(p, OPTIONS, FORMATS).status;
assert(media({ kind: 'roll', width_mm: 610, media_source: 'ROLL' }) === 'ok', '24" roll on roll 1');
assert(media({ kind: 'roll', width_mm: 609.6, media_source: 'ROLL' }) === 'ok', '24" roll (inch rounding)');
assert(media({ kind: 'roll', width_mm: 1118, media_source: 'ROLL' }) === 'roll_width', '44" roll refused');
assert(media({ kind: 'roll', width_mm: 432, media_source: 'ROLL' }) === 'roll_width', '17" roll refused');
assert(media({ kind: 'sheet', width_mm: 297, height_mm: 420, media_source: 'MANUAL_FEED' }) === 'sheet_loaded', 'sheet refused');
assert(media({ kind: 'sheet', width_mm: 297, media_source: 'MANUAL_FEED' }) === 'sheet_unidentified', 'sheet without its length: not identified');
assert(media({ kind: 'sheet', width_mm: null, media_source: 'ROLL2' }) === 'roll_source_unsupported', 'ROLL2 refused');
assert(media(null) === 'unknown' && media({ kind: 'roll', media_source: 'ROLL' }) === 'unknown', 'unknown media: checked at print');
assert(externalMediaCheck({ kind: 'roll', width_mm: 914, media_source: 'ROLL' }, OPTIONS, FORMATS).expected[0] === 610, 'expected width from the formats');

console.log('Test 3b — an A4 sheet for the ColorMunki (manual feed)');
const OPTIONS_A4 = { ...OPTIONS, media: ['roll24', 'a4'], instruments: [
  OPTIONS.instruments[0],
  { ...OPTIONS.instruments[1], presets: [...OPTIONS.instruments[1].presets,
    { media_key: 'a4', page_w_mm: 257, page_h_mm: 190, unlimited_strip: false }] }] };
const FORMATS_A4 = [{ key: 'a4', width_mm: 210, height_mm: 297, is_roll: false },
                    { key: 'a3', width_mm: 297, height_mm: 420, is_roll: false }, ...FORMATS.slice(1)];
const A4_LOADED = { kind: 'sheet', width_mm: 210.52, height_mm: 292.74, media_source: 'MANUAL_FEED' };
const cm = externalMediaCheck(A4_LOADED, OPTIONS_A4, FORMATS_A4, 'colormunki');
assert(cm.status === 'ok' && cm.key === 'a4', 'A4 as the Z9 measures it, ColorMunki: accepted, format a4');
assert(externalMediaCheck({ ...A4_LOADED, height_mm: 297 }, OPTIONS_A4, FORMATS_A4, 'colormunki').key === 'a4', 'nominal A4 accepted');
assert(externalMediaCheck(A4_LOADED, OPTIONS_A4, FORMATS_A4, 'i1pro3plus').status === 'sheet_loaded', 'A4 refused for the i1Pro 3 Plus (no preset)');
assert(externalMediaCheck({ ...A4_LOADED, height_mm: 270 }, OPTIONS_A4, FORMATS_A4, 'colormunki').status === 'sheet_loaded', 'a sheet much shorter than A4 refused');
assert(externalMediaCheck({ kind: 'sheet', width_mm: 297, height_mm: 420, media_source: 'MANUAL_FEED' }, OPTIONS_A4, FORMATS_A4, 'colormunki').status === 'sheet_loaded', 'A3 sheet refused (no A3 preset)');
const r24 = externalMediaCheck({ kind: 'roll', width_mm: 610, media_source: 'ROLL' }, OPTIONS_A4, FORMATS_A4, 'colormunki');
assert(r24.status === 'ok' && r24.key === 'roll24' && r24.expected.join() === '610', '24" roll still accepted for the ColorMunki, format roll24');
assert(externalMediaCheck({ kind: 'roll', width_mm: 1118, media_source: 'ROLL' }, OPTIONS_A4, FORMATS_A4, 'colormunki').status === 'roll_width', '44" roll still refused');
assert(externalFormats(OPTIONS_A4, FORMATS_A4, 'colormunki').map((f) => f.key).join() === 'roll24,a4', 'ColorMunki formats: roll24 and a4, in the backend order');
assert(externalFormats(OPTIONS_A4, FORMATS_A4, 'i1pro3plus').map((f) => f.key).join() === 'roll24', 'i1Pro 3 Plus formats: roll24 only');
assert(externalFormats(OPTIONS_A4, FORMATS_A4).map((f) => f.key).join() === 'roll24,a4', 'no instrument: every format the backend lists, its order');
// nothing known about the loaded media: the default stays the backend's first medium (the roll),
// never a sheet because of the order of GET /formats (a4 comes first there)
const unk = externalMediaCheck(null, OPTIONS_A4, FORMATS_A4, 'colormunki');
assert(unk.status === 'unknown' && !unk.key && externalFormats(OPTIONS_A4, FORMATS_A4, 'colormunki')[0].key === 'roll24', 'unknown media → roll24 stays the default format');
assert(externalMediaCheck({ kind: 'roll', media_source: 'ROLL' }, OPTIONS_A4, FORMATS_A4, 'colormunki').status === 'unknown', 'roll of unknown width → unknown, no sheet format chosen');
assert(cm.accepted.join() === 'roll24,a4' && externalMediaCheck(A4_LOADED, OPTIONS_A4, FORMATS_A4, 'i1pro3plus').accepted.join() === 'roll24', 'accepted formats named for the messages');

console.log('Test 3c — printing a created external chart: the loaded media must be its format');
const A4_RESULT = { layout: 'printtarg', media_key: 'a4', media_source: 'sheet', instrument: 'colormunki' };
const R24_RESULT = { layout: 'printtarg', media_key: 'roll24', media_source: 'roll', instrument: 'colormunki' };
const ROLL_LOADED = { kind: 'roll', width_mm: 610, media_source: 'ROLL' };
const chk = (loaded, res) => externalPrintBlock(res, externalMediaCheck(loaded, OPTIONS_A4, FORMATS_A4, res.instrument), FORMATS_A4);
assert(chk(A4_LOADED, A4_RESULT) === null, 'A4 chart, A4 loaded: printable');
assert(chk(ROLL_LOADED, R24_RESULT) === null, 'roll chart, 24" roll loaded: printable');
const mm = chk(ROLL_LOADED, A4_RESULT);
assert(mm?.status === 'media_mismatch' && mm.chart === 'a4' && mm.loaded === 'roll24', 'A4 chart, roll loaded since: blocked (media_mismatch)');
assert(chk(A4_LOADED, R24_RESULT)?.status === 'media_mismatch', 'roll chart, A4 loaded: blocked (media_mismatch)');
assert(chk({ kind: 'sheet', width_mm: 297, height_mm: 420, media_source: 'MANUAL_FEED' }, A4_RESULT)?.status === 'sheet_loaded', 'A4 chart, A3 loaded: blocked');
// a sheet chart needs the sheet identified: nothing known, no length, an unknown source → blocked
assert(chk(null, A4_RESULT)?.status === 'sheet_unidentified', 'A4 chart, nothing known about the paper: blocked');
assert(chk({ ...A4_LOADED, height_mm: null }, A4_RESULT)?.status === 'sheet_unidentified', 'A4 chart, sheet length not reported: blocked');
assert(chk({ kind: 'sheet', width_mm: 210.52, height_mm: 292.74, media_source: '?' }, A4_RESULT)?.status === 'sheet_unidentified', 'A4 chart, unknown source: blocked');
assert(chk({ ...A4_LOADED, media_source: '' }, A4_RESULT)?.status === 'sheet_unidentified', 'A4 chart, no source: blocked');
assert(chk({ ...A4_LOADED, media_source: 'ROLL2' }, A4_RESULT)?.status === 'roll_source_unsupported', 'A4 chart, ROLL2: blocked (as before)');
assert(chk({ ...A4_LOADED, media_source: 'SHEET' }, A4_RESULT) === null, 'A4 chart, A4 on the SHEET source: printable');
// a roll chart: unchanged, an unknown paper stays the print route's to check
assert(chk(null, R24_RESULT) === null && chk({ kind: 'roll', media_source: 'ROLL' }, R24_RESULT) === null, 'roll chart, unknown paper: not blocked here (as before)');
assert(externalPrintBlock({ layout: 'printtarg', media_key: 'a4', instrument: 'colormunki' }, externalMediaCheck(null, OPTIONS_A4, FORMATS_A4, 'colormunki'), FORMATS_A4)?.status === 'sheet_unidentified', 'sheet chart recognised from the formats when media_source is absent');
assert(externalPrintBlock({ layout: 'honeycomb' }, mm, FORMATS_A4) === null && externalPrintBlock(null, mm) === null, 'honeycomb / no result: never blocked here');

console.log('Test 4 — how a chart is measured (library items and detail identities)');
assert(chartMeasurement({ chart_id: 'CHT-old' }).integrated && chartMeasurement({}).layout === 'honeycomb', 'historical chart without layout = honeycomb, built-in');
assert(chartMeasurement(null).integrated, 'no chart → honeycomb (selection not made yet)');
const item = { layout: 'printtarg', measurement_method: 'external', instrument: 'colormunki' };
assert(chartMeasurement(item).external && chartMeasurement(item).instrument === 'colormunki', 'library printtarg item');
const ident = { layout: 'printtarg', instrument: null, measurement: { method: 'external', instruments: ['i1pro3plus'] } };
assert(chartMeasurement(ident).instrument === 'i1pro3plus', 'detail identity: instrument from the measurement contract');
assert(chartMeasurement({ layout: 'printtarg', instrument: 'colormunki' }).external, 'create response (no measurement_method)');
assert(chartBadge({}).kind === 'integrated' && chartBadge(item).kind === 'external' && chartBadge({ layout: 'hexagon' }).kind === 'unknown', 'badges');

console.log('Test 5 — preview of a created printtarg chart (generator numbers only)');
const created = { chart_id: 'CHT-20261122-1600-AA', layout: 'printtarg', instrument: 'i1pro3plus', n_patches: 800,
  roll_layout: 'transverse', n_pages: 1, steps_in_pass: 20, total_strips: 40, tiff_w_mm: 500.0, tiff_h_mm: 657.0,
  roll_length_mm: 667.0, composition: { rotation: 'cw', strips_cross_the_roll: true, strip_order: 'top_to_bottom', patch_1_side: 'right' },
  tag: { icc_name: 'Resident GE OFF', gloss_enhancer: 'OFF' }, warnings: [],
  generator_warnings: [{ code: 'patches_not_located', count: 8 }] };
const ps = printtargSummary(created);
assert(ps.nStrips === 40 && ps.patchesPerStrip === 20 && ps.widthMm === 500 && ps.lengthMm === 657 && ps.rollLengthMm === 667, 'strips, size, roll length');
assert(ps.stripsCrossTheRoll && ps.stripOrder === 'top_to_bottom' && ps.patch1Side === 'right', 'orientation: across the roll, strip A on top, patch 1 right');
assert(ps.iccName === 'Resident GE OFF' && ps.ge === 'OFF', 'resident and GE');
assert(ps.generatorWarnings[0].code === 'patches_not_located' && ps.generatorWarnings[0].count === 8, 'generator findings as codes');
assert(printtargSummary({ chart_id: 'x', cols: 18, nrows: 4 }) === null, 'honeycomb result → no printtarg summary (no fake strips)');
assert(ps.onSheet === false && printtargSummary({ ...created, media_key: 'a4', media_source: 'sheet', roll_length_mm: null }).onSheet === true,
       'a sheet chart is presented as a sheet (no roll consumed)');
assert(!('cols' in ps) && !('nrows' in ps), 'no columns / rows in the printtarg summary');

console.log('Test 6 — structured identity refusals');
const err409 = { status: 409, message: 'HTTP 409', detail: {
  code: 'measurement_identity_incompatible', message: 'these measurements …', remedies: ['exclude_scan', 'use_base_last'],
  decision: { status: 'incompatible', conflicts: [{ a: 'CHT-A/a.ti3', b: 'CHT-A/b.ti3', status: 'incompatible', differences: [
    { code: 'condition_differs', field: 'condition', a: 'M0', b: 'M2' },
    { code: 'instrument_differs', field: 'instrument', a: 'colormunki', b: 'i1pro3plus' },
    { code: 'spectral_differs', field: 'spectral_bands', a: { n_bands: 16, start_nm: 400, end_nm: 700, step_nm: 20 },
      b: { n_bands: 36, start_nm: 380, end_nm: 730, step_nm: 10 } }] }] } } };
const r = identityRefusal(err409);
assert(r.code === 'measurement_identity_incompatible' && r.remedies.length === 2, 'code and remedies');
assert(r.conflicts[0].items.map((x) => x.field).join() === 'instrument,condition,spectral_bands', 'differences in a stable order');
assert(r.conflicts[0].items[2].a === '16 × 20 nm (400–700)', 'spectral grid summarized');
assert(identityRefusal({ status: 409, detail: { code: 'scans_not_comparable', decision: { conflicts: [] } } }).code === 'scans_not_comparable', 'concordance refusal');
assert(identityRefusal({ status: 409, detail: { error: 'name_conflict' } }) === null, 'other 409 → not an identity refusal');
assert(identityRefusal({ status: 422, detail: 'plain text' }) === null, 'string detail → null');
assert(identityRefusal({ status: 409, message: 'x', detail: { code: 'measurement_identity_incompatible' } }) !== null
       && identityRefusal({ code: 'measurement_record_invalid', record_error: { code: 'measurement_record_mismatch' } }).recordError.code === 'measurement_record_mismatch',
       'preview error objects and record errors');
assert(describeValue('condition', null) === null, 'unknown value stays unknown (not M0)');
assert(classifyBuildError(err409).kind === 'identity_refused', 'build error classified (re-preview)');
assert(classifyBuildError({ status: 409, detail: { error: 'inputs_changed' } }).kind === 'inputs_changed', 'inputs_changed unchanged');

console.log('Test 7 — scan identity, validation and multi-source notices');
assert(scanIdentityLine({ historical: true, method: 'sol' }) === null, 'historical SOL scan: nothing new to show');
const ext = scanIdentityLine({ method: 'external', instrument: 'i1pro3plus', condition: 'M2', missing: [], has_record: true });
assert(ext.instrument === 'i1pro3plus' && ext.condition === 'M2' && ext.hasRecord, 'external scan line');
assert(scanIdentityLine({ error: { code: 'measurement_record_malformed' } }).error === 'measurement_record_malformed', 'record error shown');
assert(validationIdentityNotice({ measurement_identity: { comparison: { status: 'same' }, warnings: [] } }) === null, 'same identity: no notice');
const vn = validationIdentityNotice({ measurement_identity: {
  comparison: { status: 'differs', differences: [{ scan: 'x' }] }, warnings: [{ code: 'validation_identity_differs' }],
  validation: { identity: { instrument: 'i1pro3plus', condition: 'M2' } },
  build: { scans: [{ identity: { instrument: 'colormunki' } }, { identity: { instrument: 'colormunki' } }] } } });
assert(vn.status === 'differs' && vn.warnings[0] === 'validation_identity_differs', 'cross-instrument validation: warning');
assert(vn.validationInstrument === 'i1pro3plus' && vn.buildInstruments.join() === 'colormunki', 'both sides named');
assert(validationIdentityNotice({}) === null && validationIdentityNotice({ measurement_identity: { error: 'x' } }) === null, 'no report → nothing');
assert(untrackedSourceProfiles({ sources: { source_profiles: [{ identity_tracked: false }, { identity_tracked: false }] } }) === 2, 'untracked source profiles counted');
assert(untrackedSourceProfiles({ sources: { source_profiles: [] } }) === 0 && untrackedSourceProfiles(null) === 0, 'none');
assert(previewCompatibility({ measurement_compatibility: { combined: false } }) === null, 'single scan: nothing');
assert(previewCompatibility({ measurement_compatibility: { combined: true, status: 'compatible', members: [1, 2] } }).nMembers === 2, 'combined scans');

console.log('Test 7b — printtarg -a / -h choices and the paper estimate request');
const LAYOUT = {
  instruments: [
    { key: 'colormunki', layout_options: { patch_scales: [1, 0.9, 0.8], default_patch_scale: 1, high_density: true,
      geometry: { standard: { 1: { patch_width_mm: 28 }, 0.9: { patch_width_mm: 25.2 }, 0.8: { patch_width_mm: 22.4 } },
                  high_density: { patch_width_mm: 13.7, stagger_mm: 7.25 } } } },
    { key: 'i1pro3plus', layout_options: { patch_scales: [1], default_patch_scale: 1, high_density: false,
      geometry: { standard: { 1: { patch_width_mm: 16 } } } } },
  ],
};
const cmo = layoutOptionsFor(LAYOUT, 'colormunki'), i1o = layoutOptionsFor(LAYOUT, 'i1pro3plus');
assert(layoutOptionsFor(LAYOUT, 'spyder') === null && layoutOptionsFor(null, 'colormunki') === null, 'unknown instrument → no options');
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
assert(same(resolveLayoutChoice({ patchScale: null, highDensity: false }, cmo), { patchScale: 1, highDensity: false }), 'default: standard, 100 %');
assert(same(resolveLayoutChoice({ patchScale: 0.8, highDensity: false }, cmo), { patchScale: 0.8, highDensity: false }), '80 % kept');
assert(same(resolveLayoutChoice({ patchScale: 0.8, highDensity: true }, cmo), { patchScale: 1, highDensity: true }), 'high density forces 100 % (never combined)');
assert(same(resolveLayoutChoice({ patchScale: 0.85, highDensity: false }, cmo), { patchScale: 1, highDensity: false }), 'size not offered → default');
assert(same(resolveLayoutChoice({ patchScale: 0.9, highDensity: true }, i1o), { patchScale: 1, highDensity: false }), 'i1Pro 3 Plus: 100 %, no high density');
assert(layoutGeometry(cmo, { patchScale: 0.9, highDensity: false }).patch_width_mm === 25.2, 'geometry of 90 %');
assert(layoutGeometry(cmo, { patchScale: 1, highDensity: true }).stagger_mm === 7.25, 'geometry of high density');
const req = (o) => estimateRequest({ instrument: 'colormunki', mediaKey: 'roll24', srcMode: 'targen', fCount: 2000,
  ti1Text: '', choice: { patchScale: 1, highDensity: true }, ...o });
assert(same(req({}).body, { instrument: 'colormunki', media_key: 'roll24', patch_scale: 1, high_density: true, n_patches: 2000 }), 'estimate from the targen -f count');
assert(req({ fCount: null }).skip === 'needs_count' && req({ mediaKey: null }).skip === 'incomplete', 'no count / no format → no request');
assert(req({ srcMode: 'ti1', ti1Text: 'CTI1' }).body.ti1_text === 'CTI1' && req({ srcMode: 'ti1' }).skip === 'incomplete', 'estimate from the .ti1');
const psHd = printtargSummary({ ...created, patch_scale: 1, high_density: true, patch_geometry: { measured: { strip_stagger_mm: 7.25 } } });
assert(psHd.highDensity && psHd.patchScale === 1 && psHd.patchGeometry.measured.strip_stagger_mm === 7.25, 'summary carries the options');
const psOld = printtargSummary(created);
assert(psOld.patchScale === 1 && psOld.highDensity === false && psOld.patchGeometry === null, 'a chart made before the options: defaults, no patch row');

console.log('Test 8 — deep link to the library');
assert(libraryHref('CHT-20261122-1600-AA') === '/mesures#chart=CHT-20261122-1600-AA', 'href');
assert(chartIdFromHash('#chart=CHT-20261122-1600-AA') === 'CHT-20261122-1600-AA', 'parsed');
assert(chartIdFromHash('#paper=X&chart=CHT-1') === 'CHT-1' && chartIdFromHash('#paper=X') === null && chartIdFromHash('') === null, 'other hashes');

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
