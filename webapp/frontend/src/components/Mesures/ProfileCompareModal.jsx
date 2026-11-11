import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Loader2, X } from 'lucide-react';
import * as api from '../../api/client.js';
import { fmt2, focusFinding, perceptualNote, statRows, topColours, whiteBlackRows } from '../../lib/profileCompare.js';

// Comparison of two STORED versions of the chart's profile (A = reference, B = new): ΔE00 between
// their A2B transforms, computed offline by the backend (never rebuilt, never installed).
export default function ProfileCompareModal({ chartId, a, b, onClose }) {
  const { t } = useTranslation();
  const [res, setRes] = useState({ data: null, error: null, done: false });
  useEffect(() => {
    let alive = true;
    api.compareBuilds(chartId, a, b)
      .then((d) => { if (alive) setRes({ data: d, error: null, done: true }); })
      .catch((e) => { if (alive) setRes({ data: null, error: e?.detail?.error || (e?.status ? 'error' : 'network'), done: true }); });
    return () => { alive = false; };
  }, [chartId, a, b]);
  const d = res.data;
  return createPortal(
    <div className="fixed inset-0 z-[110] bg-black/50 flex items-center justify-center p-4" role="dialog" aria-modal="true"
         aria-label={t('profile_compare.title')} onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}>
      <div className="bg-bg border border-border-soft rounded-[14px] shadow-2xl w-[880px] max-w-[96vw] max-h-[90vh] flex flex-col overflow-hidden" data-compare-modal>
        <div className="px-5 pt-4 pb-3 border-b border-border-soft flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <h2 className="text-[15px] font-semibold text-text-strong">{t('profile_compare.title')}</h2>
            <div className="text-tiny text-text-muted font-mono break-all">
              A {d?.a?.label || ''} ({a}) → B {d?.b?.label || ''} ({b})
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label={t('common.close')} className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted hover:bg-sunken"><X size={16}/></button>
        </div>
        <div className="overflow-auto px-5 py-3 space-y-3 text-xs2">
          {!res.done && <div className="flex items-center gap-2 text-text-muted"><Loader2 size={14} className="animate-spin"/>{t('profile_compare.loading')}</div>}
          {res.error && <div className="text-danger bg-danger/10 border border-danger/30 rounded-md px-3 py-2" role="alert">{t(`profile_compare.error.${res.error}`, { defaultValue: t('profile_compare.error.error') })}</div>}
          {d && (
            <>
              {d.identical_files && <div className="text-text-strong">{t('profile_compare.identical_files')}</div>}
              {(d.focus || []).map((f, i) => {
                const ff = focusFinding(f, d.method?.focus_radius);
                return <div key={i} className={`rounded-md border px-3 py-2 ${ff.concentrated ? 'border-accent/30 bg-accent/5' : 'border-border-soft bg-sunken/50'}`} data-focus-concentrated={ff.concentrated}>{t(ff.key, ff.params)}</div>;
              })}
              <table className="table w-full" data-compare-stats>
                <thead><tr className="text-text-muted">
                  <th className="text-left">{t('profile_compare.col.intent')}</th><th className="text-left">{t('profile_compare.col.set')}</th>
                  <th className="text-right">{t('profile_compare.col.n')}</th><th className="text-right">{t('profile_compare.col.mean')}</th>
                  <th className="text-right">{t('profile_compare.col.p95')}</th><th className="text-right">{t('profile_compare.col.max')}</th></tr></thead>
                <tbody className="font-mono">
                  {statRows(d).map((r) => (
                    <tr key={`${r.intent}-${r.set}`}><td className="font-sans">{t(`profile_compare.intent.${r.intent}`)}</td>
                      <td className="font-sans">{t(`profile_compare.set.${r.set}`)}</td><td className="text-right">{r.n}</td>
                      <td className="text-right">{fmt2(r.mean)}</td><td className="text-right">{fmt2(r.p95)}</td><td className="text-right">{fmt2(r.max)}</td></tr>
                  ))}
                </tbody>
              </table>
              {!d.method?.perceptual_compared && <div className="text-tiny text-text-faint">{t('profile_compare.no_perceptual')}</div>}
              {perceptualNote(d) && <div className="text-tiny text-text-muted" data-perceptual-note>{t(perceptualNote(d).key, perceptualNote(d).params)}</div>}
              <div>
                <div className="text-tiny font-semibold uppercase tracking-wide text-text-faint mb-1">{t('profile_compare.top_title')}</div>
                <ul className="space-y-0.5">
                  {topColours(d).map((c) => (
                    <li key={c.rgb255.join(',')} className="flex items-center gap-2 font-mono text-tiny">
                      <span className="inline-block w-4 h-4 rounded-sm border border-border-soft" title={t('profile_compare.swatch_note')}
                            style={{ background: `rgb(${c.rgb255.join(',')})` }}/>
                      <span className="w-[110px]">RGB {c.rgb255.join(' ')}</span>
                      <span className="w-[90px] text-right">ΔE00 {fmt2(c.de00)}</span>
                      <span className="text-text-faint">Lab {c.lab_a.join(' ')} → {c.lab_b.join(' ')}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="text-tiny text-text-muted">
                {whiteBlackRows(d).map((w) => <div key={w.key}>{t(`profile_compare.wb.${w.key}`)} : ΔE00 {fmt2(w.de00)}</div>)}
              </div>
              <p className="text-tiny text-text-faint leading-relaxed">{t('profile_compare.limits', { grid: d.method?.grid_n, measured: d.method?.n_measured })}</p>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
