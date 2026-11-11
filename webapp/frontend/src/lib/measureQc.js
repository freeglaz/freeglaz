// Presentation helpers for the INTRINSIC measurement QC (backend: lib/z9_client/measure_qc.py).
// Pure functions, no React, no scientific logic: statuses, reasons and the reliability verdict
// all come from the backend report — this module only orders, filters, labels and exports them.
// Read-only by construction: nothing here talks to the API.
import { compareNatural } from './naturalSort.js';
import { labToBackgroundRgb } from './labChromaBackground.js';

// Stable codes mirrored from the backend (checked against measure_qc.py by measureQc.i18n.test.mjs).
export const STATUSES = ['strong', 'to_check', 'not_assessable', 'coherent'];   // suspicion order
export const POSITIONS = ['interior', 'face', 'edge', 'corner'];
export const MODELS = ['linear', 'quadratic'];
export const REASON_CODES = [
  'PHYS_LIGHTER_THAN_PAPER', 'PHYS_WHITE_SUSPECT', 'PHYS_CHROMA', 'PHYS_NEGATIVE_XYZ',
  'DUP_DISCORDANT', 'DUP_PAIR_DIVERGENT', 'DUP_TWIN_DIVERGES', 'DUP_CONFIRMED',
  'MODEL_RESIDUAL', 'MODEL_EXTRAPOLATION', 'MODEL_INCOHERENT_NEIGHBORHOOD', 'MODEL_SPARSE',
  'HIGH_DEVIATION_UNRELIABLE_PREDICTION', 'MONOTONIC_INVERSION',
];
export const FINDING_CODES = ['SET_INCOHERENT', 'ZONE_INCOHERENT', 'LOW_DENSITY'];
export const WARNING_CODES = [
  'LAB_COLUMNS_INCONSISTENT_WITH_XYZ', 'NO_XYZ_LAB_COLUMNS_USED', 'XYZ_SCALE_0_1_RESCALED',
  'NUMBER_OF_SETS_MISMATCH', 'MERGED_FILE_ANALYSED', 'IDENTICAL_DUPLICATE_ROWS',
];
export const ERROR_CODES = [
  'TRUNCATED', 'NO_DATA', 'MERGED_FILE', 'MISSING_COLUMNS', 'SHORT_ROW', 'DUPLICATE_SAMPLE_ID',
  'INVALID_VALUE', 'RGB_OUT_OF_RANGE', 'INVALID_COLORIMETRY',
  'SCAN_NOT_FOUND', 'NOT_A_RAW_SCAN', 'UNREADABLE',                      // service-level
];
export const FILTERS = ['all', 'flagged', 'not_assessable'];
export const SORT_KEYS = ['suspicion', 'id', 'rgb', 'labL', 'de00'];

const RANK = Object.fromEntries(STATUSES.map((s, i) => [s, i]));
const FLAGGED = new Set(['strong', 'to_check', 'not_assessable']);         // NE never hidden silently
const ATTENTION_FINDINGS = new Set(['SET_INCOHERENT', 'ZONE_INCOHERENT']);

/** Finding codes that raise the ALERT level. On a sparse chart (LOW_DENSITY) a set-level
 *  SET_INCOHERENT may come from the low density itself: it stays reported and worded as such
 *  (findingMessage), but it only LIMITS the check. A localized ZONE_INCOHERENT always alerts. */
export function alertFindingCodes(codes = []) {
  const set = new Set(codes || []);
  return [...set].filter((c) => ATTENTION_FINDINGS.has(c) && !(c === 'SET_INCOHERENT' && set.has('LOW_DENSITY')));
}

/** True when the check is limited by the chart density: SET_INCOHERENT reported on a LOW_DENSITY chart. */
export function isLimitedByDensity(codes = []) {
  const set = new Set(codes || []);
  return set.has('SET_INCOHERENT') && set.has('LOW_DENSITY');
}

/** API error → {kind, code}. kind: invalid (422, ti3 not analysable) | unavailable (404) |
 *  error (network / 5xx / anything else). A 422 is NEVER "no suspicious patch". */
export function classifyQcError(err) {
  const status = err?.status;
  const code = (err?.detail && typeof err.detail === 'object' && err.detail.code) || null;
  if (status === 422) return { kind: 'invalid', code };
  if (status === 404) return { kind: 'unavailable', code: code || 'SCAN_NOT_FOUND' };
  return { kind: 'error', code };
}

/** Failure of the measurements read (/patches) → localized i18n key. The raw backend text never
 *  reaches the UI: it is logged server-side (freeglaz.log) and in the browser console.
 *  404 scan gone · 422 ti3 unreadable (damaged / incomplete) · anything else technical. */
