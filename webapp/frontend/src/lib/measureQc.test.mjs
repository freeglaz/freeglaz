/**
 * Tests of lib/measureQc.js — presentation of the intrinsic measurement QC.
 * Fake reports shaped like the backend's (lib/z9_client/measure_qc.py, report_format 2).
 *
 * Run: node webapp/frontend/src/lib/measureQc.test.mjs
 */
import {
  STATUSES, classifyQcError, qcPanelState, summaryBadge, mergeQc, sortRows, filterRows, filterCounts,
  reasonMessages, findingMessage, labSwatch, buildCsv, alertFindingCodes, isLimitedByDensity, classifyPatchesError,
} from './measureQc.js';

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log(`  ✓ ${msg}`); }
  else { fail++; console.error(`  ✗ ${msg}`); }
}

const qcPatch = (id, status, de00, extra = {}) => ({
  sample_id: id, status, de00, score: de00 == null ? null : de00 / 1.5, position: 'interior',
  lab: [50, 1, 2], lab_pred: de00 == null ? [null, null, null] : [51, 1, 2], n_neighbors: 24,
  reliability: { assessable: status !== 'not_assessable', issues: status === 'not_assessable' ? ['MODEL_EXTRAPOLATION'] : [],
                 v: 0.2, s_loc: 0.5, neff: 10, asymmetry: 0.1, model: 'linear' },
  reasons: [], duplicates: null, ...extra,
});
const report = (patches, { findings = [], warnings = [] } = {}) => {
  const counts = { coherent: 0, to_check: 0, strong: 0, not_assessable: 0 };
  patches.forEach((p) => { counts[p.status]++; });
  return { algo_version: '1', report_format: 2, n_patches: patches.length, counts, findings,
           integrity: { warnings }, patches };
};
const measRows = (ids) => ids.map((id, k) => ({ id, rgb: [k, k, k], lab: [50 + k, 0, 0] }));

console.log('Test 1 — fully coherent scan');
{
  const rep = report(['1', '2', '3'].map((i) => qcPatch(i, 'coherent', 0.5)));
  const st = qcPanelState({ report: rep });
  assert(st.kind === 'clean' && st.nAttention === 0, 'clean → kind clean, nothing to examine');
  assert(summaryBadge({ counts: rep.counts, findings: [] }) === null, 'no badge for a clean scan');
}

console.log('Test 2 — one strongly suspect patch');
{
  const rep = report([qcPatch('1', 'coherent', 0.4), qcPatch('2', 'strong', 42,
    { reasons: [{ code: 'MODEL_RESIDUAL', residual: 41.96, score: 30.1 }] })]);
  const st = qcPanelState({ report: rep });
  assert(st.kind === 'attention' && st.nAttention === 1, 'one strong → attention, 1 to examine');
  const b = summaryBadge({ counts: rep.counts, findings: [] });
  assert(b && b.kind === 'warn' && b.count === 1, 'badge warn with count 1');
  const msg = reasonMessages(rep.patches[1].reasons)[0];
  assert(msg.key === 'measure_qc.reason.MODEL_RESIDUAL' && msg.params.residual === '42.0', 'reason → i18n key + rounded params');
}

console.log('Test 3 — several patches to check');
{
  const rep = report([qcPatch('1', 'to_check', 5), qcPatch('2', 'to_check', 6), qcPatch('3', 'coherent', 1)]);
  const st = qcPanelState({ report: rep });
  assert(st.kind === 'attention' && st.nAttention === 2, 'two to_check → attention, 2');
  const b = summaryBadge({ counts: rep.counts, findings: [] });
  assert(b.kind === 'neutral' && b.count === 2, 'to_check only → discreet neutral badge');
}

console.log('Test 4 — not assessable measurements (a limit, never hidden, never "validated")');
{
  const rep = report([qcPatch('1', 'not_assessable', null), qcPatch('2', 'coherent', 0.3)]);
  const st = qcPanelState({ report: rep });
  assert(st.kind === 'limited' && st.counts.not_assessable === 1, 'NE only → kind limited (not clean)');
  const rows = mergeQc(measRows(['1', '2']), rep);
  assert(filterRows(rows, 'flagged').map((r) => r.id).join() === '1', '"flagged" filter keeps not_assessable');
  assert(filterRows(rows, 'not_assessable').length === 1, 'dedicated not_assessable filter');
  assert(summaryBadge({ counts: rep.counts, findings: [] }).key === 'measure_qc.badge.limits', 'badge "limits" for NE only');
}

