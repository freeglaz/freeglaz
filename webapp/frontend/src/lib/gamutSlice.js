/**
 * Slice of a triangulated mesh by a plane L=constant.
 *
 * Pure JS, no React, no dependency. Computed client-side from
 * the mesh already fetched by useGamutMesh. The L slider re-slices on
 * every tick → interactive without network.
 *
 * Convention: `vertices[i] = [L, a, b]` (consistent with the backend
 * device_surface_grid).
 */


/**
 * Slices the triangulated mesh at the plane L = L0 and returns the
 * intersection segments in the a*b* plane.
 *
 * @param {Array<Array<number>>} vertices  List of [L, a, b]
 * @param {Array<Array<number>>} indices   List of [i, j, k] triangles
 * @param {number} L0                      L slice level
 * @param {Array<Array<number>>} [colors_srgb]  Optional: [r, g, b] ∈ [0,1]
 *   per vertex. If provided, each segment carries ``colors: [[r,g,b],[r,g,b]]``
 *   interpolated with the same t parameter as the position.
 * @returns {{
 *   segments: Array<Array<number>>,  // [a0, b0, a1, b1]
 *   colors: Array<Array<Array<number>>>|null,  // [[r0,g0,b0],[r1,g1,b1]] per segment
 *   area: number|null,               // a*b* area if chainable into a simple loop
 *   bounds_ab: { aMin, aMax, bMin, bMax }|null,
 * }}
 */
export function sliceMeshAtL(vertices, indices, L0, colors_srgb) {
  const segments = [];
  const colors = colors_srgb ? [] : null;
  for (let t = 0; t < indices.length; t++) {
    const [i, j, k] = indices[t];
    const v0 = vertices[i];
    const v1 = vertices[j];
    const v2 = vertices[k];
    const c0 = colors_srgb ? colors_srgb[i] : null;
    const c1 = colors_srgb ? colors_srgb[j] : null;
    const c2 = colors_srgb ? colors_srgb[k] : null;
    // Signed distance from plane (positive = above L0)
    const d0 = v0[0] - L0;
    const d1 = v1[0] - L0;
    const d2 = v2[0] - L0;
    const s0 = d0 > 0 ? 1 : (d0 < 0 ? -1 : 0);
    const s1 = d1 > 0 ? 1 : (d1 < 0 ? -1 : 0);
    const s2 = d2 > 0 ? 1 : (d2 < 0 ? -1 : 0);

    // All on the same side (none zero) → triangle does not cross
    if (s0 > 0 && s1 > 0 && s2 > 0) continue;
    if (s0 < 0 && s1 < 0 && s2 < 0) continue;
    // All exactly on plane → degenerate triangle (rare in practice), skip
    if (s0 === 0 && s1 === 0 && s2 === 0) continue;

    // Special case: exactly 2 vertices on the plane → the edge between
    // these 2 vertices IS the slice segment.
    const zeros = (s0 === 0 ? 1 : 0) + (s1 === 0 ? 1 : 0) + (s2 === 0 ? 1 : 0);
    if (zeros === 2) {
      let pa, pb, ca, cb;
      if (s0 === 0 && s1 === 0) { pa = v0; pb = v1; ca = c0; cb = c1; }
      else if (s1 === 0 && s2 === 0) { pa = v1; pb = v2; ca = c1; cb = c2; }
      else { pa = v2; pb = v0; ca = c2; cb = c0; }
      segments.push([pa[1], pa[2], pb[1], pb[2]]);
      if (colors) colors.push([ca || [0.5, 0.5, 0.5], cb || [0.5, 0.5, 0.5]]);
      continue;
    }

    // Compute up to 3 intersection points along the edges
    const inter = [];
    _addEdgeIntersection(inter, v0, v1, d0, d1, s0, s1, c0, c1);
    _addEdgeIntersection(inter, v1, v2, d1, d2, s1, s2, c1, c2);
    _addEdgeIntersection(inter, v2, v0, d2, d0, s2, s0, c2, c0);

    // Deduplicate (a vertex on the plane shared by two edges produces 2 hits)
    const distinct = _dedupePoints(inter);
    if (distinct.length === 2) {
      const [a0, b0, r0, g0, bl0] = distinct[0];
      const [a1, b1, r1, g1, bl1] = distinct[1];
      segments.push([a0, b0, a1, b1]);
      if (colors) {
        colors.push([
          [r0 ?? 0.5, g0 ?? 0.5, bl0 ?? 0.5],
          [r1 ?? 0.5, g1 ?? 0.5, bl1 ?? 0.5],
        ]);
      }
    }
    // 0 or 1 point → triangle touches the plane tangentially, skip
  }

  const bounds_ab = _computeBounds(segments);
  const area = _tryComputeLoopArea(segments);

  return { segments, colors, area, bounds_ab };
}


