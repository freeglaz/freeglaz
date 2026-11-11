// Presentation helpers for the EXPLICIT exclusion of a reading (backend: POST
// /charts/{id}/scans/{ti3}/readings/{sid}/exclusion, phase 4C). Pure functions: the levels,
// the refusals and the per-base consequences all come from the backend; this module only
// labels them. An excluded reading is not a "bad" measurement, and a strongly suspect
// reading is not excluded unless the user decides so.

export const EXCLUSION_LEVELS = ['ok', 'info', 'warning', 'strong_warning', 'refused'];   // severity order
export const REASON_CHOICES = ['doubtful', 'visible_defect', 'other'];
const LEVEL_RANK = Object.fromEntries(EXCLUSION_LEVELS.map((l, i) => [l, i]));

/** Optional reason → the string stored by the backend (informative, never read to build). */
export function reasonPayload(kind, text = '') {
  const free = (text || '').trim().slice(0, 180);
  if (kind === 'other') return free ? `other: ${free}` : 'other';
  return REASON_CHOICES.includes(kind) ? kind : (free || null);
}

/** Stored reason → {key, text} for display (known codes translated, free text kept). */
export function reasonLabel(reason) {
  if (!reason) return null;
  if (reason === 'doubtful' || reason === 'visible_defect' || reason === 'other') return { key: `exclusion.reason.${reason}`, text: '' };
  if (reason.startsWith('other: ')) return { key: 'exclusion.reason.other', text: reason.slice(7) };
  return { key: null, text: reason };
}

/** Backend error of the exclusion API → {kind, code, impact}. kind: refused | scan_excluded |
 *  not_found | unreadable | network | error. A refusal is a decision of the backend, not a bug. */
export function classifyExclusionError(err) {
  const status = err?.status;
  const d = err?.detail && typeof err.detail === 'object' ? err.detail : {};
  if (status === 409 && d.error === 'exclusion_refused') return { kind: 'refused', code: d.code || null, impact: d.impact || null };
  if (status === 409 && d.error === 'scan_excluded') return { kind: 'scan_excluded', code: d.error, impact: null };
  if (status === 404) return { kind: 'not_found', code: d.error || null, impact: null };
  if (status === 422) return { kind: 'unreadable', code: d.error || null, impact: null };
  if (status === undefined || status === 0) return { kind: 'network', code: null, impact: null };
  return { kind: 'error', code: d.error || null, impact: null };
}

/** Impact (from the dry run) → view-model of the confirmation. */
export function impactView(impact) {
  if (!impact) return null;
  const r = impact.reading || {};
  const level = impact.chart_level_refusal ? 'refused' : (impact.level || 'ok');
  let headline;
  if (impact.chart_level_refusal) headline = `exclusion.impact.refused_${r.corner === 'black' ? 'black' : 'white'}`;
  else if ((impact.other_compatible_readings_in_chart || 0) > 0) headline = 'exclusion.impact.duplicate';
  else headline = `exclusion.impact.${r.position || 'interior'}`;
  const bases = Object.entries(impact.bases || {}).map(([base, b]) => ({
    base, used: !!b.reading_used, level: b.level || 'ok', hole: !!b.hole,
    recommend: !!b.recommend_remeasure, reasons: b.reasons || [], refusals: b.refusals || [] }));
  const differ = bases.length > 1 && new Set(bases.map((b) => b.level)).size > 1;
  return { level, headline, corner: r.corner || null, position: r.position || null,
           others: impact.other_compatible_readings_in_chart || 0, bases, basesDiffer: differ,
           recommend: bases.some((b) => b.recommend), canConfirm: level !== 'refused' };
}

/** Risk if excluded (from /patches exclusion_risk) → {level, key} for a table cell. */
export function riskView(risk) {
  if (!risk) return null;
  return { level: risk.level, key: `exclusion.risk.${risk.level}` };
}

export function maxLevel(levels) {
  return (levels || []).reduce((m, l) => ((LEVEL_RANK[l] ?? 0) > (LEVEL_RANK[m] ?? 0) ? l : m), 'ok');
}
