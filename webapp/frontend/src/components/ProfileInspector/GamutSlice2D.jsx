import { useEffect, useMemo, useRef, useState } from 'react';
import { paintLabChromaBackground } from '../../lib/labChromaBackground.js';

/**
 * SVG plot of an a*b* slice at constant L.
 *
 * - a* axes (horizontal, right = +a) and b* (vertical, up
 *   = +b), neutral origin (a=0, b=0) at center.
 * - isometric scale: a chroma circle stays round.
 * - `meshes`: array of mesh with fields:
 *     - `segments`: Array of [a0, b0, a1, b1]
 *     - `colors`:   Array of [[r0,g0,b0], [r1,g1,b1]] (optional) — if
 *                   provided, each segment is drawn as an sRGB gradient
 *                   (1-contour mode). Otherwise, solid color from `color`.
 *     - `color`:    fallback color (multi-contour / reference mode)
 *     - `dashed`:   bool
 *     - `strokeWidth`, `opacity`: optional
 * - hover → tooltip (a*, b*, C*, h°).
 *
 * Grid/axes: neutral tokens `--border-soft` / `--border-strong` /
 * `--text-faint` to recede visually.
 *
 * profile-compare — optional `backgroundLstar` prop:
 * if provided (number 0..100), a canvas behind the SVG is
 * painted with the Lab chromaticity map → screen at L*=backgroundLstar.
 * Pixels out of the reference space's gamut → transparent (no
 * gray background). Default null = no background, historical
 * mono-profile behavior intact.
 *
 * profile-compare — optional `backgroundSpace` prop: reference
 * space of the background ∈ REFERENCE_SPACES (sRGB / AdobeRGB / P3 /
 * Rec2020 / ProPhoto). Default `DEFAULT_REFERENCE_SPACE` = AdobeRGB.
 * Changing this prop repaints the canvas (space added as a dependency
 * of the repaint useEffect).
 */
