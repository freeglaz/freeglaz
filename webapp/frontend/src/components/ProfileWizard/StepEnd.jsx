import { AlertTriangle, Check, Info } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { processToPhase } from './PhaseTracker.jsx';
import { geLabel } from '../../lib/geLabel.js';

export default function StepEnd({ variant, result, error, job, paper, glossEnhancer, workflow, onViewInDetailPanel }) {
  if (variant === 'success') {
    if (workflow === 'PRINT_ONLY') {
      return <PrintOnlySuccessVariant paper={paper} glossEnhancer={glossEnhancer}/>;
    }
    return <SuccessVariant result={result} paper={paper} glossEnhancer={glossEnhancer} onViewInDetailPanel={onViewInDetailPanel}/>;
  }
  return <ErrorVariant error={error} job={job}/>;
}


function SuccessVariant({ result, paper, glossEnhancer, onViewInDetailPanel }) {
  const { t, i18n } = useTranslation();
  const geText = geLabel(glossEnhancer);
  const dateStr = _fmtDate(result?.profile_date, i18n.language);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-full bg-success/[0.12] flex items-center justify-center flex-shrink-0">
          <Check size={16} strokeWidth={2.5} className="text-success"/>
        </div>
        <h2 className="text-base font-semibold text-text-strong">
          {t('wizard_profile_paper.end_success_title')}
        </h2>
      </div>

      <div className="border border-border-soft rounded-lg p-4 space-y-2.5">
        <RecapRow label={t('wizard_profile_paper.end_success_recap_paper')} value={paper?.name}/>
        <RecapRow label={t('wizard_profile_paper.end_success_recap_profile')}
          value={<span className="font-mono text-xs2">{result?.profile_icc_name || result?.profile_name || '—'}</span>}/>
        <RecapRow label={t('wizard_profile_paper.end_success_recap_slot')} value={geText}/>
        <RecapRow label={t('wizard_profile_paper.end_success_recap_date')} value={dateStr || '—'}/>
      </div>

      {/* ColorSync hint banner */}
      <div className="flex items-start gap-3 bg-accent/[0.08] border border-accent/25 rounded-md p-3">
        <Info size={16} className="text-accent mt-0.5 flex-shrink-0" aria-hidden="true"/>
        <div className="flex-1">
          <p className="text-sm text-text-muted leading-relaxed">
            {t('wizard_profile_paper.end_success_colorsync_hint')}
          </p>
          <button
            type="button"
            onClick={onViewInDetailPanel}
            className="mt-2.5 text-sm font-medium text-accent hover:text-accent-press transition-colors">
            {t('wizard_profile_paper.end_success_view_in_panel')}
          </button>
        </div>
      </div>
    </div>
  );
}


function PrintOnlySuccessVariant({ paper, glossEnhancer }) {
  const { t } = useTranslation();
  const geText = geLabel(glossEnhancer);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-full bg-success/[0.12] flex items-center justify-center flex-shrink-0">
          <Check size={16} strokeWidth={2.5} className="text-success"/>
        </div>
        <h2 className="text-base font-semibold text-text-strong">
          {t('wizard_profile_paper.end_print_only_title')}
        </h2>
      </div>

      <div className="border border-border-soft rounded-lg p-4 space-y-2.5">
        <RecapRow label={t('wizard_profile_paper.end_success_recap_paper')} value={paper?.name}/>
        <RecapRow label={t('wizard_profile_paper.end_success_recap_slot')} value={geText}/>
      </div>

      <div className="flex items-start gap-3 bg-accent/[0.08] border border-accent/25 rounded-md p-3">
        <Info size={16} className="text-accent mt-0.5 flex-shrink-0" aria-hidden="true"/>
        <p className="text-sm text-text-muted leading-relaxed">
          {t('wizard_profile_paper.end_print_only_hint')}
        </p>
      </div>
    </div>
  );
}


function ErrorVariant({ error, job }) {
  const { t } = useTranslation();
  const failedProcess = job?.process || '';
  const failedPhase = processToPhase(failedProcess);
  const phaseLabel = failedProcess
    ? t(`wizard_profile_paper.phase_${failedPhase === 'calculating' ? 'computing' : failedPhase}`)
    : t('wizard_profile_paper.phase_preparing');

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-full bg-danger/10 flex items-center justify-center flex-shrink-0">
          <AlertTriangle size={16} strokeWidth={2} className="text-danger"/>
        </div>
        <div>
          <h2 className="text-base font-semibold text-text-strong">
            {t('wizard_profile_paper.end_error_title')}
          </h2>
          <p className="text-xs2 text-text-muted">
            {t('wizard_profile_paper.end_error_subtitle', { phase: phaseLabel })}
          </p>
        </div>
      </div>

      {error && (
        <div className="bg-danger/[0.04] border border-danger/25 rounded-md p-3">
          <pre className="text-xs font-mono text-text-strong whitespace-pre-wrap break-words">
            {error}
          </pre>
        </div>
      )}

      <p className="text-xs2 text-text-muted leading-relaxed">
        {t('wizard_profile_paper.end_error_no_waste')}
      </p>
    </div>
  );
}


function RecapRow({ label, value }) {
  return (
    <div className="flex items-start gap-3">
      <span className="text-[10px] tracking-[0.08em] uppercase text-text-faint w-[80px] flex-shrink-0 pt-0.5">
        {label}
      </span>
      <span className="text-[13px] text-text-strong min-w-0 truncate">{value}</span>
    </div>
  );
}


function _fmtDate(iso, lang) {
  if (!iso) return null;
  const locale = lang === 'en' ? 'en-US' : 'fr-FR';
  try {
    return new Intl.DateTimeFormat(locale, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}
