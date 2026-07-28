/**
 * Unit tests for sliceMeshAtL — synthetic geometry verifiable
 * analytically.
 *
 * Run: node webapp/frontend/src/lib/gamutSlice.test.mjs
 */
import { sliceMeshAtL } from './gamutSlice.js';

let pass = 0, fail = 0;

function assert(cond, msg) {
  if (cond) {
    pass++;
    console.log(`  ✓ ${msg}`);
  } else {
    fail++;
    console.error(`  ✗ ${msg}`);
  }
}

function approxEq(a, b, eps = 1e-6) {
  return Math.abs(a - b) < eps;
}

// ─── Test 1: tetrahedron point down ─────────────────────────────────
// v0=(L=0, a=0, b=0)
// v1=(L=1, a=1, b=0)
// v2=(L=1, a=0, b=1)
// v3=(L=1, a=0, b=0)
// 4 triangles. Slice at L=0.5 → a*b* triangle (0,0)/(0.5,0)/(0,0.5), area 0.125.
console.log('Test 1 — tetrahedron, L=0.5');
{
  const verts = [
    [0, 0, 0],
    [1, 1, 0],
    [1, 0, 1],
    [1, 0, 0],
  ];
  const tris = [
    [0, 1, 2],
    [0, 2, 3],
    [0, 3, 1],
    [1, 2, 3], // sommet haut, ne traverse pas
  ];
  const res = sliceMeshAtL(verts, tris, 0.5);
  assert(res.segments.length === 3, `3 segments (got ${res.segments.length})`);
  assert(res.bounds_ab !== null, 'bounds_ab calculé');
  if (res.bounds_ab) {
    assert(approxEq(res.bounds_ab.aMin, 0), `aMin=0 (got ${res.bounds_ab.aMin})`);
    assert(approxEq(res.bounds_ab.aMax, 0.5), `aMax=0.5 (got ${res.bounds_ab.aMax})`);
    assert(approxEq(res.bounds_ab.bMin, 0), `bMin=0 (got ${res.bounds_ab.bMin})`);
    assert(approxEq(res.bounds_ab.bMax, 0.5), `bMax=0.5 (got ${res.bounds_ab.bMax})`);
  }
  assert(res.area !== null, 'aire calculée (loop fermé)');
  if (res.area !== null) {
    assert(approxEq(res.area, 0.125), `aire=0.125 (got ${res.area})`);
  }
}

// ─── Test 2: tetrahedron, slice out of range ─────────────────────────────
console.log('\nTest 2 — tetrahedron, L=2 hors plage');
{
  const verts = [
    [0, 0, 0], [1, 1, 0], [1, 0, 1], [1, 0, 0],
  ];
  const tris = [
    [0, 1, 2], [0, 2, 3], [0, 3, 1], [1, 2, 3],
  ];
  const res = sliceMeshAtL(verts, tris, 2);
  assert(res.segments.length === 0, `0 segments (got ${res.segments.length})`);
  assert(res.area === null, 'area null sur coupe vide');
  assert(res.bounds_ab === null, 'bounds_ab null sur coupe vide');
}

// ─── Test 3: closed cube ──────────────────────────────────────────────
// 8 unit vertices, 12 triangles (2 per face, 6 faces).
// Slice at L=0.5 → a*b* square of side 1, area 1.
console.log('\nTest 3 — cube unitaire (L, a, b) ∈ [0,1]³, L=0.5');
{
  // Indices v0..v7 = binary combination LSB=L: (L=i&1, a=(i>>1)&1, b=(i>>2)&1)
  // v0=[0,0,0] v1=[1,0,0] v2=[0,1,0] v3=[1,1,0]
  // v4=[0,0,1] v5=[1,0,1] v6=[0,1,1] v7=[1,1,1]
  const verts = [
    [0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0],
    [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1],
  ];
  // 6 faces × 2 triangles = 12 triangles, consistent winding (doesn't matter
  // for the slicing, just to have a clean closed cube).
  const tris = [
    // L=0 face (v0, v2, v6, v4)
    [0, 2, 6], [0, 6, 4],
    // L=1 face (v1, v5, v7, v3)
    [1, 5, 7], [1, 7, 3],
    // a=0 face (v0, v4, v5, v1)
    [0, 4, 5], [0, 5, 1],
    // a=1 face (v2, v3, v7, v6)
    [2, 3, 7], [2, 7, 6],
    // b=0 face (v0, v1, v3, v2)
    [0, 1, 3], [0, 3, 2],
    // b=1 face (v4, v6, v7, v5)
    [4, 6, 7], [4, 7, 5],
  ];
  const res = sliceMeshAtL(verts, tris, 0.5);
  // 4 faces cross the plane (a=0, a=1, b=0, b=1) × 2 triangles → 8 segments.
  assert(res.segments.length === 8, `8 segments (got ${res.segments.length})`);
  assert(res.bounds_ab !== null, 'bounds_ab calculé');
  if (res.bounds_ab) {
    assert(approxEq(res.bounds_ab.aMin, 0), `aMin=0`);
    assert(approxEq(res.bounds_ab.aMax, 1), `aMax=1`);
    assert(approxEq(res.bounds_ab.bMin, 0), `bMin=0`);
    assert(approxEq(res.bounds_ab.bMax, 1), `bMax=1`);
  }
  // 8 segments chainable into 1 closed loop (unit square), area = 1.
  assert(res.area !== null, 'aire calculée');
  if (res.area !== null) {
    assert(approxEq(res.area, 1, 1e-3), `aire=1 (got ${res.area})`);
  }
}

