import { useTranslation } from 'react-i18next';

/**
 * Inline SVG sparkline.
 *
 * ALWAYS displays the curve + the reference y=x diagonal. If the
 * curve is trivial (y=x), it is visible (the colored trace
 * overlaps the gray diagonal) — no masking. A small
 * discreet "≈ identity" indicator in the corner if it deviates < 1e-4.
 *
 * fix: default dimensions 180×80, channel label handled by
 * the parent (CurvesRow in TagPopover).
 *
 * @param {object} p
 * @param {number[]} p.samples — y values ∈ [0, 1], n ≥ 2
 * @param {string} p.color — Valid CSS stroke color
 * @param {boolean} p.linear — Backend hint (trivial y=x curve) → discreet
 *   identity badge in the corner, but the trace is still displayed
 * @param {number} p.width
 * @param {number} p.height
 * @param {string} p.label — Aria-label
 */
export default function Sparkline({
  samples, color = '#7B7B7B',
  linear = false,
  width = 180, height = 80,
  label,
}) {
  const { t } = useTranslation();
  if (!samples || samples.length < 2) {
    return (
      <div
        className="inline-flex items-center justify-center
                   bg-sunken/30 border border-border-soft rounded
                   text-text-faint"
        style={{ width, height, fontSize: 10 }}>
        —
      </div>
    );
  }

  const n = samples.length;
  const pad = 4;
  const w = width - 2 * pad;
  const h = height - 2 * pad;
  // Clamp values to [0, 1] — some XYZ curves may slightly
  // exceed it (case Y > 1 on linear output).
  const clamped = samples.map((v) => Math.max(0, Math.min(1, v)));
  const pts = clamped.map((v, i) => {
    const x = pad + (i / (n - 1)) * w;
    const y = pad + (1 - v) * h;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');

  // Strict identity detection (Σ(yi - xi)² < 1e-4 over n points) for
  // the discreet indicator. Distinct from the `linear` prop which comes from the backend
  // (wider tolerance).
  const isExactIdentity = _isExactIdentity(samples);

  return (
    <svg
      width={width} height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label || 'Sparkline'}
      className="bg-sunken/40 border border-border-soft rounded">
      {/* Reference y=x diagonal — always visible, in muted gray */}
      <line
        x1={pad} y1={height - pad}
        x2={width - pad} y2={pad}
        stroke="currentColor" strokeWidth="1"
        className="text-text-faint" opacity="0.5"
        strokeDasharray="2,2"/>
      {/* Actual curve on top, channel color */}
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"/>
      {/* Discreet "≈ identity" badge if it deviates < 1e-4 */}
      {isExactIdentity && (
        <text
          x={width - pad - 2} y={pad + 9}
          textAnchor="end"
          fontSize="9"
          className="fill-text-faint font-mono"
          aria-hidden="true">
          {t('inspector.sparkline_identity_hint')}
        </text>
      )}
    </svg>
  );
}


function _isExactIdentity(samples) {
  if (!samples || samples.length < 2) return false;
  const n = samples.length;
  let acc = 0;
  for (let i = 0; i < n; i++) {
    const x = i / (n - 1);
    const d = samples[i] - x;
    acc += d * d;
  }
  return acc / n < 1e-4;
}


/** Standard channel colors for the sparkline. */
export const CHANNEL_COLOR = {
  R: '#C53030',
  G: '#3A8540',
  B: '#1F5FB8',
  C: '#0096D6',
  M: '#C53030',
  Y: '#D49B3D',
  K: '#444444',
  X: '#C53030',
  // Y conflicts with Yellow — for the PCS output, we use a neutral green
  Z: '#1F5FB8',
};

export function channelColor(label) {
  if (label === 'Y') {
    // Resolve the ambiguity Y (Yellow CMYK) vs Y (XYZ luminance). When the
    // sparkline is in a PCS output context, we pass the explicit color
    // prop. By default → neutral gray.
    return '#3A8540';
  }
  return CHANNEL_COLOR[label] || '#666666';
}
