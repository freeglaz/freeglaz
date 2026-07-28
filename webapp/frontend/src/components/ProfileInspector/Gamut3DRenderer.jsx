import { useEffect, useRef } from 'react';
import * as THREE from 'three';

/**
 * Reusable Three.js 3D rendering.
 *
 * Paradigm: MOVING OBJECT / FIXED CAMERA.
 *
 * - The object (gamut, LUT scatter or gamt) rotates on itself around
 *   the intrinsic center of the nominal Lab cube (a=0, L=50, b=0).
 * - The camera stays fixed in orbit, only used for zoom (Z movement)
 *   and pan (X/Y translation applied to the object).
 * - No more pivot/bbox/target computation that could drift.
 *
 * Scene hierarchy:
 *   scene
 *     ambient + directional lights
 *     objectGroup       ← position (0, 0, 0), mutable rotation/position
 *                          (rotation = intrinsic pivot at world origin)
 *       pivotGroup      ← position (0, -50, 0), static offset so that
 *                          meshes in raw Lab coords (Y=L) reach
 *                          their Lab center (L=50) at world Y=0 = view center
 *         primaryObj    (mesh or Points)
 *         referenceObj  (wireframe mesh)
 *         labLandmarks  (top/bottom cross L=0/L=100 + L gradient axis)
 *
 * Camera at world (0, 0, initialZ) lookAt (0, 0, 0). A Lab vertex L=50
 * a/b=0 reaches world (0, 0, 0) = camera target = viewport center.
 *
 * Disciplined cleanup at unmount:
 *  - cancelAnimationFrame
 *  - dispose geometries / materials / textures
 *  - remove the event listeners (window + canvas)
 *  - renderer.dispose() + forceContextLoss()
 */

// Half-side of the nominal Lab cube for the camera distance computation.
// The Lab cube covers a/b ∈ [-128, 127] (half-side 128) and L ∈ [0, 100]
// (half-side 50). We size for the largest of the three → 130
// with margin, so any gamut fits in the initial framing.
const LAB_CUBE_HALF = 130;
const LAB_CENTER_L = 50;            // L=50 = vertical middle
const ZOOM_MIN = 50;
const ZOOM_MAX = 1500;
const ROTATE_SPEED = 0.005;
const PAN_SPEED = 0.5;
const ZOOM_SPEED = 0.0015;          // % per wheel tick × current Z