export default function GamutSlice2D({
  meshes = [],
  domain = null,
  size = 480,
  backgroundLstar = null,
  backgroundAlpha = 0.65,
  backgroundSpace = null,
}) {
  const padding = 40;
  const plotSize = size - 2 * padding;
  const containerRef = useRef(null);
  const bgCanvasRef = useRef(null);
  const bgRafRef = useRef(0);
  const [hover, setHover] = useState(null);
  // Internal resolution of the background canvas. The old 256² was too low
  // (upscale ~2-2.4× on large screen / compare → blur + jagged edges
  // under the contours). We target the device resolution of the useful area
  // (plotSize × devicePixelRatio), capped to bound the cost of the paint
  // recomputed at each tick of the L* slider.
  const BG_RES_CAP = 512;
  const _dpr = typeof window !== 'undefined' ? (window.devicePixelRatio || 1) : 1;
  const BG_RES = Math.max(256, Math.min(BG_RES_CAP, Math.round(plotSize * Math.min(_dpr, 2))));

  // Domain: union of bounds + margin, extended to stay square.
  const range = useMemo(() => {
    if (domain) return domain;
    let lo = -50, hi = 50;
    let any = false;
    for (const m of meshes) {
      for (const seg of m.segments) {
        const [a0, b0, a1, b1] = seg;
        if (!any) { lo = a0; hi = a0; any = true; }
        lo = Math.min(lo, a0, a1, b0, b1);
        hi = Math.max(hi, a0, a1, b0, b1);
      }
    }
    const half = Math.max(Math.abs(lo), Math.abs(hi)) + 8;
    return { aMin: -half, aMax: half, bMin: -half, bMax: half };
  }, [meshes, domain]);

  const xScale = (a) => padding + ((a - range.aMin) / (range.aMax - range.aMin)) * plotSize;
  const yScale = (b) => padding + ((range.bMax - b) / (range.bMax - range.bMin)) * plotSize;

  // Repaint the swatch background when L*, domain or reference
  // space change. No-op if backgroundLstar=null (inspector
  // mono-profile mode, intact).
  useEffect(() => {
    if (backgroundLstar == null || !bgCanvasRef.current) return;
    const canvas = bgCanvasRef.current;
    // rAF coalescing: a fast move of the L* slider emits many
    // values; we only repaint once per frame with the latest one,
    // which keeps the drag smooth even at 512² internal.
    if (bgRafRef.current) cancelAnimationFrame(bgRafRef.current);
    bgRafRef.current = requestAnimationFrame(() => {
      paintLabChromaBackground(canvas, backgroundLstar, {
        range,
        alpha: backgroundAlpha,
        space: backgroundSpace || undefined,
      });
    });
    return () => {
      if (bgRafRef.current) cancelAnimationFrame(bgRafRef.current);
    };
  }, [backgroundLstar, backgroundAlpha, backgroundSpace, BG_RES,
    range.aMin, range.aMax, range.bMin, range.bMax]);

  const TICK_STEP = 50;
  const ticks = [];
  const startTick = Math.ceil(range.aMin / TICK_STEP) * TICK_STEP;
  for (let t = startTick; t <= range.aMax; t += TICK_STEP) {
    if (t > range.aMin && t < range.aMax) ticks.push(t);
  }

  const handleMouseMove = (e) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const sx = (e.clientX - rect.left) * (size / rect.width);
    const sy = (e.clientY - rect.top) * (size / rect.height);
    if (sx < padding || sx > size - padding || sy < padding || sy > size - padding) {
      setHover(null);
      return;
    }
    const a = range.aMin + ((sx - padding) / plotSize) * (range.aMax - range.aMin);
    const b = range.bMax - ((sy - padding) / plotSize) * (range.bMax - range.bMin);
    const C = Math.sqrt(a * a + b * b);
    let h = Math.atan2(b, a) * 180 / Math.PI;
    if (h < 0) h += 360;
    setHover({ a, b, C, h, sx, sy });
  };

  const handleMouseLeave = () => setHover(null);

  function _rgbToCss(c) {
    const r = Math.round(Math.max(0, Math.min(1, c[0])) * 255);
    const g = Math.round(Math.max(0, Math.min(1, c[1])) * 255);
    const b = Math.round(Math.max(0, Math.min(1, c[2])) * 255);
    return `rgb(${r},${g},${b})`;
  }

  // Relative position of the canvas within the viewBox: the
  // chromaticity map occupies the useful area of the plot (between the paddings).
  const bgInsetPct = (padding / size) * 100;
  const bgSizePct = (plotSize / size) * 100;

  return (
    <div
      ref={containerRef}
      className="relative w-full"
      style={{ maxWidth: size }}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}>
      {backgroundLstar != null && (
        <canvas
          ref={bgCanvasRef}
          width={BG_RES}
          height={BG_RES}
          className="absolute pointer-events-none"
          style={{
            left: `${bgInsetPct}%`,
            top: `${bgInsetPct}%`,
            width: `${bgSizePct}%`,
            height: `${bgSizePct}%`,
            imageRendering: 'auto',
          }}
          aria-hidden="true"/>
      )}
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width="100%"
        className="block relative">

        <defs>
          {/* sRGB gradients per segment when mesh.colors is provided (1-contour
              mode). Vertex colors interpolated in t,
              like the position. */}
          {meshes.map((m, mi) =>
            m.colors ? m.segments.map((seg, si) => {
              const [a0, b0, a1, b1] = seg;
              const [c0, c1] = m.colors[si] || [[0.5, 0.5, 0.5], [0.5, 0.5, 0.5]];
              return (
                <linearGradient
                  key={`grad-${mi}-${si}`}
                  id={`grad-${mi}-${si}`}
                  gradientUnits="userSpaceOnUse"
                  x1={xScale(a0)} y1={yScale(b0)}
                  x2={xScale(a1)} y2={yScale(b1)}>
                  <stop offset="0%" stopColor={_rgbToCss(c0)}/>
                  <stop offset="100%" stopColor={_rgbToCss(c1)}/>
                </linearGradient>
              );
            }) : null,
          )}
        </defs>

        {/* Frame — neutral token */}
        <rect
          x={padding} y={padding}
          width={plotSize} height={plotSize}
          fill="none"
          stroke="var(--border-strong)"
          strokeOpacity="0.55"/>

        {/* Grid (tick lines) — discreet `--border-soft` */}
        {ticks.map((t) => (
          <g key={`tick-${t}`}>
            <line
              x1={xScale(t)} y1={padding}
              x2={xScale(t)} y2={size - padding}
              stroke="var(--border-soft)"
              strokeOpacity="0.8"/>
            <line
              x1={padding} y1={yScale(t)}
              x2={size - padding} y2={yScale(t)}
              stroke="var(--border-soft)"
              strokeOpacity="0.8"/>
          </g>
        ))}

        {/* a=0 and b=0 axes (neutral cross, stronger than the grid) */}
        <line
          x1={xScale(0)} y1={padding}
          x2={xScale(0)} y2={size - padding}
          stroke="var(--border-strong)" strokeOpacity="0.85" strokeDasharray="2 3"/>
        <line
          x1={padding} y1={yScale(0)}
          x2={size - padding} y2={yScale(0)}
          stroke="var(--border-strong)" strokeOpacity="0.85" strokeDasharray="2 3"/>

        {/* Axis labels — text-faint */}
        <text x={size - padding + 4} y={yScale(0) + 4}
              fontSize="10" fill="var(--text-faint)"
              fontFamily="monospace">+a*</text>
        <text x={xScale(0) + 4} y={padding - 4}
              fontSize="10" fill="var(--text-faint)"
              fontFamily="monospace">+b*</text>

        {/* Tick labels — text-faint */}
        {ticks.map((t) => (
          <g key={`tick-label-${t}`}>
            <text
              x={xScale(t)} y={size - padding + 12}
              fontSize="9" textAnchor="middle"
              fill="var(--text-faint)"
              fontFamily="monospace">{t}</text>
            <text
              x={padding - 5} y={yScale(t) + 3}
              fontSize="9" textAnchor="end"
              fill="var(--text-faint)"
              fontFamily="monospace">{t}</text>
          </g>
        ))}

        {/* Slice contours */}
        {meshes.map((m, mi) => (
          <g key={`mesh-${mi}`}>
            {m.segments.map((seg, si) => {
              const [a0, b0, a1, b1] = seg;
              // If the mesh has a `colors` field, we use the per-segment sRGB
              // gradient (1-contour mode, actual hue). Otherwise solid
              // color from m.color (categorical or reference mode).
              const strokeAttr = m.colors
                ? `url(#grad-${mi}-${si})`
                : m.color;
              return (
                <line
                  key={`seg-${mi}-${si}`}
                  x1={xScale(a0)} y1={yScale(b0)}
                  x2={xScale(a1)} y2={yScale(b1)}
                  stroke={strokeAttr}
                  strokeWidth={m.strokeWidth ?? 1.5}
                  strokeDasharray={m.dashed ? '4 3' : undefined}
                  strokeOpacity={m.opacity ?? 1}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  shapeRendering="geometricPrecision"/>
              );
            })}
          </g>
        ))}

        {/* Hover cursor */}
        {hover && (
          <g>
            <circle
              cx={hover.sx} cy={hover.sy}
              r={2.5}
              fill="currentColor" fillOpacity="0.8"
              pointerEvents="none"/>
          </g>
        )}
      </svg>

      {/* Absolutely positioned tooltip */}
      {hover && (
        <div
          className="absolute pointer-events-none px-2 py-1.5 rounded-md text-tiny font-mono
                     bg-bg/95 border border-border-soft shadow-md"
          style={{
            left: Math.min(hover.sx * 100 / size, 80) + '%',
            top: Math.max((hover.sy - 40) * 100 / size, 0) + '%',
            transform: 'translate(8px, 0)',
            whiteSpace: 'nowrap',
          }}>
          <div className="text-text-strong">
            a*&nbsp;<span className="text-text-faint">=</span>&nbsp;{hover.a.toFixed(1)}
            &nbsp;&nbsp;b*&nbsp;<span className="text-text-faint">=</span>&nbsp;{hover.b.toFixed(1)}
          </div>
          <div className="text-text-muted">
            C*&nbsp;<span className="text-text-faint">=</span>&nbsp;{hover.C.toFixed(1)}
            &nbsp;&nbsp;h°&nbsp;<span className="text-text-faint">=</span>&nbsp;{hover.h.toFixed(1)}
          </div>
        </div>
      )}
    </div>
  );
}
