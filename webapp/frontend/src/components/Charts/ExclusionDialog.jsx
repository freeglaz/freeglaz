import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Info, Loader2, XCircle } from 'lucide-react';
import * as api from '../../api/client.js';
import { REASON_CHOICES, classifyExclusionError, impactView, reasonPayload } from '../../lib/exclusion.js';

// Confirmation of the EXPLICIT exclusion of one reading, shared by MeasurementsModal (one scan)
// and QCTableModal (several scans) — one logic, one API (POST …/exclusion). Always a dry run
// first: the level, the refusal and the per-base consequences are the backend's. Nothing is
// shown as done before the server confirmed it. Never automatic. Mounted only while in use
// (key = the reading): its initial state is the reset.
const LEVEL_STYLE = {
  ok: 'border-border-soft bg-sunken/50', info: 'border-accent/30 bg-accent/5',
  warning: 'border-icc-warn/40 bg-icc-warn/10', strong_warning: 'border-icc-warn/60 bg-icc-warn/15',
  refused: 'border-danger/40 bg-danger/10',
};

export default function ExclusionDialog({ chartId, ti3, sid, rgb, extraChartIds = [], onClose, onDone }) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState('loading');      // loading | ready | refused | submitting | error
  const [view, setView] = useState(null);
  const [error, setError] = useState(null);
  const [kind, setKind] = useState('');
  const [text, setText] = useState('');
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    api.setReadingExclusion(chartId, ti3, sid, { dry_run: true, extra_chart_ids: extraChartIds })
      .then((r) => { if (alive.current) { setView(impactView(r.impact)); setPhase('ready'); } })
      .catch((e) => {
        if (!alive.current) return;
        const c = classifyExclusionError(e);
        if (c.kind === 'refused') { setView(impactView(c.impact)); setPhase('refused'); }
        else { setError(c); setPhase('error'); }
      });
    return () => { alive.current = false; };
  }, [chartId, ti3, sid]);   // eslint-disable-line react-hooks/exhaustive-deps

  const confirm = () => {
    if (phase !== 'ready') return;                    // double click / stale state: ignored
    setPhase('submitting');
    api.setReadingExclusion(chartId, ti3, sid, { reason: reasonPayload(kind, text), extra_chart_ids: extraChartIds })
      .then((r) => { if (alive.current) onDone?.(r); })
      .catch((e) => {
        if (!alive.current) return;
        const c = classifyExclusionError(e);
        if (c.kind === 'refused') { setView(impactView(c.impact)); setPhase('refused'); }   // data changed meanwhile
        else { setError(c); setPhase('error'); }
      });
  };

  const Icon = !view ? Info : view.level === 'refused' ? XCircle : (view.level === 'warning' || view.level === 'strong_warning') ? AlertTriangle : Info;
  return createPortal(
    <div className="fixed inset-0 z-[120] bg-black/40 flex items-center justify-center p-4" role="dialog" aria-modal="true"
         aria-label={t('exclusion.dialog.title', { sid })}
         onClick={(e) => { if (e.target === e.currentTarget && phase !== 'submitting') onClose?.(); }}>
      <div className="bg-bg border border-border-soft rounded-[12px] shadow-2xl w-[560px] max-w-[96vw] p-5 space-y-3" data-exclusion-dialog={phase}>
        <div>
          <h3 className="text-[14px] font-semibold text-text-strong">{t('exclusion.dialog.title', { sid })}</h3>
          <div className="text-tiny text-text-muted flex items-center gap-2 mt-0.5">
            {Array.isArray(rgb) && <span className="inline-block w-3.5 h-3.5 rounded-sm border border-border-soft"
                                          style={{ background: `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` }}/>}
            <span className="font-mono">{t('exclusion.dialog.subject', { sid, scan: ti3, rgb: Array.isArray(rgb) ? rgb.join(' ') : '—' })}</span>
          </div>
        </div>
        {phase === 'loading' && (
          <div className="flex items-center gap-2 text-xs2 text-text-muted"><Loader2 size={14} className="animate-spin"/>{t('exclusion.dialog.checking')}</div>
        )}
        {phase === 'error' && (
          <div className="text-xs2 text-danger bg-danger/10 border border-danger/30 rounded-md px-3 py-2" role="alert">
            {t(`exclusion.error.${error?.kind || 'error'}`)}
          </div>
        )}
        {view && (phase === 'ready' || phase === 'refused' || phase === 'submitting') && (
          <div className={`rounded-md border px-3 py-2.5 text-xs2 space-y-2 ${LEVEL_STYLE[view.level] || LEVEL_STYLE.info}`} data-level={view.level}>
            <div className="flex items-start gap-2">
              <Icon size={15} className="mt-0.5 flex-shrink-0"/>
              <div className="space-y-1">
                <div className="font-semibold text-text-strong">{t(view.headline, { n: view.others })}</div>
                <div className="text-text-muted">{t(`exclusion.level_explain.${view.level}`)}</div>
              </div>
            </div>
            {view.bases.length > 0 && (
              <table className="w-full text-tiny">
                <tbody>
                  {view.bases.map((b) => (
                    <tr key={b.base} className="border-t border-border-soft/40">
                      <td className="py-0.5 pr-2 text-text-muted">{t(`exclusion.base.${b.base}`)}</td>
                      <td className="py-0.5 text-text-strong">
                        {!b.used ? t('exclusion.base_not_used') : t(`exclusion.base_level.${b.level}`)}
                        {b.recommend && <span className="text-text-faint"> · {t('exclusion.recommend_remeasure')}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {view.basesDiffer && <div className="text-tiny text-text-faint">{t('exclusion.bases_differ')}</div>}
            {phase === 'refused' && <div className="text-text-strong">{t('exclusion.refused_advice')}</div>}
          </div>
        )}
        {(phase === 'ready' || phase === 'submitting') && (
          <div className="space-y-1">
            <label className="text-tiny text-text-muted" htmlFor="exclusion-reason">{t('exclusion.reason_label')}</label>
            <div className="flex gap-2">
              <select id="exclusion-reason" value={kind} onChange={(e) => setKind(e.target.value)} disabled={phase === 'submitting'}
                      className="text-xs2 bg-sunken border border-border-soft rounded-md px-2 py-1">
                <option value="">{t('exclusion.reason.none')}</option>
                {REASON_CHOICES.map((r) => <option key={r} value={r}>{t(`exclusion.reason.${r}`)}</option>)}
              </select>
              {kind === 'other' && (
                <input type="text" value={text} maxLength={180} onChange={(e) => setText(e.target.value)} disabled={phase === 'submitting'}
                       placeholder={t('exclusion.reason_other_placeholder')}
                       className="flex-1 text-xs2 bg-sunken border border-border-soft rounded-md px-2 py-1"/>
              )}
            </div>
            <div className="text-tiny text-text-faint">{t('exclusion.reason_informative')}</div>
          </div>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} disabled={phase === 'submitting'}
                  className="text-xs2 font-semibold px-3 py-1.5 rounded-md border border-border-soft text-text-strong hover:bg-sunken">
            {phase === 'refused' || phase === 'error' ? t('common.close') : t('common.cancel')}
          </button>
          {(phase === 'ready' || phase === 'submitting') && view?.canConfirm && (
            <button type="button" onClick={confirm} disabled={phase === 'submitting'} data-action="confirm-exclusion"
                    className="text-xs2 font-semibold px-3 py-1.5 rounded-md bg-accent text-on-accent hover:bg-accent-press disabled:opacity-60 inline-flex items-center gap-1.5">
              {phase === 'submitting' && <Loader2 size={13} className="animate-spin"/>}
              {t('exclusion.confirm')}
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