export default function Gamut3DRenderer({
  mode = 'marching_cubes',
  primaryMesh = null,
  // profile-compare — backward-compatible multi-mesh extension.
  // `primaryMeshes` (optional): array of objects
  //   { mesh, color, opacity }
  // where `mesh` has the same structure as `primaryMesh` (vertices, indices,
  // [colors_srgb], wireframe_edges, wireframe_style, scatter). When
  // provided, takes precedence over `primaryMesh` and disables vertex colors to
  // favor profile identification by uniform color (qualitative
  // palette on the parent side). `referenceMesh` stays available if we
  // want to keep an sRGB ref overlaid on N profiles.
  // If null/undefined, falls back to historical mono-profile behavior.
  primaryMeshes = null,
  referenceMesh = null,
  showReference = true,
  // renderStyle : "both" (default), "solid", "wireframe"
  renderStyle = 'both',
  autoRotate = false,
  showLabAxes = true,
  width = 800,
  height = 600,
  className,
  resetCounter = 0,
  // Cross-content camera persistence.
  // initialCameraState: { quaternion: [x,y,z,w], position: [x,y,z],
  //                        cameraZ: number } | null
  // If provided at mount, applied after setup. null = default state.
  initialCameraState = null,
  // Callback called at the end of drag/zoom with a snapshot of the
  // camera state for persistence on the parent side.
  onCameraChange = null,
}) {
  const containerRef = useRef(null);
  const sceneRef = useRef(null);
  // Ref to the latest onCameraChange callback to avoid putting
  // the callback in the deps of the main useEffect.
  const onCameraChangeRef = useRef(onCameraChange);
  useEffect(() => { onCameraChangeRef.current = onCameraChange; }, [onCameraChange]);

  useEffect(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;
    const w = container.clientWidth || width;
    const h = container.clientHeight || height;


    // ─── Scene ───
    const scene = new THREE.Scene();
    // LIGHT NEUTRAL viz background — named intention, DELIBERATELY not themed: a
    // constant achromatic gray protects the colorimetric judgment of the gamut
    // (do not route it through theme tokens). The toolbar above, however,
    // is themed. Counterpart of the CSS token --viz-surface (CSS viz zones).
    const VIZ_NEUTRAL_BG = 0xE0E0E0;
    scene.background = new THREE.Color(VIZ_NEUTRAL_BG);

    // ─── Fixed camera ───
    // Camera and origin aligned on (0, 0, 0) instead
    // of (0, 50, 0). The Lab center is brought back to the world origin via
    // the pivotGroup offset below. The camera targets the origin.
    // Distance computed to frame the full nominal Lab cube
    // (half-side 130 to cover a/b∈[-128, 127]) with margin 1.3.
    const camera = new THREE.PerspectiveCamera(35, w / h, 1, 2000);
    const fovRad = (camera.fov * Math.PI) / 180;
    const initialZ = (LAB_CUBE_HALF / Math.tan(fovRad / 2)) * 1.3;
    camera.position.set(0, 0, initialZ);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(w, h, false);
    renderer.domElement.style.display = 'block';
    renderer.domElement.style.width = '100%';
    renderer.domElement.style.height = '100%';
    container.appendChild(renderer.domElement);

    // Lights (useful if MeshStandard later; the current MeshBasic does
    // not use them but they cost nothing)
    scene.add(new THREE.AmbientLight(0xffffff, 0.85));
    const dir = new THREE.DirectionalLight(0xffffff, 0.4);
    dir.position.set(1, 1, 1);
    scene.add(dir);

    // ─── Group hierarchy: intrinsic pivot at the Lab center ───
    // Refactor of the layout: objectGroup at world (0, 0, 0),
    // pivotGroup at local (0, -50, 0) to offset the meshes in raw Lab.
    // Benefit: rotation pivot = world origin → Lab center. The camera
    // targets (0, 0, 0). A vertex at Lab L=50 reaches world Y=0 (view center).
    const objectGroup = new THREE.Group();
    objectGroup.position.set(0, 0, 0);
    scene.add(objectGroup);

    const pivotGroup = new THREE.Group();
    pivotGroup.position.set(0, -LAB_CENTER_L, 0);
    objectGroup.add(pivotGroup);

    // ─── Lab landmarks (children of the pivot → rotate with the object) ───
    const labLandmarks = _buildAxesHelper();
    labLandmarks.visible = showLabAxes;
    pivotGroup.add(labLandmarks);

    // ─── Primary mesh(es) ───
    // profile-compare — primaryMeshes (multi) takes precedence over primaryMesh (mono).
    // Multi: one mesh per entry with dedicated color/opacity (qualitative
    // palette). Mono: historical behavior (vertex colors if available).
    const primaryObjs = [];
    const multi = Array.isArray(primaryMeshes) && primaryMeshes.length > 0;
    if (multi) {
      for (const entry of primaryMeshes) {
        const m = entry?.mesh;
        if (!m) continue;
        if (mode === 'marching_cubes' && m.vertices?.length) {
          primaryObjs.push(_buildMeshMarchingCubes(
            m, renderStyle,
            { overrideColor: entry.color, overrideOpacity: entry.opacity ?? 0.55 },
          ));
        } else if (mode === 'scatter' && m.scatter?.length) {
          primaryObjs.push(_buildScatter(m));
        }
      }
    } else if (mode === 'marching_cubes' && primaryMesh?.vertices?.length) {
      primaryObjs.push(_buildMeshMarchingCubes(primaryMesh, renderStyle));
    } else if (mode === 'scatter' && primaryMesh?.scatter?.length) {
      primaryObjs.push(_buildScatter(primaryMesh));
    }
    for (const obj of primaryObjs) pivotGroup.add(obj);
    // Keep `primaryObj` (singular) for the pivot diag below and
    // the public sceneRef API — first mesh in multi, identical in mono.
    const primaryObj = primaryObjs[0] || null;

    // ─── Reference mesh ───
    let referenceObj = null;
    if (showReference && referenceMesh && referenceMesh.vertices?.length) {
      referenceObj = _buildReferenceMesh(referenceMesh);
      pivotGroup.add(referenceObj);
    }

    // ─── Pivot diag ───
    // One-shot at mount: logs the Lab center of the primary mesh for
    // analysis of the initial centering. If the center is not close to
    // the world origin, it means the mesh is asymmetric and the static
    // pivot (0, -50, 0) does not frame it perfectly.
    objectGroup.updateMatrixWorld(true);
    if (primaryObj) {
      const worldBox = new THREE.Box3().setFromObject(primaryObj);
      const worldCenter = worldBox.getCenter(new THREE.Vector3());
      console.log('[diag pivot]', {
        worldCenter: worldCenter.toArray().map((x) => +x.toFixed(2)),
        bboxMin: worldBox.min.toArray().map((x) => +x.toFixed(2)),
        bboxMax: worldBox.max.toArray().map((x) => +x.toFixed(2)),
        cameraZ: +camera.position.z.toFixed(2),
      });
    }

    // ─── Custom controller: moving object, fixed camera ───
    // Left-click drag → rotation of objectGroup
    // Right-click drag OR Ctrl/Meta+left drag → pan of objectGroup
    // Wheel → camera.position.z
    // Double-click → reset
    let isRotating = false;
    let isPanning = false;
    let lastX = 0, lastY = 0;
    let autoRotateActive = autoRotate;

    const onCanvasContextMenu = (e) => { e.preventDefault(); };
    renderer.domElement.addEventListener('contextmenu', onCanvasContextMenu);

    const onCanvasMouseDown = (e) => {
      autoRotateActive = false;
      const useRightOrModifier = e.button === 2 || e.ctrlKey || e.metaKey;
      if (useRightOrModifier) {
        isPanning = true;
      } else {
        isRotating = true;
      }
      lastX = e.clientX;
      lastY = e.clientY;
      e.preventDefault();
    };
    renderer.domElement.addEventListener('mousedown', onCanvasMouseDown);

    const onWindowMouseMove = (e) => {
      if (!isRotating && !isPanning) return;
      const dx = e.clientX - lastX;
      const dy = e.clientY - lastY;
      lastX = e.clientX;
      lastY = e.clientY;
      if (isRotating) {
        // Yaw around the vertical axis (world Y) — horizontal mouse movement
        const qY = new THREE.Quaternion().setFromAxisAngle(
          new THREE.Vector3(0, 1, 0), dx * ROTATE_SPEED,
        );
        // Pitch around the horizontal axis (camera X) — vertical mouse movement
        const qX = new THREE.Quaternion().setFromAxisAngle(
          new THREE.Vector3(1, 0, 0), dy * ROTATE_SPEED,
        );
        objectGroup.quaternion.premultiply(qY).premultiply(qX);
      } else if (isPanning) {
        // Translation in the screen plane. Z inverted because screen Y goes down.
        objectGroup.position.x += dx * PAN_SPEED;
        objectGroup.position.y -= dy * PAN_SPEED;
      }
    };
    // Camera state snapshot → debounced on mouseup/wheel end
    const _snapshotCamera = () => ({
      quaternion: objectGroup.quaternion.toArray(),
      position: objectGroup.position.toArray(),
      cameraZ: camera.position.z,
    });
    let wheelEndTimer = null;
    const _notifyChange = () => {
      if (onCameraChangeRef.current) {
        onCameraChangeRef.current(_snapshotCamera());
      }
    };

    const onWindowMouseUp = () => {
      const wasInteracting = isRotating || isPanning;
      isRotating = false;
      isPanning = false;
      if (wasInteracting) _notifyChange();
    };
    window.addEventListener('mousemove', onWindowMouseMove);
    window.addEventListener('mouseup', onWindowMouseUp);

    const onCanvasWheel = (e) => {
      e.preventDefault();
      autoRotateActive = false;
      // Zoom proportional to the current distance for a uniform feel
      const delta = e.deltaY * ZOOM_SPEED * camera.position.z;
      const newZ = camera.position.z + delta;
      camera.position.z = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, newZ));
      // Debounce 200 ms: notifies once the burst is over
      if (wheelEndTimer) clearTimeout(wheelEndTimer);
      wheelEndTimer = setTimeout(_notifyChange, 200);
    };
    renderer.domElement.addEventListener('wheel', onCanvasWheel, { passive: false });

    const _resetView = () => {
      // reset aligns with the world origin (the Group's pivot)
      objectGroup.quaternion.identity();
      objectGroup.position.set(0, 0, 0);
      camera.position.set(0, 0, initialZ);
      autoRotateActive = false;
      _notifyChange();
    };

    const onCanvasDblClick = () => { _resetView(); };
    renderer.domElement.addEventListener('dblclick', onCanvasDblClick);

    // ─── Animation loop ───
    let frameId = 0;
    const animate = () => {
      frameId = requestAnimationFrame(animate);
      if (autoRotateActive) {
        // Slow auto-rotation (1 turn ~30 s at 60 fps)
        const qY = new THREE.Quaternion().setFromAxisAngle(
          new THREE.Vector3(0, 1, 0), 0.0035,
        );
        objectGroup.quaternion.premultiply(qY);
      }
      renderer.render(scene, camera);
    };
    animate();

    // ─── Minimal ResizeObserver: follows the container's CSS layout ───
    const ro = new ResizeObserver(() => {
      const newW = container.clientWidth;
      const newH = container.clientHeight;
      if (newW > 0 && newH > 0) {
        camera.aspect = newW / newH;
        camera.updateProjectionMatrix();
        renderer.setSize(newW, newH, false);
      }
    });
    ro.observe(container);

    // ─── Application of the initial camera state ───
    // If the parent provides a snapshot (intra-profile entry change),
    // we restore the orientation/position/zoom instead of the default state.
    if (initialCameraState) {
      const { quaternion: q, position: p, cameraZ: z } = initialCameraState;
      if (Array.isArray(q) && q.length === 4) {
        objectGroup.quaternion.fromArray(q);
      }
      if (Array.isArray(p) && p.length === 3) {
        objectGroup.position.fromArray(p);
      }
      if (typeof z === 'number' && isFinite(z)) {
        camera.position.z = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, z));
      }
    }

    sceneRef.current = {
      scene, renderer, camera, objectGroup, pivotGroup,
      labLandmarks, primaryObj, referenceObj, initialZ, resetView: _resetView,
    };

    return () => {
      cancelAnimationFrame(frameId);
      if (wheelEndTimer) clearTimeout(wheelEndTimer);
      ro.disconnect();
      renderer.domElement.removeEventListener('contextmenu', onCanvasContextMenu);
      renderer.domElement.removeEventListener('mousedown', onCanvasMouseDown);
      renderer.domElement.removeEventListener('wheel', onCanvasWheel);
      renderer.domElement.removeEventListener('dblclick', onCanvasDblClick);
      window.removeEventListener('mousemove', onWindowMouseMove);
      window.removeEventListener('mouseup', onWindowMouseUp);

      for (const obj of primaryObjs) _disposeObject3D(obj);
      _disposeObject3D(referenceObj);
      _disposeObject3D(labLandmarks);
      scene.clear();

      renderer.dispose();
      try { renderer.forceContextLoss(); } catch { /* noop */ }
      if (renderer.domElement.parentNode) {
        renderer.domElement.parentNode.removeChild(renderer.domElement);
      }
      sceneRef.current = null;
    };
  }, [mode, primaryMesh, primaryMeshes, referenceMesh, showReference, renderStyle, autoRotate, showLabAxes, width, height]);

  // Reset from external button (parent does setResetCounter(c+1)).
  // Instant reset, no animation, no multi-click logic.
  useEffect(() => {
    if (resetCounter === 0 || !sceneRef.current) return;
    sceneRef.current.resetView?.();
  }, [resetCounter]);

  return (
    <div
      ref={containerRef}
      className={className || 'w-full h-full'}
      style={{ width: '100%', height: '100%' }}
      aria-label="3D gamut renderer"/>
  );
}


