import { useState, useMemo, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { saveFile } from '../../lib/fileIO.js';
import { X, Ban, RotateCcw, AlertTriangle, ArrowUp, ArrowDown } from 'lucide-react';
import * as api from '../../api/client.js';

// QC patch × scan view — the scalpel. FROZEN columns on the left: SAMPLE_ID + RGB device
// (the common-sense anchor) + sortable DISAGREEMENT index → the suspects rise on their own. One column
// per included scan (L/a/b), horizontal scroll. Intruding reading highlighted; rejected struck through.
// Action [reject this reading] (patch × scan), reversible. The patch is then averaged over its
// remaining readings. Data via /scan/qc; rejection via /scans/{ti3}/readings/{sid}/reject.
const _rgbCss = (rgb) => `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
const _short = (ti3) => ti3.replace(/\.ti3$/, '').slice(-6);   // last 6 chars (HHMMSS) or short name

function _toCsv(scans, patches) {
  const head = ['id', 'disagreement', 'R', 'G', 'B',
    ...scans.flatMap((s, i) => [`s${i + 1}_L`, `s${i + 1}_a`, `s${i + 1}_b`, `s${i + 1}_rejected`])].join(',');
  const rows = patches.map((p) => [
    p.id, p.disagreement, ...p.rgb,
    ...p.readings.flatMap((r) => [...r.lab, r.rejected ? 1 : 0]),
  ].join(','));
  return [head, ...rows].join('\n');
}

export default function QCTableModal({ open, chartId, title, onClose, onChanged }) {
  const { t } = useTranslation();
  const [qc, setQc] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);          // a rejection in flight (locks the table)
  const [sortKey, setSortKey] = useState('disagreement');
  const [sortDir, setSortDir] = useState('desc');

  const load = useCallback(() => {
    if (!chartId) return;
    api.getScanQC(chartId).then((d) => { setQc(d); setErr(null); })
      .catch((e) => { setErr(e?.message || t('errors.qc_unavailable')); setQc(null); });
  }, [chartId]);
  useEffect(() => { if (open) load(); }, [open, load]);

  const sorted = useMemo(() => {
    if (!qc?.patches) return [];
    const fn = sortKey === 'id' ? ((p) => p.id) : ((p) => p.disagreement);
    const arr = [...qc.patches].sort((a, b) => (fn(a) > fn(b) ? 1 : fn(a) < fn(b) ? -1 : 0));
    return sortDir === 'desc' ? arr.reverse() : arr;
  }, [qc, sortKey, sortDir]);

  if (!open) return null;

  const setSort = (k) => {
    if (k === sortKey) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    else { setSortKey(k); setSortDir(k === 'disagreement' ? 'desc' : 'asc'); }
  };

  const reject = (ti3, sampleId, rejected) => {
    if (busy) return;
    setBusy(true); setErr(null);
    api.setReadingRejected(chartId, ti3, sampleId, rejected)
      .then(() => { load(); onChanged?.(); })
      .catch((e) => setErr((e?.message || '').includes('409')
        ? t('scan.qc.last_valid') : (e?.message || t('scan.qc.reject_failed'))))
      .finally(() => setBusy(false));
  };

  const exportCsv = () => {
    if (!qc) return;
    saveFile(`qc_${(title || chartId).replace(/[^\w.-]+/g, '_')}.csv`, _toCsv(qc.scans, sorted), 'text/csv;charset=utf-8');
  };

  const SortHead = ({ k, label }) => (
    <span className="inline-flex items-center gap-1 cursor-pointer select-none hover:text-text-strong"
          onClick={() => setSort(k)}>
      {label}{sortKey === k && (sortDir === 'desc' ? <ArrowDown size={11}/> : <ArrowUp size={11}/>)}
    </span>
  );
  // frozen left cell (ID + RGB swatch + disagreement) — reused in header & body
  const frozenCls = 'sticky left-0 z-10 bg-bg';

  return createPortal(
    <div className="fixed inset-0 z-[115] bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4"
         onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}>
      <div className="bg-bg border border-border-soft rounded-[14px] shadow-2xl w-[860px] max-w-[97vw] max-h-[90vh] flex flex-col overflow-hidden">
        <div className="px-6 pt-5 pb-3 flex items-center gap-3 border-b border-border-soft">
          <div className="flex-1 min-w-0">
            <h2 className="text-[15px] font-semibold text-text-strong truncate">
              {t('scan.qc.title', { count: qc?.n_patches || 0 })}
            </h2>
            <div className="text-tiny text-text-faint truncate">
              {title}{qc ? ` · ${qc.n_scans} ${t('scan.scans_label').toLowerCase()}` : ''}
            </div>
          </div>
          {qc && (
            <button type="button" onClick={exportCsv}
                    className="text-xs2 font-semibold inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border-soft text-text-strong hover:bg-sunken">
              {t('profils.check_all_export')}
            </button>
          )}
          <button type="button" onClick={onClose} aria-label={t('common.close')}
                  className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-strong hover:bg-sunken">
            <X size={16}/>
          </button>
        </div>

        {err && <div className="mx-6 mt-3 text-xs2 text-danger bg-danger/10 border border-danger/30 rounded-md px-3 py-2">{err}</div>}
        <p className="px-6 pt-3 text-tiny text-text-faint">{t('scan.qc.hint')}</p>

        <div className="overflow-auto m-6 mt-3 border border-border-soft rounded-lg">
          <table className="table">
            <thead className="sticky top-0 z-20 bg-bg border-b border-border-soft">
              <tr>
                <th className={`${frozenCls} z-30 px-3 py-2 text-left font-semibold text-text-muted border-r border-border-soft`}>
                  <div className="flex items-center gap-3">
                    <SortHead k="id" label={t('profils.check_all_id')}/>
                    <span className="text-text-faint">·</span>
                    <SortHead k="disagreement" label={t('scan.qc.disagreement')}/>
                  </div>
                </th>
                {(qc?.scans || []).map((s, i) => (
                  <th key={s} className="px-3 py-2 text-left font-semibold text-text-muted whitespace-nowrap">
                    {t('scan.qc.scan_col', { n: i + 1 })}
                    <span className="ml-1 font-mono text-text-faint font-normal">{_short(s)}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="font-mono">
              {sorted.map((p) => {
                const hot = p.disagreement > 1.5;
                return (
                  <tr key={p.id} className="border-b border-border-soft/40">
                    <td className={`${frozenCls} px-3 py-1.5 border-r border-border-soft`}>
                      <div className="flex items-center gap-2">
                        <span className="inline-block w-4 h-4 rounded-sm border border-border-soft flex-shrink-0"
                              style={{ background: _rgbCss(p.rgb) }}/>
                        <span className="text-text-muted w-8">{p.id}</span>
                        <span className={`tabular-nums ${hot ? 'text-danger font-semibold' : 'text-text-faint'}`}>
                          {p.disagreement.toFixed(2)}
                        </span>
                        <span className="text-text-faint font-sans">{p.rgb.join(' ')}</span>
                      </div>
                    </td>
                    {p.readings.map((r) => (
                      <td key={r.ti3}
                          className={`px-3 py-1.5 whitespace-nowrap ${r.outlier ? 'bg-icc-warn/15' : ''}`}>
                        <div className="flex items-center gap-2">
                          {r.outlier && <AlertTriangle size={12} className="text-warn flex-shrink-0"/>}
                          <span className={r.rejected ? 'line-through opacity-50 text-text-faint' : 'text-text-muted'}>
                            {r.lab.map((v) => v.toFixed(1)).join(' ')}
                          </span>
                          {r.rejected ? (
                            <button type="button" disabled={busy} title={t('scan.qc.reinclude')}
                                    onClick={() => reject(r.ti3, p.id, false)}
                                    className="text-text-faint hover:text-accent disabled:opacity-40">
                              <RotateCcw size={13}/>
                            </button>
                          ) : (
                            <button type="button" disabled={busy} title={t('scan.qc.reject')}
                                    onClick={() => reject(r.ti3, p.id, true)}
                                    className="text-text-faint hover:text-danger disabled:opacity-40">
                              <Ban size={13}/>
                            </button>
                          )}
                        </div>
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="px-6 py-3 border-t border-border-soft flex items-center justify-between">
          <span className="text-tiny text-text-faint">{t('scan.qc.legend')}</span>
          <button type="button" onClick={onClose}
                  className="text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press">{t('common.close')}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
