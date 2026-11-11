// Presentation of the profile status (v2) and of the version history (phase 4D). Pure.
// A stale profile is not a defective profile: it only means its inputs changed since it was
// built. The status fields are not mutually exclusive (toolchain is reported separately).

/** profile.status (backend) → badges [{key, kind}] (several may apply). */
export function statusBadges(status) {
  if (!status) return [];
  const out = [];
  const state = status.state || 'unknown';
  const kind = { up_to_date: 'ok', stale: 'warn', missing_input: 'danger', unknown: 'neutral', no_profile: 'neutral' }[state] || 'neutral';
  out.push({ key: `profile_history.state.${state}`, kind, code: state });
  if (status.toolchain_changed === true) out.push({ key: 'profile_history.toolchain.changed', kind: 'warn', code: 'toolchain_changed' });
  else if (status.toolchain_changed === null && state !== 'unknown' && state !== 'no_profile') {
    out.push({ key: 'profile_history.toolchain.unknown', kind: 'neutral', code: 'toolchain_unknown' });
  }
  return out;
}

/** One version (GET /builds) → what the list shows. Unknown values stay unknown. */
export function historyRow(b) {
  const preserved = b.kind === 'preserved';
  return {
    id: b.build_id, shortId: (b.build_id || '').slice(-6), current: !!b.current, intact: !!b.intact,
    preserved, provenanceUnknown: preserved && (b.provenance || 'unknown') !== 'from_previous_manifest',
    date: b.created_at || null, label: b.label || null,
    base: b.base_effective || null, baseFallback: !!(b.base_requested && b.base_effective && b.base_requested !== b.base_effective),
    nScans: b.n_scans, nExtra: b.n_extra_charts, nProfiles: b.n_source_profiles,
    nExcluded: b.n_excluded, nHoles: b.n_holes, flags: Array.isArray(b.colprof_flags) ? b.colprof_flags.join(' ') : null,
    manifest: !!b.manifest_available, references: b.references || [],
    comparable: !!b.intact,
  };
}

/** Two selected versions → {a: reference (older), b: new (younger)} by build id (chronological). */
export function orderPair(ids) {
  if (!Array.isArray(ids) || ids.length !== 2) return null;
  const [x, y] = [...ids].sort();
  return { a: x, b: y };
}
