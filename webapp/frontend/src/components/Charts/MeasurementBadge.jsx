import { useTranslation } from 'react-i18next';
import { chartBadge, instrumentLabel } from '../../lib/measurementMethod.js';

// How a chart is measured, as a small pill: "HP built-in" (honeycomb, also every historical
// chart without layout) or "External — <instrument>" (printtarg). Presentational only.
export default function MeasurementBadge({ chart, className = '' }) {
  const { t } = useTranslation();
  const b = chartBadge(chart);
  const label = b.kind === 'integrated' ? t('measurement.badge.integrated')
    : b.kind === 'external' ? t('measurement.badge.external', { instrument: instrumentLabel(t, b.instrument) })
      : t('measurement.badge.unknown');
  const tone = b.kind === 'external' ? 'border-accent/40 text-accent bg-accent/5'
    : 'border-border-soft text-text-muted bg-sunken/60';
  return (
    <span data-measurement-badge={b.kind} data-instrument={b.instrument || ''}
          className={`inline-flex items-center px-1.5 py-px rounded border text-[10px] font-medium whitespace-nowrap ${tone} ${className}`}>
      {label}
    </span>
  );
}