function _addEdgeIntersection(out, pa, pb, da, db, sa, sb, ca, cb) {
  if (sa * sb < 0) {
    // Proper crossing — linear interpolation in t (position + color)
    const t = da / (da - db);
    const pt = [pa[1] + t * (pb[1] - pa[1]), pa[2] + t * (pb[2] - pa[2])];
    if (ca && cb) {
      pt.push(
        ca[0] + t * (cb[0] - ca[0]),
        ca[1] + t * (cb[1] - ca[1]),
        ca[2] + t * (cb[2] - ca[2]),
      );
    }
    out.push(pt);
  } else if (sa === 0 && sb !== 0) {
    // Vertex a exactly on plane
    const pt = [pa[1], pa[2]];
    if (ca) pt.push(ca[0], ca[1], ca[2]);
    out.push(pt);
  }
  // sb === 0 case: handled by next edge starting at pb
}


function _dedupePoints(pts) {
  const eps = 1e-9;
  const out = [];
  for (const p of pts) {
    let dup = false;
    for (const q of out) {
      if (Math.abs(q[0] - p[0]) < eps && Math.abs(q[1] - p[1]) < eps) {
        dup = true;
        break;
      }
    }
    if (!dup) out.push(p);
  }
  return out;
}


function _computeBounds(segments) {
  if (segments.length === 0) return null;
  let aMin = Infinity, aMax = -Infinity, bMin = Infinity, bMax = -Infinity;
  for (const [a0, b0, a1, b1] of segments) {
    if (a0 < aMin) aMin = a0;
    if (a0 > aMax) aMax = a0;
    if (a1 < aMin) aMin = a1;
    if (a1 > aMax) aMax = a1;
    if (b0 < bMin) bMin = b0;
    if (b0 > bMax) bMax = b0;
    if (b1 < bMin) bMin = b1;
    if (b1 > bMax) bMax = b1;
  }
  return { aMin, aMax, bMin, bMax };
}


/**
 * Attempts to chain the segments into a simple closed loop and to
 * compute the area via the shoelace formula.
 *
 * Returns null if the topology is non-trivial (several loops,
 * endpoints with degree ≠ 2, orphan segments). In practice for
 * device_surface_grid, a slice at L inside the gamut produces
 * ONE closed loop → v1 case supported. Near the folds (yellow
 * fold), the slice may produce self-intersections → null.
 */
function _tryComputeLoopArea(segments) {
  if (segments.length < 3) return null;

  // Quantize endpoints to merge the near-coincidences
  const quant = 1e6;
  const key = (a, b) => `${Math.round(a * quant)},${Math.round(b * quant)}`;

  // adj : map(endpointKey → [{ otherKey, otherPoint, segIdx }])
  const adj = new Map();
  for (let i = 0; i < segments.length; i++) {
    const [a0, b0, a1, b1] = segments[i];
    const k0 = key(a0, b0), k1 = key(a1, b1);
    if (k0 === k1) return null; // degenerate segment
    if (!adj.has(k0)) adj.set(k0, []);
    if (!adj.has(k1)) adj.set(k1, []);
    adj.get(k0).push({ other: k1, point: [a1, b1], segIdx: i });
    adj.get(k1).push({ other: k0, point: [a0, b0], segIdx: i });
  }

  // Each endpoint must have exactly 2 incident segments
  for (const list of adj.values()) {
    if (list.length !== 2) return null;
  }

  // Walk: start at any endpoint, follow the chain.
  // Seed: arbitrarily take the first incident segment to
  // initialize prevKey (otherwise the filter `n.other !== prevKey` returns
  // 2 candidates on the first round and the walk aborts).
  const visited = new Set();
  const startKey = adj.keys().next().value;
  const startPoint = startKey.split(',').map((x) => Number(x) / quant);
  const loop = [startPoint];

  const firstEdge = adj.get(startKey)[0];
  visited.add(firstEdge.segIdx);
  if (firstEdge.other === startKey) return null; // degenerate segment
  let prevKey = startKey;
  let currentKey = firstEdge.other;
  loop.push(firstEdge.point);

  while (visited.size < segments.length) {
    const candidates = adj.get(currentKey).filter((n) => n.other !== prevKey);
    if (candidates.length !== 1) return null; // junction or dead-end
    const next = candidates[0];
    if (visited.has(next.segIdx)) {
      // Already seen: should be the segment that closes the loop
      if (next.other === startKey) break;
      return null;
    }
    visited.add(next.segIdx);
    if (next.other === startKey) break; // closed loop
    loop.push(next.point);
    prevKey = currentKey;
    currentKey = next.other;
  }

  if (visited.size !== segments.length) return null; // several components

  // Shoelace
  let area2 = 0;
  for (let i = 0; i < loop.length; i++) {
    const [x0, y0] = loop[i];
    const [x1, y1] = loop[(i + 1) % loop.length];
    area2 += x0 * y1 - x1 * y0;
  }
  return Math.abs(area2) / 2;
}