// ─── Construction helpers ───

function _buildMeshMarchingCubes(mesh, renderStyle, opts = {}) {
  // opts (profile-compare):
  //   overrideColor    — hex/string (eg. "#E69F00"): if provided, ignores the
  //                      vertex colors and applies a uniform color
  //                      to the solid AND the wireframe (multi-profile mode).
  //                      If absent and colors_srgb available → vertex colors
  //                      (rendered painted with real Lab colors, as in
  //                      mono-profile mode = "reference" profile in
  //                      the compare modal).
  //   overrideOpacity  — float ∈ ]0,1]: opacity of the solid mesh. By
  //                      default 1 (opaque, historical behavior).
  //                      In multi-mesh, the parent passes ~0.45-0.55 to
  //                      see the wireframes passing through the solid.
  //   renderStyle      — "both"|"solid"|"wireframe" per-mesh, overrides the
  //                      global parameter. Allows in multi-profile to have
  //                      a reference in solid and the others in
  //                      wireframe without changing the renderer's global mode.
  const { vertices, indices, colors_srgb } = mesh;
  const useOverrideColor = typeof opts.overrideColor === 'string';
  const overrideOpacity = typeof opts.overrideOpacity === 'number'
    ? opts.overrideOpacity
    : 1.0;
  // Effective style: opts.renderStyle takes precedence over the global param.
  const effectiveRenderStyle = typeof opts.renderStyle === 'string'
    ? opts.renderStyle
    : renderStyle;
  const geom = new THREE.BufferGeometry();
  const positions = new Float32Array(vertices.length * 3);
  for (let i = 0; i < vertices.length; i++) {
    // Scene convention: X = a*, Y = L*, Z = b*
    positions[i * 3] = vertices[i][1];
    positions[i * 3 + 1] = vertices[i][0];
    positions[i * 3 + 2] = vertices[i][2];
  }
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));

  // Vertex colors: only in mono-profile (override = uniform).
  if (!useOverrideColor && colors_srgb && colors_srgb.length === vertices.length) {
    const colors = new Float32Array(colors_srgb.length * 3);
    for (let i = 0; i < colors_srgb.length; i++) {
      colors[i * 3] = colors_srgb[i][0];
      colors[i * 3 + 1] = colors_srgb[i][1];
      colors[i * 3 + 2] = colors_srgb[i][2];
    }
    geom.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  }

  const idx = new Uint32Array(indices.length * 3);
  for (let i = 0; i < indices.length; i++) {
    idx[i * 3] = indices[i][0];
    idx[i * 3 + 1] = indices[i][1];
    idx[i * 3 + 2] = indices[i][2];
  }
  geom.setIndex(new THREE.BufferAttribute(idx, 1));
  geom.computeVertexNormals();

  // Group: solid mesh + overlaid wireframe mesh.
  const group = new THREE.Group();
  // opaque: per-fragment depthTest/depthWrite become
  // fully active again. On the device_surface sheet that folds near
  // max chroma (yellow, L≈80/b≈80), transparency at 0.92 without
  // intra-mesh sorting produced a jagged Z fringe. DoubleSide kept
  // (mesh not closed at the fold).
  // In multi-profile: opacity < 1 needed to see the overlaps
  // between volumes — the readability tradeoff prevails over the z-order artifact.
  const solidMat = useOverrideColor
    ? new THREE.MeshBasicMaterial({
        color: new THREE.Color(opts.overrideColor),
        side: THREE.DoubleSide,
        transparent: overrideOpacity < 1.0,
        opacity: overrideOpacity,
        depthWrite: overrideOpacity >= 1.0,
      })
    : new THREE.MeshBasicMaterial({
        vertexColors: true,
        side: THREE.DoubleSide,
      });
  const solidMesh = new THREE.Mesh(geom, solidMat);
  solidMesh.visible = effectiveRenderStyle !== 'wireframe';
  group.add(solidMesh);

  // Wireframe — two modes depending on the extraction method:
  // - cage_*: LineSegments from wireframe_edges (few edges,
  //            high readability, ColorSync-like). Vertex colors when
  //            in "Wireframe only" mode to preserve the info.
  // - triangular: old wireframe mode on the indexed geometry.
  // In multi-profile with overrideColor: the wireframe is also painted
  // in the identity color (qualitative palette), not gray nor
  // vertex colors — the line color IS the profile identifier.
  const wfEdges = mesh.wireframe_edges;
  let wfStyle = mesh.wireframe_style;
  if (!wfStyle) {
    console.warn('[gamut renderer] wireframe_style missing from backend mesh — '
                 + 'fallback "triangular". extraction_method:',
                 mesh.extraction_method);
    wfStyle = 'triangular';
  }
  let wireMesh;
  if (wfEdges && wfEdges.length > 0 && wfStyle !== 'triangular') {
    wireMesh = _buildCageWireframe(
      vertices, wfEdges, colors_srgb, effectiveRenderStyle,
      { overrideColor: opts.overrideColor },
    );
  } else {
    let wireMat;
    if (useOverrideColor) {
      // Multi-profile identity: uniform line in the profile color,
      // higher opacity in wireframe-only mode (the wireframe IS the
      // main representation), reduced when the solid of a
      // reference sits next to it.
      wireMat = new THREE.MeshBasicMaterial({
        color: new THREE.Color(opts.overrideColor),
        wireframe: true,
        transparent: true,
        opacity: effectiveRenderStyle === 'wireframe' ? 0.9 : 0.6,
      });
    } else if (effectiveRenderStyle === 'wireframe') {
      wireMat = new THREE.MeshBasicMaterial({
        vertexColors: true,
        wireframe: true,
        transparent: true,
        opacity: 0.9,
      });
    } else {
      wireMat = new THREE.MeshBasicMaterial({
        color: 0x333333,
        wireframe: true,
        transparent: true,
        opacity: 0.4,
      });
    }
    wireMesh = new THREE.Mesh(geom, wireMat);
  }
  wireMesh.visible = effectiveRenderStyle !== 'solid';
  group.add(wireMesh);

  return group;
}


