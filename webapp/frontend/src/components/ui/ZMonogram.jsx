import { useId } from 'react';

/**
 * freeglaZ monogram — glaz gradient square + inverted white Z. Shared brand
 * asset (same path as the splash). Self-contained gradient with a unique id
 * (useId) so it can be instantiated multiple times without SVG id collisions.
 * No shadow by default: on a light background the teal square stands out on
 * its own (the TopNav). The splash adds its shadow separately (dark background).
 */
export default function ZMonogram({ size = 24, className = '' }) {
  const gid = `zmono-${useId()}`;
  return (
    <svg
      width={size} height={size} viewBox="0 0 100 100"
      className={className} aria-hidden="true">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="oklch(40% 0.07 222)"/>
          <stop offset="0.55" stopColor="oklch(57% 0.082 197)"/>
          <stop offset="1" stopColor="oklch(72% 0.066 182)"/>
        </linearGradient>
      </defs>
      <rect width="100" height="100" rx="24" fill={`url(#${gid})`}/>
      <path d="M26 28 H74 V39 H48 L74 61 V72 H26 V61 H52 L26 39 Z" fill="#fff"/>
    </svg>
  );
}