console.log('Test 5 — inconsistent zone');
{
  const rep = report([qcPatch('1', 'not_assessable', null), qcPatch('2', 'coherent', 1)],
    { findings: [{ code: 'ZONE_INCOHERENT', n: 29, sample_ids: ['1'], rgb8_center: [26, 24, 28] }] });
  const st = qcPanelState({ report: rep });
  assert(st.kind === 'attention', 'zone finding → attention even without strong');
  const f = findingMessage(rep.findings[0]);
  assert(f.key === 'measure_qc.finding.ZONE_INCOHERENT' && f.params.rgb === '26, 24, 28' && f.params.n === 29, 'zone message params');
  assert(summaryBadge({ counts: rep.counts, findings: ['ZONE_INCOHERENT'] }).kind === 'warn', 'zone → warn badge');
}

console.log('Test 6 — globally inconsistent set');
{
  const rep = report([qcPatch('1', 'not_assessable', null)], { findings: [{ code: 'SET_INCOHERENT', fraction: 0.41 }] });
  assert(qcPanelState({ report: rep }).kind === 'attention', 'set finding → attention');
  assert(findingMessage(rep.findings[0]).params.pct === 41, 'set fraction → percent');
}

console.log('Test 7 — Lab/XYZ discrepancy warning');
{
  const rep = report([qcPatch('1', 'coherent', 0.2)], { warnings: ['LAB_COLUMNS_INCONSISTENT_WITH_XYZ'] });
  const st = qcPanelState({ report: rep });
  assert(st.kind === 'limited' && st.warnings[0] === 'LAB_COLUMNS_INCONSISTENT_WITH_XYZ', 'warning surfaced, not "clean"');
}

console.log('Test 8 — QC unavailable / invalid / technical error / loading');
{
  const e422 = Object.assign(new Error('x'), { status: 422, detail: { code: 'TRUNCATED', message: 'truncated' } });
  const e404 = Object.assign(new Error('x'), { status: 404, detail: { code: 'SCAN_NOT_FOUND' } });
  const e500 = Object.assign(new Error('x'), { status: 500, detail: 'boom' });
  const eNet = new TypeError('Failed to fetch');
  assert(qcPanelState({ loading: true }).kind === 'loading', 'loading state');
  const s422 = qcPanelState({ error: e422 });
  assert(s422.kind === 'invalid' && s422.code === 'TRUNCATED', '422 → invalid with code');
  assert(!['clean', 'limited', 'attention'].includes(s422.kind), '422 is NEVER read as "no suspicious patch"');
  assert(qcPanelState({ error: e404 }).kind === 'unavailable', '404 → unavailable');
  assert(qcPanelState({ error: e500 }).kind === 'error' && qcPanelState({ error: eNet }).kind === 'error', '5xx / network → error');
  assert(classifyQcError(e500).code === null, 'string detail → no code');
  assert(qcPanelState({}).kind === 'none', 'nothing yet → none (panel hidden)');
}

console.log('Test 9 — sorting');
{
  const rep = report([qcPatch('10', 'coherent', 0.5), qcPatch('2', 'strong', 30), qcPatch('3', 'to_check', 6),
                      qcPatch('1', 'not_assessable', null), qcPatch('20', 'strong', 12)]);
  const rows = mergeQc(measRows(['10', '2', '3', '1', '20']), rep);
  assert(sortRows(rows, 'suspicion').map((r) => r.id).join() === '2,20,3,1,10', 'suspicion: strong (ΔE desc) > to_check > NE > coherent');
  assert(sortRows(rows, 'id').map((r) => r.id).join() === '1,2,3,10,20', 'back to the natural patch order');
  assert(sortRows(rows, 'de00', 'desc').map((r) => r.id).join() === '2,20,3,10,1', 'ΔE desc, missing ΔE last');
  assert(sortRows(rows, 'de00', 'asc').map((r) => r.id).join() === '10,3,20,2,1', 'ΔE asc, missing ΔE still last');
  const noQc = mergeQc(measRows(['10', '2']), null);
  assert(sortRows(noQc, 'suspicion').map((r) => r.id).join() === '2,10', 'without QC, suspicion degrades to natural order');
  assert(STATUSES[0] === 'strong' && STATUSES[3] === 'coherent', 'status order constant');
}