/** Wireframe cage: LineSegments from a list of edges [i, j].
 *
 * - opts.overrideColor provided (multi-profile) → uniform line in that
 *   identity color, opacity 0.9; no dependency on colors_srgb.
 * - renderStyle="wireframe" without override → vertex colors preserved
 *   (mono-profile wireframe-only mode, opacity 0.85).
 * - otherwise → neutral gray #333333 op. 0.45 (overlaid wireframe). */
function _buildCageWireframe(vertices, edges, colors_srgb, renderStyle, opts = {}) {
  const positions = new Float32Array(edges.length * 2 * 3);
  const useOverrideColor = typeof opts.overrideColor === 'string';
  const useVertexColors = !useOverrideColor
    && renderStyle === 'wireframe'
    && colors_srgb && colors_srgb.length === vertices.length;
  const colors = useVertexColors
    ? new Float32Array(edges.length * 2 * 3)
    : null;
  for (let k = 0; k < edges.length; k++) {
    const [i, j] = edges[k];
    // Scene convention: X = a*, Y = L*, Z = b*
    positions[k * 6]     = vertices[i][1];
    positions[k * 6 + 1] = vertices[i][0];
    positions[k * 6 + 2] = vertices[i][2];
    positions[k * 6 + 3] = vertices[j][1];
    positions[k * 6 + 4] = vertices[j][0];
    positions[k * 6 + 5] = vertices[j][2];
    if (useVertexColors) {
      colors[k * 6]     = colors_srgb[i][0];
      colors[k * 6 + 1] = colors_srgb[i][1];
      colors[k * 6 + 2] = colors_srgb[i][2];
      colors[k * 6 + 3] = colors_srgb[j][0];
      colors[k * 6 + 4] = colors_srgb[j][1];
      colors[k * 6 + 5] = colors_srgb[j][2];
    }
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  if (useVertexColors) {
    geom.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  }
  let mat;
  if (useOverrideColor) {
    mat = new THREE.LineBasicMaterial({
      color: new THREE.Color(opts.overrideColor),
      transparent: true,
      opacity: 0.9,
    });
  } else if (useVertexColors) {
    mat = new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: 0.85,
    });
  } else {
    mat = new THREE.LineBasicMaterial({
      color: 0x333333, transparent: true, opacity: 0.45,
    });
  }
  return new THREE.LineSegments(geom, mat);
}


