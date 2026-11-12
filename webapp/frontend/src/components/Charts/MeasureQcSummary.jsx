// Summary of the INTRINSIC measurement QC of one scan (advisory). Shared by MeasurementsModal
// (Measurements tab) and the scan wizard's "scanned" screen — one presentation, no duplicate.
// Purely presentational: it receives the view-model from lib/measureQc.qcPanelState and never
// calls the API. Careful wording: "no inconsistency detected by this check", never "validated".
import { useTranslation } from 'react-i18next';
import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from 'lucide-react';
import { findingMessage } from '../../lib/measureQc.js';

const BOX = {
  attention: 'border-icc-warn/40 bg-icc-warn/10',
  limited: 'border-border-soft bg-sunken/60',
  clean: 'border-ok/30 bg-ok/5',
  invalid: 'border-danger/30 bg-danger/10',
  unavailable: 'border-border-soft bg-sunken/60',
  error: 'border-danger/30 bg-danger/10',
  loading: 'border-border-soft bg-sunken/40',
};

// `measurementsShown`: the parent really displays readable measurements below (MeasurementsModal
// with rows). Only then does a failed check say so — never a judgment on the scan itself.
export default function MeasureQcSummary({ state, compact = false, onOpen, measurementsShown = false }) {
  const { t } = useTranslation();
  if (!state || state.kind === 'none') return null;
  const k = state.kind;
  const Icon = k === 'loading' ? Loader2 : k === 'clean' ? CheckCircle2
             : (k === 'attention') ? AlertTriangle : (k === 'invalid' || k === 'error') ? XCircle : Info;
  const iconCls = k === 'loading' ? 'animate-spin text-text-muted' : k === 'clean' ? 'text-ok'
                : k === 'attention' ? 'text-icc-warn' : (k === 'invalid' || k === 'error') ? 'text-danger' : 'text-accent';

  let headline;
  if (k === 'loading') headline = t('measure_qc.summary.loading');
  else if (k === 'invalid') headline = t('measure_qc.state.invalid', {
    reason: state.code ? t(`measure_qc.error.${state.code}`, { defaultValue: state.code }) : t('measure_qc.error.unknown') });
  else if (k === 'unavailable') headline = t('measure_qc.state.unavailable');
  else if (k === 'error') headline = t('measure_qc.state.error');
  else if (k === 'attention') headline = state.nAttention > 0
    ? t('measure_qc.summary.attention', { count: state.nAttention })
    : t('measure_qc.summary.attention_findings');            // alert carried by a zone / set finding only
  else if (k === 'limited' && state.limitedByDensity) headline = t('measure_qc.summary.limited_check');   // sparse chart
  else if (k === 'limited') headline = state.counts.not_assessable > 0
    ? t('measure_qc.summary.limited', { count: state.counts.not_assessable })
    : t('measure_qc.summary.limited_no_na');
  else headline = t('measure_qc.summary.clean');

  const hasCounts = ['attention', 'limited', 'clean'].includes(k);
  const findings = hasCounts ? (state.findings || []).map((f) => findingMessage(f, state.findings)).filter(Boolean) : [];
  const warnings = hasCounts ? (state.warnings || []) : [];

  return (
    <div className={`rounded-md border px-3 py-2.5 text-xs2 space-y-1.5 ${BOX[k] || BOX.limited}`}
         data-qc-kind={k} role="status">
      <div className="flex items-start gap-2">
        <Icon size={15} className={`mt-0.5 flex-shrink-0 ${iconCls}`}/>
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-text-strong">{headline}</div>
          {hasCounts && (
            <div className="text-text-muted tabular-nums">
              {t('measure_qc.summary.counts', {
                n: state.n, strong: state.counts.strong, to_check: state.counts.to_check,
                na: state.counts.not_assessable })}
            </div>
          )}
        </div>
        {onOpen && hasCounts && (
          <button type="button" onClick={onOpen}
                  className="flex-shrink-0 text-xs2 font-semibold px-2.5 py-1 rounded-md border border-border-soft text-text-strong hover:bg-sunken">
            {t('measure_qc.open')}
          </button>
        )}
      </div>
      {findings.map((f) => (
        <div key={f.code + (f.params.rgb || '')} className="flex items-start gap-2 text-text-strong">
          <AlertTriangle size={13} className="mt-0.5 flex-shrink-0 text-icc-warn"/>
          <span>{f.key ? t(f.key, f.params) : f.code}</span>
        </div>
      ))}
      {warnings.map((w) => (
        <div key={w} className="flex items-start gap-2 text-text-muted">
          <Info size={13} className="mt-0.5 flex-shrink-0"/>
          <span>{t(`measure_qc.warning.${w}`, { defaultValue: w })}</span>
        </div>
      ))}
      {!compact && hasCounts && (
        <div className="text-tiny text-text-faint leading-relaxed">{t('measure_qc.summary.disclaimer')}</div>
      )}
      {measurementsShown && (k === 'invalid' || k === 'unavailable' || k === 'error') && (
        <div className="text-tiny text-text-faint">{t('measure_qc.state.measurements_shown')}</div>
      )}
    </div>
  );
}
