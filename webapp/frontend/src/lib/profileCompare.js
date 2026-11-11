// Presentation of a comparison of two stored ICC versions (POST /builds/compare, phase 4D).
// Every number is the backend's (xicclu lookups); this module only arranges and words them.
// A transform difference, not a measurement: it never says which profile prints better.

/** Rows of the statistics table: one per intent × point set. */
export function statRows(result) {
  const rows = [];
  for (const intent of ['relative', 'perceptual']) {
    const r = result?.intents?.[intent];
    if (!r) continue;
    for (const set of ['measured', 'grid']) {
      if (r[set]) rows.push({ intent, set, n: r[set].n, mean: r[set].mean, p95: r[set].p95, max: r[set].max });
    }
  }
  return rows;
}

/** Most displaced colours (relative intent, measured points and grid), one per colour. */
export function topColours(result, n = 8) {
  const r = result?.intents?.relative || {};
  const best = new Map();                       // one entry per colour: its LARGEST difference
  for (const set of ['measured', 'grid']) {
    for (const t of r[set]?.top || []) {
      const k = t.rgb255.join(',');
      if (!best.has(k) || t.de00 > best.get(k).de00) best.set(k, { ...t, set });
    }
  }
  return [...best.values()].sort((a, b) => b.de00 - a.de00).slice(0, n);
}

/** Focus point (reading excluded in one version only) → {key, params}: "localized" when its
 *  neighbourhood moves clearly more than the rest (max near ≥ 3 × p95 elsewhere and ≥ 0.5 ΔE00). */
export function focusFinding(f) {
  const near = f?.near || {};
  const far = f?.elsewhere || {};
  const localized = near.max != null && far.p95 != null && near.max >= 0.5 && near.max >= 3 * Math.max(far.p95, 0.01);
  return {
    key: localized ? 'profile_compare.focus_localized' : 'profile_compare.focus_diffuse',
    params: { label: f?.label, side: f?.excluded_in === 'a' ? 'A' : 'B', at: _f(f?.de00_at_point), nearN: near.n ?? 0,
              nearMax: _f(near.max), farP95: _f(far.p95), farMax: _f(far.max) },
    localized,
  };
}

/** Annotation when a profile's perceptual table is a byte copy of its colorimetric one (read by
 *  the backend from the A2B0/A2B1 tags, never from the colprof options): then the perceptual
 *  rows necessarily repeat the relative ones. Only when the perceptual intent was compared. */
export function perceptualNote(result) {
  const m = result?.method || {};
  const same = m.perceptual_same_as_relative || {};
  if (!m.perceptual_compared) return null;
  if (same.a === true && same.b === true) return { key: 'profile_compare.perceptual_same_both', params: {} };
  if (same.a === true || same.b === true) return { key: 'profile_compare.perceptual_same_one', params: { side: same.a === true ? 'A' : 'B' } };
  return null;
}

export function whiteBlackRows(result) {
  const wb = result?.white_black || {};
  return ['white_point_tag', 'black_point_tag', 'device_white', 'device_black']
    .filter((k) => wb[k]).map((k) => ({ key: k, de00: wb[k].de00, lab_a: wb[k].lab_a, lab_b: wb[k].lab_b }));
}

function _f(v) { return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(2) : '—'; }
export const fmt2 = _f;