console.log('Test 10 — filters and counts');
{
  const rep = report([qcPatch('1', 'strong', 20), qcPatch('2', 'to_check', 5), qcPatch('3', 'not_assessable', null),
                      qcPatch('4', 'coherent', 1)]);
  const rows = mergeQc(measRows(['1', '2', '3', '4']), rep);
  const c = filterCounts(rows);
  assert(c.all === 4 && c.flagged === 3 && c.not_assessable === 1, 'counts all / flagged (incl. NE) / NE');
  assert(filterRows(rows, 'all').length === 4, '"all" keeps everything');
  const orphan = mergeQc(measRows(['1', 'X']), rep);
  assert(orphan[1].qc === null && filterRows(orphan, 'flagged').length === 1, 'measurement absent from report → no QC, not flagged');
}

console.log('Test 11 — CSV export');
{
  const rep = report([qcPatch('1', 'strong', 20, { reasons: [{ code: 'MODEL_RESIDUAL', residual: 20 }, { code: 'MONOTONIC_INVERSION', n_inversions: 2 }] }),
                      qcPatch('2,b', 'not_assessable', null)]);
  const rows = mergeQc(measRows(['1', '2,b']), rep);
  const csv = buildCsv(rows, true).split('\n');
  assert(csv[0] === 'id,R,G,B,L,a,b,qc_status,de00,score,L_pred,a_pred,b_pred,position,prediction_assessable,model,reasons', 'QC header');
  assert(csv[1].endsWith('strong,20,13.333333333333334,51,1,2,interior,yes,linear,MODEL_RESIDUAL|MONOTONIC_INVERSION'), 'reason CODES, language-independent');
  assert(csv[2].startsWith('"2,b",') && csv[2].includes(',not_assessable,,,,,,interior,no,linear,'), 'quoted id, empty ΔE / prediction for NE');
  assert(buildCsv(rows, false).split('\n')[0] === 'id,R,G,B,L,a,b', 'without QC: original columns unchanged');
}