// ─── Test 4: slice exactly on the low point of the tetrahedron ─────────────
console.log('\nTest 4 — tetrahedron, L=0 (sur la pointe v0)');
{
  const verts = [
    [0, 0, 0], [1, 1, 0], [1, 0, 1], [1, 0, 0],
  ];
  const tris = [
    [0, 1, 2], [0, 2, 3], [0, 3, 1], [1, 2, 3],
  ];
  const res = sliceMeshAtL(verts, tris, 0);
  // The 3 triangles incident to v0 have v0 on the plane, the 2 other
  // vertices above → a single intersection point each → no
  // segment. Triangle (1,2,3) entirely above → skip.
  // Result: 0 segments (tangent, no proper slice).
  assert(res.segments.length === 0, `0 segments (tangence, got ${res.segments.length})`);
}

// ─── Test 5: slice crossing exactly a horizontal edge ─────────────
// v0=(L=1, a=0, b=0), v1=(L=1, a=1, b=0), v2=(L=0, a=0.5, b=1)
// Single triangle. Slice L=1: edge (v0, v1) entirely on the plane.
console.log('\nTest 5 — single triangle, edge on plane (L=1)');
{
  const verts = [
    [1, 0, 0], [1, 1, 0], [0, 0.5, 1],
  ];
  const tris = [[0, 1, 2]];
  const res = sliceMeshAtL(verts, tris, 1);
  // s0=0, s1=0, s2=-1. Edge v0-v1: sa=sb=0, skip.
  // Edge v1-v2: sa=0, sb=-1 → add v1=(1,0).
  // Edge v2-v0: sa=-1, sb=0 → add v0=(0,0).
  // Dedupe: 2 distinct points → 1 segment (v0→v1) = edge on the plane.
  assert(res.segments.length === 1, `1 segment arête sur plan (got ${res.segments.length})`);
}

// ─── Test 6: color interpolation along a crossed edge ─────
// Triangle with sRGB colors per vertex: we check that the color
// at the mid-edge intersection point is indeed the average of the colors
// of the two endpoints.
console.log('\nTest 6 — color interpolation along crossed edges');
{
  const verts = [
    [0, 0, 0],   // v0: L=0
    [1, 1, 0],   // v1: L=1
    [1, 0, 1],   // v2: L=1
  ];
  const colors_srgb = [
    [1.0, 0.0, 0.0],   // v0: pure red
    [0.0, 1.0, 0.0],   // v1: pure green
    [0.0, 0.0, 1.0],   // v2: pure blue
  ];
  const tris = [[0, 1, 2]];
  const res = sliceMeshAtL(verts, tris, 0.5, colors_srgb);
  assert(res.segments.length === 1, `1 segment (got ${res.segments.length})`);
  assert(Array.isArray(res.colors), 'colors output array présent');
  assert(res.colors.length === 1, `1 entry colors (got ${res.colors?.length})`);
  if (res.colors && res.colors.length === 1) {
    const [c_start, c_end] = res.colors[0];
    // Edge v0→v1 crossed at t=0.5: color = (red + green) / 2 = (0.5, 0.5, 0)
    // Edge v0→v2 crossed at t=0.5: color = (red + blue) / 2 = (0.5, 0, 0.5)
    // (the order depends on the edge iteration — we allow both)
    const exp_a = [0.5, 0.5, 0];
    const exp_b = [0.5, 0, 0.5];
    const okOrder1 =
      approxEq(c_start[0], exp_a[0]) && approxEq(c_start[1], exp_a[1]) && approxEq(c_start[2], exp_a[2]) &&
      approxEq(c_end[0],   exp_b[0]) && approxEq(c_end[1],   exp_b[1]) && approxEq(c_end[2],   exp_b[2]);
    const okOrder2 =
      approxEq(c_start[0], exp_b[0]) && approxEq(c_start[1], exp_b[1]) && approxEq(c_start[2], exp_b[2]) &&
      approxEq(c_end[0],   exp_a[0]) && approxEq(c_end[1],   exp_a[1]) && approxEq(c_end[2],   exp_a[2]);
    assert(okOrder1 || okOrder2,
      `couleurs interpolées (0.5,0.5,0) et (0.5,0,0.5), got ${JSON.stringify(c_start)} ${JSON.stringify(c_end)}`);
  }
}

// ─── Test 7: no colors_srgb → output colors=null (back-compat) ────
console.log('\nTest 7 — colors output null when colors_srgb omitted (back-compat)');
{
  const verts = [[0, 0, 0], [1, 1, 0], [1, 0, 1], [1, 0, 0]];
  const tris = [[0, 1, 2], [0, 2, 3], [0, 3, 1], [1, 2, 3]];
  const res = sliceMeshAtL(verts, tris, 0.5);
  assert(res.colors === null, `colors === null (got ${res.colors})`);
  assert(res.segments.length === 3, `signature inchangée (3 segments)`);
}

// ─── Summary ────────────────────────────────────────────────────────────
console.log(`\n──── ${pass} pass / ${fail} fail ────`);
if (fail > 0) process.exit(1);
