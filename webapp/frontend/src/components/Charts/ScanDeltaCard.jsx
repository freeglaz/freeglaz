import { useTranslation } from 'react-i18next';

// Inter-scan ΔE concordance card — PRESENTATIONAL (zero wizard/isLoaded coupling).
// Extracted from ArgyllProfileWizard to be shared: wizard's "scanned" screen AND the
// Measurements tab (chart detail). One card, two contexts, no duplication.

// Color triage (semantics: amber=uncertainty/drying, red=DANGER/contamination;
// never mix). Thresholds on the MAX (danger = 1 abnormal patch that an average would drown out);
// the isolated flag (max >> p95) escalates to red.
export function _deltaLevel(s) {
  if (!s) return null;
  if (s.isolated_outlier || s.max > 3) return 'danger';   // isolated spike / high divergence
  if (s.max > 0.8) return 'warn';                          // moderate family = probable drying
  return 'ok';                                             // concordant
}

export default function ScanDeltaCard({ delta, onSeeAll }) {
  const { t } = useTranslation();
  const s = delta.summary;
  const lvl = _deltaLevel(s);
  const tone = lvl === 'danger' ? 'border-danger/40 bg-danger/5'
             : lvl === 'warn' ? 'border-icc-warn/40 bg-icc-warn/10'
             : 'border-icc-ok/40 bg-icc-ok/5';
  const dot = lvl === 'danger' ? 'bg-danger' : lvl === 'warn' ? 'bg-warn' : 'bg-ok';
  // Verdict NAMES the cause (not a "everything's wrong"): amber = drying (uncertainty),
  // red = contamination (danger) — color semantics respected.
  const verdict = lvl === 'ok'
    ? t('scan.delta.verdict_ok')
    : s.isolated_outlier
      ? t('scan.delta.verdict_isolated', { patch: s.worst_patch_id })
      : lvl === 'danger'
        ? t('scan.delta.verdict_danger')
        : t('scan.delta.verdict_warn');
  return (
    <div className={`rounded-md border px-3 py-2.5 space-y-1.5 ${tone}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs2 font-medium text-text-strong">
          <span className={`inline-block w-2.5 h-2.5 rounded-full ${dot}`}/>
          {t('scan.delta.title', { count: delta.n_scans })}
        </div>
        <button type="button" onClick={onSeeAll}
                className="text-tiny text-accent underline hover:text-accent-press">
          {t('scan.delta.see_all')}
        </button>
      </div>
      <div className="text-tiny text-text-muted font-mono">
        {t('scan.delta.summary', { mean: s.mean.toFixed(2), median: s.median.toFixed(2), max: s.max.toFixed(2), patch: s.worst_patch_id })}
      </div>
      <p className="text-tiny text-text-faint leading-snug">{verdict}</p>
    </div>
  );
}
