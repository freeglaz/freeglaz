import { useState, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { saveFile } from '../../lib/fileIO.js';
import { X, Download, ArrowUp, ArrowDown } from 'lucide-react';

/**
 * FULL view of all patches of a check (part c.2 complement).
 * Scrollable + sortable table (ΔE / ID / device / Lab) + CSV export.
 * Pure presentation: same data as ProfileCheckModal, exhaustive access for
 * anyone wanting to analyze the whole distribution, look up a patch, export to a spreadsheet.
 */
const _rgbCss = (rgb) => `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;

const _SORTERS = {
  de2000: (p) => p.de2000,
  id: (p) => p.id,
  rgb: (p) => p.rgb[0] * 1e6 + p.rgb[1] * 1e3 + p.rgb[2],
  labL: (p) => p.lab_target[0],
};

function _toCsv(patches) {
  const head = 'id,de2000,R,G,B,target_L,target_a,target_b,achieved_L,achieved_a,achieved_b';
  const rows = patches.map((p) => [
    p.id, p.de2000, ...p.rgb, ...p.lab_target, ...p.lab_achieved,
  ].join(','));
  return [head, ...rows].join('\n');
}

export default function AllPatchesModal({ open, patches, title, onClose,
                                         titleLabel, labTargetLabel, labAchievedLabel }) {
  // OPTIONAL column labels (default profcheck "target / achieved" unchanged) →
  // reusable for an inter-scan comparison ("scan #1 / scan #2"), without breaking the profcheck usage.
  const { t } = useTranslation();
  const [sortKey, setSortKey] = useState('de2000');
  const [sortDir, setSortDir] = useState('desc');

  const sorted = useMemo(() => {
    if (!patches) return [];
    const fn = _SORTERS[sortKey] || _SORTERS.de2000;
    const arr = [...patches].sort((a, b) => fn(a) - fn(b));
    return sortDir === 'desc' ? arr.reverse() : arr;
  }, [patches, sortKey, sortDir]);

  if (!open) return null;

  const setSort = (k) => {
    if (k === sortKey) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    else { setSortKey(k); setSortDir(k === 'de2000' ? 'desc' : 'asc'); }
  };

  const exportCsv = () => {
    saveFile(`patches_${(title || 'profil').replace(/[^\w.-]+/g, '_')}.csv`, _toCsv(sorted), 'text/csv;charset=utf-8');
  };

  const SortHead = ({ k, label, align = 'left' }) => (
    <th className={`px-2 py-1.5 font-semibold text-text-muted cursor-pointer select-none
                    hover:text-text-strong text-${align}`}
        onClick={() => setSort(k)}>
      <span className="inline-flex items-center gap-1">
        {label}
        {sortKey === k && (sortDir === 'desc' ? <ArrowDown size={11}/> : <ArrowUp size={11}/>)}
      </span>
    </th>
  );

  return createPortal(
    <div className="fixed inset-0 z-[110] bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4"
         onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}>
      <div className="bg-bg border border-border-soft rounded-[14px] shadow-2xl w-[720px] max-w-[96vw] max-h-[90vh] flex flex-col overflow-hidden">
        <div className="px-6 pt-5 pb-3 flex items-center gap-3 border-b border-border-soft">
          <div className="flex-1 min-w-0">
            <h2 className="text-[15px] font-semibold text-text-strong truncate">
              {titleLabel || t('profils.check_all_title', { n: patches?.length || 0 })}
            </h2>
            <div className="text-tiny text-text-faint truncate">{title}</div>
          </div>
          <button type="button" onClick={exportCsv}
                  className="text-xs2 font-semibold inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border-soft text-text-strong hover:bg-sunken">
            <Download size={14}/> {t('profils.check_all_export')}
          </button>
          <button type="button" onClick={onClose} aria-label={t('common.close')}
                  className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-strong hover:bg-sunken">
            <X size={16}/>
          </button>
        </div>

        <div className="overflow-y-auto">
          <table className="w-full text-xs2 border-collapse">
            <thead className="sticky top-0 bg-bg border-b border-border-soft">
              <tr>
                <SortHead k="id" label={t('profils.check_all_id')}/>
                <SortHead k="de2000" label="ΔE₀₀"/>
                <th className="px-2 py-1.5"/>
                <SortHead k="rgb" label={t('profils.check_all_rgb')}/>
                <th className="px-2 py-1.5 font-semibold text-text-muted text-left">{labTargetLabel || t('profils.check_all_lab_target')}</th>
                <th className="px-2 py-1.5 font-semibold text-text-muted text-left">{labAchievedLabel || t('profils.check_all_lab_achieved')}</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {sorted.map((p) => (
                <tr key={p.id} className="border-b border-border-soft/40 hover:bg-sunken/50">
                  <td className="px-2 py-1 text-text-muted">{p.id}</td>
                  <td className="px-2 py-1 text-text-strong">{p.de2000.toFixed(3)}</td>
                  <td className="px-2 py-1">
                    <span className="inline-block w-4 h-4 rounded-sm border border-border-soft align-middle"
                          style={{ background: _rgbCss(p.rgb) }}/>
                  </td>
                  <td className="px-2 py-1 text-text-muted">{p.rgb.join(' ')}</td>
                  <td className="px-2 py-1 text-text-muted">{p.lab_target.map((v) => v.toFixed(1)).join(' ')}</td>
                  <td className="px-2 py-1 text-text-muted">{p.lab_achieved.map((v) => v.toFixed(1)).join(' ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="px-6 py-3 border-t border-border-soft flex justify-end">
          <button type="button" onClick={onClose}
                  className="text-xs2 font-semibold bg-accent text-white px-4 py-1.5 rounded-md hover:bg-accent/90">{t('common.close')}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