export function classifyPatchesError(err) {
  const status = err?.status;
  const code = (err?.detail && typeof err.detail === 'object' && err.detail.code) || null;
  if (status === 404) return { key: 'scan.measures.error.not_found', code: code || 'SCAN_NOT_FOUND' };
  if (status === 422) return { key: 'scan.measures.error.unreadable', code: code || 'UNREADABLE' };
  return { key: 'scan.measures.error.unavailable', code };
}

/** View-model of the QC panel. {kind, counts, n, findings, warnings, nAttention, limitedByDensity}
 *  kind: loading | attention | limited | clean | invalid | unavailable | error | none.
 *  limitedByDensity: kind 'limited' because SET_INCOHERENT was reported on a sparse chart
 *  (headline "limited check", never "no inconsistency detected"). */
export function qcPanelState({ loading = false, report = null, error = null } = {}) {
  if (loading) return { kind: 'loading' };
  if (error) return { kind: classifyQcError(error).kind, code: classifyQcError(error).code };
  if (!report) return { kind: 'none' };
  const counts = { strong: 0, to_check: 0, not_assessable: 0, coherent: 0, ...(report.counts || {}) };
  const findings = report.findings || [];
  const warnings = report.integrity?.warnings || [];
  const nAttention = counts.strong + counts.to_check;
  const codes = findings.map((f) => f.code);
  let kind = 'clean';
  if (nAttention > 0 || alertFindingCodes(codes).length > 0) kind = 'attention';
  else if (counts.not_assessable > 0 || findings.length > 0 || warnings.length > 0) kind = 'limited';
  return { kind, counts, n: report.n_patches ?? 0, findings, warnings, nAttention,
           limitedByDensity: kind === 'limited' && isLimitedByDensity(codes) };
}

/** Compact indicator for a scan row, from chart_detail's scans[].qc_summary (may be null). */
export function summaryBadge(summary) {
  if (!summary?.counts) return null;
  const c = summary.counts;
  const nAttention = (c.strong || 0) + (c.to_check || 0);
  const codes = summary.findings || [];
  const attentionFinding = alertFindingCodes(codes).length > 0;
  if (nAttention > 0 || attentionFinding) {
    return { kind: (c.strong || 0) > 0 || attentionFinding ? 'warn' : 'neutral', count: nAttention,
             key: nAttention > 0 ? 'measure_qc.badge.attention' : 'measure_qc.badge.finding' };
  }
  if (isLimitedByDensity(codes)) return { kind: 'neutral', count: c.not_assessable || 0, key: 'measure_qc.badge.limited_check' };
  if ((c.not_assessable || 0) > 0) return { kind: 'neutral', count: c.not_assessable, key: 'measure_qc.badge.limits' };
  return null;
}

/** Join the measurement rows (/patches) with the QC patches (by SAMPLE_ID). */
export function mergeQc(patches, report) {
  const byId = new Map((report?.patches || []).map((p) => [String(p.sample_id), p]));
  return (patches || []).map((p) => ({ ...p, qc: byId.get(String(p.id)) || null }));
}

const _num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const _rgbKey = (r) => r.rgb[0] * 1e6 + r.rgb[1] * 1e3 + r.rgb[2];

/** Sort merged rows. 'suspicion' = backend status order then ΔE00 desc then natural id;
 *  'id' = natural order of the patches; ΔE00 missing → always last. */
export function sortRows(rows, key = 'suspicion', dir = 'asc') {
  const arr = [...rows];
  const byId = (a, b) => compareNatural(a.id, b.id);
  let cmp;
  if (key === 'suspicion') {
    cmp = (a, b) => {
      const ra = RANK[a.qc?.status] ?? STATUSES.length;
      const rb = RANK[b.qc?.status] ?? STATUSES.length;
      if (ra !== rb) return ra - rb;
      const da = _num(a.qc?.de00); const db = _num(b.qc?.de00);
      if (da !== db) return (db ?? -1) - (da ?? -1);
      return byId(a, b);
    };
    return arr.sort(cmp);                                   // fixed direction: most suspicious first
  }
  if (key === 'id') cmp = byId;
  else if (key === 'rgb') cmp = (a, b) => _rgbKey(a) - _rgbKey(b) || byId(a, b);
  else if (key === 'labL') cmp = (a, b) => a.lab[0] - b.lab[0] || byId(a, b);
  else if (key === 'de00') {
    const sorted = arr.sort((a, b) => {
      const da = _num(a.qc?.de00); const db = _num(b.qc?.de00);
      if (da == null && db == null) return byId(a, b);
      if (da == null) return 1;
      if (db == null) return -1;
      return (dir === 'desc' ? db - da : da - db) || byId(a, b);
    });
    return sorted;                                          // nulls last in both directions
  } else cmp = byId;
  arr.sort(cmp);
  return dir === 'desc' ? arr.reverse() : arr;
}

