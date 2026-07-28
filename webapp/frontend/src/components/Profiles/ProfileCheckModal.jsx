import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { X, ShieldCheck, AlertTriangle } from 'lucide-react';
import { checkProfile } from '../../api/client';
import ProfcheckReport from './ProfcheckReport';

/**
 * SELF-CONSISTENCY check (part a) of a stored profile: profcheck -k
 * (CIEDE2000) on its creation ti3 + white Lab. Verdict = IN-HOUSE grid.
 *
 * ⚠️ HONESTY: self-consistency is measured on the CREATION PATCHES = best case,
 * optimistic, mostly detects measurement anomalies (misreads). The true
 * predictive quality = INDEPENDENT validation (part b). The report body (metrics,
 * histogram, worst patches, full table) is shared with (b) via ProfcheckReport.
 */
export default function ProfileCheckModal({ open, target, onClose }) {
  const { t } = useTranslation();
  const [state, setState] = useState({ loading: true, report: null, error: null });

  useEffect(() => {
    if (!open || !target) return;
    setState({ loading: true, report: null, error: null });
    checkProfile({ path: target.absolute_path || target.path })
      .then((report) => setState({ loading: false, report, error: null }))
      .catch((e) => setState({ loading: false, report: null, error: e.message }));
  }, [open, target]);

  if (!open) return null;
  const { loading, report, error } = state;
  const profileName = target?.label || target?.filename;

  return createPortal(
    <div className="fixed inset-0 z-[100] bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4"
         onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}>
      <div className="bg-bg border border-border-soft rounded-[14px] shadow-2xl w-[560px] max-w-[95vw] max-h-[92vh] flex flex-col overflow-hidden">
        <div className="px-6 pt-5 pb-4 flex items-start gap-3 border-b border-border-soft">
          <ShieldCheck size={18} className="text-accent mt-0.5"/>
          <div className="flex-1 min-w-0">
            <div className="text-[10px] font-bold tracking-[0.10em] uppercase text-accent font-mono">
              {t('profils.check_kicker')}
            </div>
            <h2 className="text-[17px] font-semibold text-text-strong">{profileName}</h2>
          </div>
          <button type="button" onClick={onClose} aria-label={t('common.close')}
                  className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-strong hover:bg-sunken">
            <X size={16}/>
          </button>
        </div>

        <div className="px-6 py-5 overflow-y-auto">
          {loading && (
            <div className="flex items-center gap-3 py-6 justify-center text-text-muted">
              <div className="w-5 h-5 border-2 border-accent/30 border-t-accent rounded-full animate-spin"/>
              <span className="text-sm">{t('profils.check_running')}</span>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 text-xs2 text-danger bg-danger/10 rounded-md px-3 py-2">
              <AlertTriangle size={14} className="mt-0.5"/><span>{error}</span>
            </div>
          )}

          {!loading && !error && report && (
            <ProfcheckReport report={report} scope="self" profileName={profileName}/>
          )}
        </div>

        <div className="px-6 py-4 border-t border-border-soft flex items-center justify-end">
          <button type="button" onClick={onClose}
                  className="text-xs2 font-semibold bg-accent text-white px-4 py-1.5 rounded-md hover:bg-accent/90">{t('common.close')}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
