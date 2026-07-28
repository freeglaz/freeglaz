import { useState, useMemo, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { saveFile } from '../../lib/fileIO.js';
import { X, Download, ArrowUp, ArrowDown } from 'lucide-react';
import * as api from '../../api/client.js';

// View the measurements of ONE scan (>=1) — DECOUPLED from the comparison (QC >=2). id + RGB device + Lab,
// sortable, + CSV export (the user's data). Data via /scans/{ti3}/patches
// (reuses scan_delta._read_lab_rgb on the backend). Presentational, read-only.
const _rgbCss = (rgb) => `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;

function _toCsv(patches) {
  const head = 'id,R,G,B,L,a,b';
  const rows = patches.map((p) => [p.id, ...p.rgb, ...p.lab].join(','));
  return [head, ...rows].join('\n');
}

export default function MeasurementsModal({ open, chartId, ti3, onClose }) {
  const { t } = useTranslation();
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [sortKey, setSortKey] = useState('id');
  const [sortDir, setSortDir] = useState('asc');

  useEffect(() => {
    if (!open || !chartId || !ti3) return;
    setData(null); setErr(null);
    api.getScanPatches(chartId, ti3)
      .then(setData)
      .catch((e) => setErr(e?.message || t('scan.measures.unavailable')));
  }, [open, chartId, ti3, t]);

  const sorted = useMemo(() => {
    if (!data?.patches) return [];
    const fn = sortKey === 'rgb' ? ((p) => p.rgb[0] * 1e6 + p.rgb[1] * 1e3 + p.rgb[2])
             : sortKey === 'labL' ? ((p) => p.lab[0]) : ((p) => p.id);
    const arr = [...data.patches].sort((a, b) => (fn(a) > fn(b) ? 1 : fn(a) < fn(b) ? -1 : 0));
    return sortDir === 'desc' ? arr.reverse() : arr;
  }, [data, sortKey, sortDir]);

  if (!open) return null;

  const setSort = (k) => {
    if (k === sortKey) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    else { setSortKey(k); setSortDir('asc'); }
  };
  const exportCsv = () => {
    if (!data) return;
    saveFile(`mesures_${ti3.replace(/\.ti3$/, '')}.csv`, _toCsv(sorted), 'text/csv;charset=utf-8');
  };
  const SortHead = ({ k, label, align = 'left' }) => (
    <th className={`px-2 py-1.5 font-semibold text-text-muted cursor-pointer select-none hover:text-text-strong text-${align}`}
        onClick={() => setSort(k)}>
      <span className="inline-flex items-center gap-1">
        {label}{sortKey === k && (sortDir === 'desc' ? <ArrowDown size={11}/> : <ArrowUp size={11}/>)}
      </span>
    </th>
  );

  return createPortal(
    <div className="fixed inset-0 z-[110] bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4"
         onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}>
      <div className="bg-bg border border-border-soft rounded-[14px] shadow-2xl w-[640px] max-w-[96vw] max-h-[90vh] flex flex-col overflow-hidden">
        <div className="px-6 pt-5 pb-3 flex items-center gap-3 border-b border-border-soft">
          <div className="flex-1 min-w-0">
            <h2 className="text-[15px] font-semibold text-text-strong truncate">
              {t('scan.measures.title', { count: data?.n_patches || 0 })}
            </h2>
            <div className="text-tiny text-text-faint font-mono truncate">{ti3}</div>
          </div>
          {data && (
            <button type="button" onClick={exportCsv}
                    className="text-xs2 font-semibold inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border-soft text-text-strong hover:bg-sunken">
              <Download size={14}/> {t('profils.check_all_export')}
            </button>
          )}
          <button type="button" onClick={onClose} aria-label={t('common.close')}
                  className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-strong hover:bg-sunken">
            <X size={16}/>
          </button>
        </div>
        {err && <div className="mx-6 mt-3 text-xs2 text-danger bg-danger/10 border border-danger/30 rounded-md px-3 py-2">{err}</div>}
        <div className="overflow-y-auto">
          <table className="table">
            <thead className="sticky top-0 bg-bg">
              <tr>
                <SortHead k="id" label={t('profils.check_all_id')}/>
                <th className="px-2 py-1.5"/>
                <SortHead k="rgb" label={t('profils.check_all_rgb')}/>
                <SortHead k="labL" label="Lab"/>
              </tr>
            </thead>
            <tbody className="font-mono">
              {sorted.map((p) => (
                <tr key={p.id} className="border-b border-border-soft/40 hover:bg-sunken/50">
                  <td className="px-2 py-1 text-text-muted">{p.id}</td>
                  <td className="px-2 py-1">
                    <span className="inline-block w-4 h-4 rounded-sm border border-border-soft align-middle"
                          style={{ background: _rgbCss(p.rgb) }}/>
                  </td>
                  <td className="px-2 py-1 text-text-muted">{p.rgb.join(' ')}</td>
                  <td className="px-2 py-1 text-text-muted">{p.lab.map((v) => v.toFixed(1)).join(' ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="px-6 py-3 border-t border-border-soft flex justify-end">
          <button type="button" onClick={onClose}
                  className="text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press">{t('common.close')}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