export function filterRows(rows, filter = 'all') {
  if (filter === 'flagged') return rows.filter((r) => FLAGGED.has(r.qc?.status));
  if (filter === 'not_assessable') return rows.filter((r) => r.qc?.status === 'not_assessable');
  return rows;
}

export function filterCounts(rows) {
  return {
    all: rows.length,
    flagged: rows.filter((r) => FLAGGED.has(r.qc?.status)).length,
    not_assessable: rows.filter((r) => r.qc?.status === 'not_assessable').length,
  };
}

const _fmt = (v, nd = 1) => (typeof v === 'number' && Number.isFinite(v) ? v.toFixed(nd) : v);

/** Backend reason objects → i18n descriptors {key, params, code}. Unknown codes keep their code. */
export function reasonMessages(reasons) {
  return (reasons || []).map((r) => {
    const { code, ...rest } = r;
    const params = {};
    for (const [k, v] of Object.entries(rest)) params[k] = _fmt(v, k === 'n' || k.startsWith('n_') ? 0 : 1);
    return { code, params, key: REASON_CODES.includes(code) ? `measure_qc.reason.${code}` : null };
  });
}

/** Finding → i18n descriptor. `allFindings` (the report's findings) lets a set-level verdict on a
 *  SPARSE chart be worded honestly: there, incoherent neighbourhoods may come from the low density
 *  rather than from a mis-attributed chart (the backend reports both facts; we only word them). */
export function findingMessage(f, allFindings = []) {
  if (!f?.code) return null;
  const params = { n: f.n ?? f.n_patches, pct: f.fraction != null ? Math.round(f.fraction * 100) : undefined,
                   rgb: Array.isArray(f.rgb8_center) ? f.rgb8_center.join(', ') : undefined,
                   threshold: f.threshold };
  if (f.code === 'SET_INCOHERENT' && (allFindings || []).some((x) => x?.code === 'LOW_DENSITY')) {
    return { code: f.code, key: 'measure_qc.finding.SET_INCOHERENT_LOW_DENSITY', params };
  }
  return { code: f.code, key: FINDING_CODES.includes(f.code) ? `measure_qc.finding.${f.code}` : null, params };
}

/** Approximate on-screen swatch of a Lab value (sRGB, clipped). NOT colour-accurate:
 *  numbers remain the reference; never used to judge severity. */
export function labSwatch(lab) {
  if (!Array.isArray(lab) || lab.some((v) => typeof v !== 'number' || !Number.isFinite(v))) return null;
  const [r, g, b, fade] = labToBackgroundRgb(lab[0], lab[1], lab[2], 'sRGB');
  const c = (v) => Math.round(Math.max(0, Math.min(1, v)) * 255);
  const outOfSrgb = !(fade >= 1);
  const css = fade > 0 ? `rgb(${c(r)}, ${c(g)}, ${c(b)})` : 'repeating-linear-gradient(45deg,#999 0 3px,#bbb 3px 6px)';
  return { css, outOfSrgb };
}

const _csvCell = (v) => {
  if (v == null) return '';
  const s = String(v);
  return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

/** CSV of the displayed rows. With QC: status, ΔE00, score, predicted Lab, position,
 *  prediction reliability and reason CODES (stable, language-independent). */
export function buildCsv(rows, withQc) {
  const head = ['id', 'R', 'G', 'B', 'L', 'a', 'b'];
  if (withQc) head.push('qc_status', 'de00', 'score', 'L_pred', 'a_pred', 'b_pred', 'position',
                        'prediction_assessable', 'model', 'reasons');
  const lines = rows.map((r) => {
    const cells = [r.id, ...r.rgb, ...r.lab];
    if (withQc) {
      const q = r.qc;
      cells.push(q?.status, q?.de00, q?.score, ...(q?.lab_pred || [null, null, null]), q?.position,
                 q ? (q.reliability?.assessable ? 'yes' : 'no') : null, q?.reliability?.model,
                 q ? (q.reasons || []).map((x) => x.code).join('|') : null);
    }
    return cells.map(_csvCell).join(',');
  });
  return [head.join(','), ...lines].join('\n');
}
