import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { AlertTriangle } from 'lucide-react';

// Explicit confirmation before a build that the preview shows as different from a plain build:
// exclusions applied, earlier exclusions newly applied, strong suspects kept, coverage warnings.
// Confirming never changes anything else than launching that build.
export default function BuildConfirmModal({ summary, onCancel, onConfirm }) {
  const { t } = useTranslation();
  if (!summary) return null;
  const lines = [
    summary.nExcluded > 0 && t('build_preview.excluded', { count: summary.nExcluded }),
    summary.nNewlyApplied > 0 && t('build_preview.newly_applied', { count: summary.nNewlyApplied }),
    summary.nHoles > 0 && t('build_preview.holes', { count: summary.nHoles }),
    summary.nStrongIncluded > 0 && t('build_preview.strong_included', { count: summary.nStrongIncluded }),
    summary.warnings.length > 0 && [...new Set(summary.warnings.map((g) => g.code))].map((c) => t(`build_preview.guard.${c}`, { defaultValue: c })).join(' · '),
    summary.recommend && t('build_preview.recommend'),
  ].filter(Boolean);
  return createPortal(
    <div className="fixed inset-0 z-[120] bg-black/40 flex items-center justify-center p-4" role="dialog" aria-modal="true"
         aria-label={t('build_preview.confirm_title')} onClick={(e) => { if (e.target === e.currentTarget) onCancel?.(); }}>
      <div className="bg-bg border border-border-soft rounded-[12px] shadow-2xl w-[520px] max-w-[96vw] p-5 space-y-3" data-build-confirm>
        <h3 className="text-[14px] font-semibold text-text-strong">{t('build_preview.confirm_title')}</h3>
        <ul className="space-y-1 text-xs2">
          {lines.map((l, i) => (<li key={i} className="flex items-start gap-1.5"><AlertTriangle size={13} className="mt-0.5 flex-shrink-0 text-icc-warn"/><span>{l}</span></li>))}
        </ul>
        <p className="text-tiny text-text-faint">{t('build_preview.confirm_note')}</p>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="text-xs2 font-semibold px-3 py-1.5 rounded-md border border-border-soft text-text-strong hover:bg-sunken">{t('common.cancel')}</button>
          <button type="button" onClick={onConfirm} data-action="confirm-build"
                  className="text-xs2 font-semibold px-3 py-1.5 rounded-md bg-accent text-on-accent hover:bg-accent-press">{t('build_preview.confirm')}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