function _buildReferenceMesh(mesh) {
  const { vertices, indices } = mesh;
  const geom = new THREE.BufferGeometry();
  const positions = new Float32Array(vertices.length * 3);
  for (let i = 0; i < vertices.length; i++) {
    positions[i * 3] = vertices[i][1];
    positions[i * 3 + 1] = vertices[i][0];
    positions[i * 3 + 2] = vertices[i][2];
  }
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  const idx = new Uint32Array(indices.length * 3);
  for (let i = 0; i < indices.length; i++) {
    idx[i * 3] = indices[i][0];
    idx[i * 3 + 1] = indices[i][1];
    idx[i * 3 + 2] = indices[i][2];
  }
  geom.setIndex(new THREE.BufferAttribute(idx, 1));
  const mat = new THREE.MeshBasicMaterial({
    color: 0x4080FF,
    wireframe: true,
    transparent: true,
    opacity: 0.55,
  });
  return new THREE.Mesh(geom, mat);
}


function _buildScatter(mesh) {
  const { scatter } = mesh;
  const geom = new THREE.BufferGeometry();
  const positions = new Float32Array(scatter.length * 3);
  const colors = new Float32Array(scatter.length * 3);
  for (let i = 0; i < scatter.length; i++) {
    const [L, a, b, r, g, bb] = scatter[i];
    positions[i * 3] = a;
    positions[i * 3 + 1] = L;
    positions[i * 3 + 2] = b;
    colors[i * 3] = r;
    colors[i * 3 + 1] = g;
    colors[i * 3 + 2] = bb;
  }
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geom.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  const mat = new THREE.PointsMaterial({
    size: 4.5,
    vertexColors: true,
    sizeAttenuation: true,
  });
  return new THREE.Points(geom, mat);
}


