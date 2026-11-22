import { useTranslation } from 'react-i18next';
import { Info } from 'lucide-react';
import { chartMeasurement, instrumentLabel } from '../../lib/measurementMethod.js';

// An external-instrument (printtarg) chart, where the built-in scan would otherwise be offered:
// explicit state, no measurement action (the chartread driver is a later increment). Never
// calls /scan, never pretends a measurement exists.
export default function ExternalMeasurementPending({ chart, nScans = 0, inLibrary = false }) {
  const { t } = useTranslation();
  const m = chartMeasurement(chart);
  return (
    <div className="rounded-md border border-accent/30 bg-accent/5 px-3 py-2.5 text-xs2 space-y-1.5" data-external-pending="1">
      <div className="flex items-center gap-1.5 font-semibold text-text-strong">
        <Info size={14} className="text-accent flex-shrink-0"/>{t('measurement.pending.title')}
      </div>
      <p className="text-text-strong leading-relaxed">{t('measurement.pending.body', { instrument: instrumentLabel(t, m.instrument) })}</p>
      {nScans > 0 && <p className="text-text-muted">{t(inLibrary ? 'measurement.pending.has_scans_here' : 'measurement.pending.has_scans', { count: nScans })}</p>}
      <p className="text-tiny text-text-faint">{t('measurement.pending.later')}</p>
    </div>
  );
}
