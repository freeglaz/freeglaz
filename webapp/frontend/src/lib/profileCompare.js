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

/** Focus point (reading excluded in one version only) → {key, params}. Always DESCRIPTIVE (ΔE00 at
 *  the patch, max among the neighbours within the radius, max and p95 elsewhere). It adds "the
 *  difference is concentrated around this patch" only when the largest change elsewhere is at most
 *  a third of the largest change near the patch — literally true then. Phase 4E bench (464- and
 *  2000-patch charts, interior/face/edge/corner, strong and moderate errors, sane readings
 *  excluded; 40 comparisons): 40/40 meet it at radii 0.10–0.20, smallest ratio 5.4. No case was
 *  genuinely spread, so no "not concentrated" conclusion is ever drawn (the 4D rule called 4 of
 *  the 8 sane-reading cases "not concentrated" only because they were small). */
export const CONCENTRATION_RATIO = 3;
export function focusFinding(f, radius) {
  const near = f?.near || {};
  const far = f?.elsewhere || {};
  const concentrated = (near.n ?? 0) > 0 && near.max != null && far.max != null && near.max > 0
    && near.max >= CONCENTRATION_RATIO * far.max;
  return {
    key: concentrated ? 'profile_compare.focus_concentrated' : 'profile_compare.focus_descriptive',
    params: { label: f?.label, side: f?.excluded_in === 'a' ? 'A' : 'B', at: _f(f?.de00_at_point), nearN: near.n ?? 0,
              nearMax: _f(near.max), farP95: _f(far.p95), farMax: _f(far.max),
              radius: typeof radius === 'number' ? Math.round(radius * 255) : '—' },
    concentrated,
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
