import { AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { UIState, isPrintable } from '../../lib/state-machine.js';
import { fmtPct } from '../../lib/format.js';

/**
 * Primary button + variants:
 *   - F_mismatch → orange banner above
 *   - E_printing → label + progress + Cancel button
 *   - C_oversized → disabled, label "Print · image too large"
 *   - otherwise → solid HP Blue button or disabled neutral gray
 */
export default function PrintButton({ state, progress = 0, geomStale = false, onPrint, onCancel }) {
  const { t } = useTranslation();
  const printing = state === UIState.E_PRINTING;
  // geomStale = the geometry/overflow guard has not been re-evaluated for the
  // current paper+orientation yet (e.g. just after a paper change). Safe by
  // default: no print on unrevalidated geometry (Bug A) — the button re-enables
  // as soon as the fresh preview arrives.
  const printable = isPrintable(state) && !geomStale;
  const pctNum = Math.round(progress * 100);

  return (
    <div className="px-5 pt-3.5 pb-4 border-t border-border-soft">
      {state === UIState.F_MISMATCH && (
        // The detail (file vs paper profile name) is in the ICC badge's
        // tooltip + in mono under the badge in PaperCard. No separate
        // "Details" button, to avoid an inert handler.
        <div className="flex items-start gap-2 mb-2.5 p-2.5 bg-warn/10 border border-warn/30 rounded-md">
          <AlertTriangle size={13} className="text-warn mt-0.5 flex-shrink-0"/>
          <div className="flex-1 text-xs2 leading-snug">
            <span className="font-semibold text-text-strong">{t('print.mismatch_banner_title')}</span>{' '}
            <span className="text-text-muted">{t('print.mismatch_banner_body')}</span>
          </div>
        </div>
      )}

      {printing ? (
        <>
          <div className="flex justify-between mb-2 text-xs">
            <span className="text-text-strong font-medium">{t('print.sending_to_z9')}</span>
            <span className="text-text-muted font-mono tabular-nums">{fmtPct(pctNum)}</span>
          </div>
          <div className="h-1.5 bg-sunken rounded-full overflow-hidden">
            <div className="h-full bg-accent rounded-full transition-[width] duration-300" style={{ width: `${pctNum}%` }}/>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="w-full mt-3 py-2 bg-transparent border border-border-soft rounded-md text-sm2 font-medium text-text-muted hover:text-text-strong hover:bg-surface transition-colors">
            {t('common.cancel')}
          </button>
        </>
      ) : (
        <button
          type="button"
          disabled={!printable}
          onClick={onPrint}
          className={`w-full py-3 rounded-lg text-sm font-semibold tracking-tight transition-colors
            ${printable
              ? 'bg-accent hover:bg-accent-press text-on-accent shadow-sm cursor-pointer'
              : 'bg-sunken-deep text-text-faint cursor-not-allowed'}`}>
          {state === UIState.C_OVERSIZED ? t('print.action_print_oversized') : t('print.action_print')}
        </button>
      )}
    </div>
  );
}
