// Build request + preview presentation (phase 4D). Every profile build launched by the
// interface goes through the same contract: preview (policy apply_all, no write) → explicit
// confirmation → build with the preview's inputs fingerprint. Pure functions only.

/** The build request of the current selection (shared by Measurements and the wizard). */
export function buildRequestBody({ nKept = 0, profileBase = 'average', colprofFlags = '', extraSources = {} }) {
  const body = { profile_base: nKept >= 2 ? profileBase : 'last' };
  if ((colprofFlags || '').trim()) body.colprof_flags = colprofFlags.trim();
  if (extraSources.extra_chart_ids?.length) body.extra_chart_ids = [...extraSources.extra_chart_ids];
  if (extraSources.source_profiles?.length) body.source_profiles = extraSources.source_profiles.map((p) => ({ path: p.path }));
  if (extraSources.name) body.name = extraSources.name;
  return body;
}

/** What the preview needs (the name and the collision intent do not change the inputs). */
export function previewRequest(body) {
  const { name, on_conflict, ...rest } = body || {};   // eslint-disable-line no-unused-vars
  return { ...rest, policy: 'apply_all' };
}

/** Stable key of a preview: request + the chart state it was computed on (roles, exclusions).
 *  Any change → another key → the previous preview AND its confirmation are invalid. */
export function previewKey(chartId, body, stateStamp = '') {
  const r = previewRequest(body);
  return JSON.stringify([chartId, r.profile_base || 'last', r.extra_chart_ids || [],
    (r.source_profiles || []).map((p) => p.path), r.colprof_flags || '', stateStamp]);
}

/** Stamp of the chart state that changes the inputs (scan roles and exclusions). */
export function chartStateStamp(detail) {
  return JSON.stringify((detail?.scans || []).map((s) => [s.ti3, s.role || (s.kept ? 'included' : 'excluded'),
                                                          [...(s.rejected_readings || [])].sort()]));
}

const _count = (groups) => (groups || []).reduce((n, g) => n + ((g.sids || []).length), 0);

/** Preview → what the user must see before building. Numbers come from the backend. */
export function summarizePreview(p) {
  if (!p) return null;
  const warnings = (p.guards || []).filter((g) => g.level !== 'refused');
  const s = {
    baseRequested: p.base_requested || 'last', baseEffective: p.base_effective,
    baseFallback: (p.base_requested || 'last') !== p.base_effective,
    nExcluded: _count(p.rejections_applied), nInactive: _count(p.rejections_not_applied),
    nUnmatched: _count(p.rejections_unmatched), nHoles: (p.holes || []).length,
    warnings, refusals: p.refusals || [], refused: !!p.would_be_refused,
    recommend: !!p.recommend_remeasure, reasons: p.reasons || [],
    nNewlyApplied: _count(p.newly_applied_vs_legacy), nStrongIncluded: _count(p.qc_strong_included),
    differsFromCurrent: p.differs_from_current_build,
    inputsChangedSinceCurrent: !!(p.current_inputs_fingerprint && p.current_inputs_fingerprint !== p.inputs_fingerprint),
    fingerprint: p.inputs_fingerprint,
  };
  s.needsConfirmation = !s.refused && (s.nExcluded > 0 || s.nNewlyApplied > 0 || s.nStrongIncluded > 0
                                       || s.warnings.length > 0 || s.recommend);
  return s;
}

/** Build error → {kind}: name_conflict | inputs_changed | coverage_refused | fingerprint_required |
 *  name_length | name_invalid | error. inputs_changed is never retried automatically. */
export function classifyBuildError(err) {
  const d = err?.detail && typeof err.detail === 'object' ? err.detail : {};
  if (err?.status === 409 && d.error === 'name_conflict') return { kind: 'name_conflict', detail: d };
  if (err?.status === 409 && d.error === 'inputs_changed') return { kind: 'inputs_changed', detail: d };
  if (err?.status === 409 && d.error === 'coverage_refused') return { kind: 'coverage_refused', detail: d };
  if (err?.status === 422 && d.error === 'fingerprint_required') return { kind: 'fingerprint_required', detail: d };
  if (err?.status === 422) return { kind: 'name_length', detail: d };
  if (err?.status === 400) return { kind: 'name_invalid', detail: d };
  return { kind: 'error', detail: d };
}