console.log('Test 12 — approximate swatches');
{
  const s = labSwatch([50, 0, 0]);
  assert(s && /^rgb\(/.test(s.css) && s.outOfSrgb === false, 'neutral Lab → in sRGB');
  const o = labSwatch([50, 0, -110]);
  assert(o && o.outOfSrgb === true, 'very saturated blue → flagged as approximate (outside sRGB)');
  assert(labSwatch([null, null, null]) === null, 'no prediction → no swatch');
}

console.log('Test 13 — unknown codes degrade gracefully');
{
  const m = reasonMessages([{ code: 'FUTURE_CODE', x: 1.234 }])[0];
  assert(m.key === null && m.code === 'FUTURE_CODE', 'unknown reason → code shown, no fake key');
  assert(findingMessage({ code: 'FUTURE_FINDING' }).key === null, 'unknown finding → no fake key');
}

console.log('Test 14 — set verdict on a sparse chart = LIMITED check; finding-only alert on a dense chart');
{
  const findings = [{ code: 'SET_INCOHERENT', fraction: 0.16 }, { code: 'LOW_DENSITY', n_patches: 64, threshold: 250 }];
  const rep = report([qcPatch('1', 'not_assessable', null), qcPatch('2', 'coherent', 1)], { findings });
  const st = qcPanelState({ report: rep });
  assert(st.kind === 'limited' && st.limitedByDensity === true, 'sparse chart + SET_INCOHERENT → "limited check", not an alert');
  assert(st.findings.length === 2 && st.counts.not_assessable === 1,
         'the possible inconsistency and the diagnostics stay in the view-model (shown below the headline)');
  assert(findingMessage(findings[0], findings).key === 'measure_qc.finding.SET_INCOHERENT_LOW_DENSITY',
         'sparse chart → nuanced set message (density, not necessarily mis-attribution)');
  assert(findingMessage(findings[0], [findings[0]]).key === 'measure_qc.finding.SET_INCOHERENT', 'dense chart → standard set message');
  const b = summaryBadge({ counts: rep.counts, findings: ['SET_INCOHERENT', 'LOW_DENSITY'] });
  assert(b.key === 'measure_qc.badge.limited_check' && b.kind === 'neutral' && b.count === 1, 'sparse chart badge: neutral "limited check"');
  const b0 = summaryBadge({ counts: { ...rep.counts, not_assessable: 0 }, findings: ['SET_INCOHERENT', 'LOW_DENSITY'] });
  assert(b0 && b0.key === 'measure_qc.badge.limited_check', 'limited-check badge kept even without NE (the limit is never silent)');
  // dense chart: a set-level finding alone still alerts, and never reads "0 to examine"
  const dense = report([qcPatch('1', 'coherent', 1)], { findings: [findings[0]] });
  const sd = qcPanelState({ report: dense });
  assert(sd.kind === 'attention' && sd.nAttention === 0 && !sd.limitedByDensity, 'dense chart: set finding without flagged patch → attention');
  const bd = summaryBadge({ counts: dense.counts, findings: ['SET_INCOHERENT'] });
  assert(bd.key === 'measure_qc.badge.finding' && bd.kind === 'warn', 'dense chart badge never reads "0 to examine"');
}

console.log('Test 15 — density limit never hides patch-level or zone alerts');
{
  const findings = [{ code: 'SET_INCOHERENT', fraction: 0.2 }, { code: 'LOW_DENSITY', n_patches: 64 }];
  const flagged = report([qcPatch('1', 'strong', 30), qcPatch('2', 'coherent', 1)], { findings });
  const s1 = qcPanelState({ report: flagged });
  assert(s1.kind === 'attention' && s1.nAttention === 1 && !s1.limitedByDensity, 'sparse chart with a strong patch → attention');
  const zone = report([qcPatch('1', 'coherent', 1)], { findings: [{ code: 'ZONE_INCOHERENT', n: 6, rgb8_center: [1, 2, 3] }, findings[1]] });
  assert(qcPanelState({ report: zone }).kind === 'attention', 'ZONE_INCOHERENT on a sparse chart still alerts');
  assert(summaryBadge({ counts: zone.counts, findings: ['ZONE_INCOHERENT', 'LOW_DENSITY'] }).kind === 'warn', 'zone badge stays warn');
  const low = report([qcPatch('1', 'coherent', 1)], { findings: [findings[1]] });
  const s3 = qcPanelState({ report: low });
  assert(s3.kind === 'limited' && !s3.limitedByDensity, 'LOW_DENSITY alone → limited, standard wording');
  assert(JSON.stringify(alertFindingCodes(['SET_INCOHERENT', 'LOW_DENSITY'])) === '[]'
         && JSON.stringify(alertFindingCodes(['SET_INCOHERENT'])) === '["SET_INCOHERENT"]'
         && JSON.stringify(alertFindingCodes(['ZONE_INCOHERENT', 'LOW_DENSITY'])) === '["ZONE_INCOHERENT"]'
         && JSON.stringify(alertFindingCodes(null)) === '[]', 'alertFindingCodes');
  assert(isLimitedByDensity(['LOW_DENSITY', 'SET_INCOHERENT']) && !isLimitedByDensity(['LOW_DENSITY']) && !isLimitedByDensity(undefined),
         'isLimitedByDensity');
}

console.log('Test 16 — measurements read errors are localized (raw backend text never shown)');
{
  const e = (status, detail) => Object.assign(new Error(typeof detail === 'string' ? detail : `HTTP ${status}`), { status, detail });
  const u = classifyPatchesError(e(422, { code: 'UNREADABLE', message: 'unreadable ti3: truncated ti3 (no END_DATA)' }));
  assert(u.key === 'scan.measures.error.unreadable' && u.code === 'UNREADABLE', '422 → "unreadable measurements"');
  assert(classifyPatchesError(e(422, 'unreadable ti3: legacy string detail')).key === 'scan.measures.error.unreadable',
         'legacy string detail → same localized key');
  assert(classifyPatchesError(e(404, 'unknown scan: x.ti3')).key === 'scan.measures.error.not_found', '404 → "scan not found"');
  assert(classifyPatchesError(e(500, undefined)).key === 'scan.measures.error.unavailable', '5xx → technical error');
  assert(classifyPatchesError(new TypeError('Failed to fetch')).key === 'scan.measures.error.unavailable', 'network error → technical error');
  assert(classifyPatchesError(null).key === 'scan.measures.error.unavailable', 'null → technical error');
  assert(!Object.values(u).some((v) => String(v).includes('truncated')), 'the descriptor carries no raw backend text');
}

console.log('Test 17 — "excluded" filter = use state, independent of the QC status');
{
  const rows = [{ id: '1', excluded: true, qc: { status: 'coherent' } }, { id: '2', excluded: false, qc: { status: 'strong' } },
                { id: '3', excluded: true, qc: null }];
  assert(filterRows(rows, 'excluded').map((r) => r.id).join() === '1,3', 'excluded rows, whatever their QC status');
  assert(filterRows(rows, 'flagged').map((r) => r.id).join() === '2', 'a strong suspect is flagged, not excluded');
  assert(filterCounts(rows).excluded === 2 && filterCounts(rows).flagged === 1, 'counts');
}

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