/**
 * Lab landmarks:
 *
 * - Borders of the nominal Lab cube (#B0B0B0 op. 0.4) — spatial context
 *   essential to situate the gamut, restored after their
 *   removal had made the mesh visually lost.
 * - Top cross (L=100) white op. 0.85, length 80
 * - Bottom cross (L=0) #1A1A1A op. 0.85, length 80
 * - Vertical L axis with black → white gradient (21 segments)
 *
 * Still removed: gradation crosses L=25/L=50/L=75.
 */
function _buildAxesHelper() {
  const group = new THREE.Group();
  group.userData.role = 'lab-axes';

  // Nominal Lab cube borders — spatial context for the gamut.
  // Cube 255×100×255 translated (0, 50, 0): covers a/b∈[-128,127] and L∈[0,100].
  const cubeEdges = new THREE.EdgesGeometry(
    new THREE.BoxGeometry(255, 100, 255).translate(0, 50, 0),
  );
  group.add(new THREE.LineSegments(
    cubeEdges,
    new THREE.LineBasicMaterial({ color: 0xB0B0B0, opacity: 0.4, transparent: true }),
  ));

  const _makeCross = (L, length, color, opacity) => {
    const geom = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-length, L, 0), new THREE.Vector3(length, L, 0),
      new THREE.Vector3(0, L, -length), new THREE.Vector3(0, L, length),
    ]);
    return new THREE.LineSegments(
      geom,
      new THREE.LineBasicMaterial({ color, opacity, transparent: true }),
    );
  };

  // Top cross (white, L=100) and bottom (#1A1A1A, L=0)
  group.add(_makeCross(100, 80, 0xFFFFFF, 0.85));
  group.add(_makeCross(0, 80, 0x1A1A1A, 0.85));

  // Vertical L axis with black → white gradient
  const N_AXIS_SEG = 20;
  const axisPositions = new Float32Array((N_AXIS_SEG + 1) * 3);
  const axisColors = new Float32Array((N_AXIS_SEG + 1) * 3);
  for (let i = 0; i <= N_AXIS_SEG; i++) {
    axisPositions[i * 3] = 0;
    axisPositions[i * 3 + 1] = (i / N_AXIS_SEG) * 100;
    axisPositions[i * 3 + 2] = 0;
    const v = i / N_AXIS_SEG;
    axisColors[i * 3] = v;
    axisColors[i * 3 + 1] = v;
    axisColors[i * 3 + 2] = v;
  }
  const axisGeom = new THREE.BufferGeometry();
  axisGeom.setAttribute('position', new THREE.BufferAttribute(axisPositions, 3));
  axisGeom.setAttribute('color', new THREE.BufferAttribute(axisColors, 3));
  const lAxis = new THREE.Line(
    axisGeom,
    new THREE.LineBasicMaterial({ vertexColors: true, linewidth: 1 }),
  );
  group.add(lAxis);

  return group;
}


function _disposeObject3D(obj) {
  if (!obj) return;
  const disposedGeoms = new Set();
  obj.traverse((child) => {
    if (child.geometry && !disposedGeoms.has(child.geometry.uuid)) {
      child.geometry.dispose();
      disposedGeoms.add(child.geometry.uuid);
    }
    const mats = Array.isArray(child.material) ? child.material : [child.material];
    for (const m of mats) {
      if (!m) continue;
      if (m.map) m.map.dispose();
      m.dispose();
    }
  });
}
