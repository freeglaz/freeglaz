import { useTranslation } from 'react-i18next';

/**
 * Distribution histogram of the patches' ΔE₀₀.
 *
 * Industrial ISO 12647-7 benchmarks (contract offset/CMYK proof) drawn in
 * NEUTRAL (faint gray, dashed) = CONTEXTUAL SCALE, never a verdict:
 *   - mean ΔE < 3, max ΔE < 6.
 * Fine art RGB is more demanding than these thresholds — hence "benchmark", not target.
 * The verdict remains the in-house grid, shown elsewhere at the top.
 *
 * Colors: bars = accent (informative neutral). NO red/amber here
 * (reserved for danger/uncertainty) — a benchmark is not an alert.
 */
const W = 480;
const H = 130;
const PAD_L = 8;
const PAD_R = 8;
const PAD_B = 18;
const PAD_T = 8;
const ISO_AVG = 3;   // target mean ΔE, ISO 12647-7 proof
const ISO_MAX = 6;   // tolerated max ΔE, ISO 12647-7 proof

export default function DeltaEHistogram({ patches }) {
  const { t } = useTranslation();
  if (!patches?.length) return null;

  const des = patches.map((p) => p.de2000);
  const peak = Math.max(...des);
  // X scale: always up to the ISO benchmarks (3 and 6) for context,
  // even if the profile is well below (typical fine art case).
  const xMax = Math.max(6.5, Math.ceil(peak + 0.5));
  const nbins = 26;
  const binW = xMax / nbins;
  const counts = new Array(nbins).fill(0);
  for (const d of des) {
    const i = Math.min(nbins - 1, Math.floor(d / binW));
    counts[i] += 1;
  }
  const maxCount = Math.max(...counts, 1);

  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;
  const xOf = (de) => PAD_L + (de / xMax) * plotW;
  const barW = plotW / nbins;

  return (
    <div>
      <div className="text-text-muted text-xs2 mb-1">{t('profils.check_hist_title')}</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
           aria-label={t('profils.check_hist_title')}>
        {/* X axis (baseline) */}
        <line x1={PAD_L} y1={H - PAD_B} x2={W - PAD_R} y2={H - PAD_B}
              className="stroke-border-soft" strokeWidth="1"/>
        {/* bars */}
        {counts.map((c, i) => {
          const h = (c / maxCount) * plotH;
          return (
            <rect key={i} x={PAD_L + i * barW + 0.5} y={H - PAD_B - h}
                  width={Math.max(0.5, barW - 1)} height={h}
                  className="fill-accent" opacity="0.78"/>
          );
        })}
        {/* ISO benchmarks — NEUTRAL (dashed gray), contextual */}
        {[{ x: ISO_AVG, l: t('profils.check_iso_avg') },
          { x: ISO_MAX, l: t('profils.check_iso_max') }].map((mk) => (
          xMax >= mk.x && (
            <g key={mk.l}>
              <line x1={xOf(mk.x)} y1={PAD_T} x2={xOf(mk.x)} y2={H - PAD_B}
                    className="stroke-text-faint" strokeWidth="1" strokeDasharray="3 3"/>
              <text x={xOf(mk.x) + 3} y={PAD_T + 8} className="fill-text-faint"
                    style={{ fontSize: '8px' }}>{mk.l}</text>
            </g>
          )
        ))}
        {/* X gradations */}
        {Array.from({ length: Math.floor(xMax) + 1 }, (_, k) => k).map((k) => (
          <text key={k} x={xOf(k)} y={H - 6} textAnchor="middle"
                className="fill-text-faint" style={{ fontSize: '8px' }}>{k}</text>
        ))}
      </svg>
      <div className="text-tiny text-text-faint text-center -mt-1">{t('profils.check_hist_xlabel')}</div>
    </div>
  );
}
