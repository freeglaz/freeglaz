import { useState, useMemo, useEffect, useRef, Fragment } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { saveFile } from '../../lib/fileIO.js';
import { X, Download, ArrowUp, ArrowDown, ChevronRight, ChevronDown, Loader2 } from 'lucide-react';
import * as api from '../../api/client.js';
import Badge from '../ui/Badge.jsx';
import MeasureQcSummary from './MeasureQcSummary.jsx';
import ExclusionDialog from './ExclusionDialog.jsx';
import { classifyExclusionError, reasonLabel, riskView } from '../../lib/exclusion.js';
import {
  FILTERS, buildCsv, classifyPatchesError, filterCounts, filterRows, labSwatch, mergeQc, qcPanelState, reasonMessages, sortRows,
} from '../../lib/measureQc.js';

// View the measurements of ONE scan (>=1) — DECOUPLED from the inter-scan comparison (QC >=2).
// id + RGB device + Lab, sortable, CSV export (the user's data) — data via /scans/{ti3}/patches.
// + INTRINSIC QC (advisory): each reading compared with the prediction from the OTHER colours of
// the chart, via /scans/{ti3}/qc (cached server-side). The QC never excludes anything: the USER
// may exclude / re-include a reading (explicit API, dry run + confirmation in ExclusionDialog).
// The QC status and the use state (included / excluded) are shown separately.
const _rgbCss = (rgb) => `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
const _lab = (v) => (Array.isArray(v) && v.every((x) => typeof x === 'number') ? v.map((x) => x.toFixed(1)).join(' ') : '—');
const _n = (v, nd = 1) => (typeof v === 'number' && Number.isFinite(v) ? v.toFixed(nd) : '—');

function SortHead({ k, label, align = 'left', title, sortKey, sortDir, onSort }) {
  return (
    <th className={`px-2 py-1.5 font-semibold text-text-muted cursor-pointer select-none hover:text-text-strong text-${align}`}
        onClick={() => onSort(k)} title={title}>
      <span className="inline-flex items-center gap-1">
        {label}{sortKey === k && k !== 'suspicion' && (sortDir === 'desc' ? <ArrowDown size={11}/> : <ArrowUp size={11}/>)}
      </span>
    </th>
  );
}

function Seg({ active, onClick, children, title }) {
  return (
    <button type="button" onClick={onClick} title={title}
            className={`px-2 py-0.5 text-tiny transition-colors ${active ? 'bg-accent text-on-accent' : 'text-text-muted hover:bg-sunken'}`}>
      {children}
    </button>
  );
}

function StatusCell({ qc, t }) {
  if (!qc) return <span className="text-text-faint">—</span>;
  const title = t(`measure_qc.status_title.${qc.status}`);
  if (qc.status === 'strong') return <span title={title}><Badge kind="warn">{t('measure_qc.status.strong')}</Badge></span>;
  if (qc.status === 'to_check') return <span title={title}><Badge kind="neutral">{t('measure_qc.status.to_check')}</Badge></span>;
  if (qc.status === 'not_assessable') {
    return <span title={title}><Badge kind="neutral" className="border-dashed border-text-faint/40 italic">
      {t('measure_qc.status.not_assessable')}</Badge></span>;
  }
  return <span className="text-text-faint" title={title}>{t('measure_qc.status.coherent')}</span>;
}

function Swatches({ row, t }) {
  const m = labSwatch(row.lab);
  const p = labSwatch(row.qc?.lab_pred);
  const title = t('measure_qc.table.swatch_cell', { measured: _lab(row.lab), predicted: _lab(row.qc?.lab_pred) })
    + ((m?.outOfSrgb || p?.outOfSrgb) ? ` — ${t('measure_qc.table.out_of_srgb')}` : '');
  const sq = (s) => (s ? <span className="inline-block w-3.5 h-4 border border-border-soft align-middle"
                               style={{ background: s.css }}/> : <span className="inline-block w-3.5 h-4 align-middle"/>);
  return (
    <span className="inline-flex items-center gap-0.5" title={title}>
      {sq(m)}{sq(p)}{(m?.outOfSrgb || p?.outOfSrgb) && <span className="text-text-faint text-tiny ml-0.5">≈</span>}
    </span>
  );
}

function reliabilityLabel(qc, t) {
  if (!qc) return { text: '—', title: '' };
  const rel = qc.reliability || {};
  if (rel.assessable) {
    return { text: t('measure_qc.reliability.short_ok'),
             title: t('measure_qc.reliability.ok', { model: t(`measure_qc.model.${rel.model}`) }) };
  }
  const issues = (rel.issues || []).map((c) => t(`measure_qc.reason.${c}`, { defaultValue: c })).join(' · ');
  return { text: t('measure_qc.reliability.weak'), title: issues };
}

function UseCell({ row, t }) {
  if (!row.excluded) return <span className="text-text-faint">{t('exclusion.use.included')}</span>;
  const r = reasonLabel(row.exclusion_reason);
  const why = r ? [r.key ? t(r.key) : '', r.text].filter(Boolean).join(' — ') : '';
  return <span title={why || t('exclusion.use.excluded_title')}><Badge kind="neutral" className="line-through decoration-text-faint">{t('exclusion.use.excluded')}</Badge></span>;
}

function RiskCell({ row, t }) {
  const v = riskView(row.exclusion_risk);
  if (!v) return <span className="text-text-faint">—</span>;
  const cls = v.level === 'refused' ? 'text-danger' : (v.level === 'strong_warning' || v.level === 'warning') ? 'text-icc-warn' : 'text-text-faint';
  return <span className={cls} title={t(`exclusion.risk_title.${v.level}`)}>{t(v.key)}</span>;
}

function DetailRow({ row, t, colSpan }) {
  const q = row.qc;
  const rel = q.reliability || {};
  const reasons = reasonMessages(q.reasons);
  const dl = [
    [t('measure_qc.detail.lab_measured'), _lab(q.lab)],
    [t('measure_qc.detail.lab_predicted'), _lab(q.lab_pred)],
    [t('measure_qc.detail.de00'), _n(q.de00, 2)],
    [t('measure_qc.detail.score'), _n(q.score, 1)],
    [t('measure_qc.detail.position'), t(`measure_qc.position.${q.position}`)],
    [t('measure_qc.detail.neighbors'), t('measure_qc.detail.neighbors_value', {
      n: q.n_neighbors, model: t(`measure_qc.model.${rel.model}`), neff: _n(rel.neff, 1) })],
    [t('measure_qc.detail.reliability'), `${rel.assessable ? t('measure_qc.reliability.assessable') : t('measure_qc.reliability.not_assessable')}`
      + ` (v ${_n(rel.v, 2)} · s ${_n(rel.s_loc, 2)})`],
  ];
  if (q.duplicates) {
    dl.push([t('measure_qc.detail.duplicates'), t('measure_qc.detail.duplicates_value', {
      n: q.duplicates.n, ids: q.duplicates.sample_ids.join(', '), dis: _n(q.duplicates.max_disagreement, 2) })
      + (q.duplicates.identical_values ? ` — ${t('measure_qc.detail.identical_values')}` : '')]);
  }
  return (
    <tr className="bg-sunken/40 border-b border-border-soft/40">
      <td colSpan={colSpan} className="px-4 py-2 font-sans">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-tiny">
          {dl.map(([k, v]) => (<Fragment key={k}><dt className="text-text-faint">{k}</dt><dd className="text-text-strong">{v}</dd></Fragment>))}
          <dt className="text-text-faint">{t('measure_qc.detail.reasons')}</dt>
          <dd className="text-text-strong">
            {reasons.length === 0 ? t('measure_qc.detail.no_reason')
              : reasons.map((r, i) => <div key={i}>{r.key ? t(r.key, r.params) : r.code}</div>)}
          </dd>
        </dl>
      </td>
    </tr>
  );
}

export default function MeasurementsModal({ open, chartId, ti3, onClose, onChanged }) {
  const { t } = useTranslation();
  const key = open && chartId && ti3 ? `${chartId}/${ti3}` : null;
  // Results are stored WITH the key they belong to → no synchronous reset in the effect.
  const [patchesRes, setPatchesRes] = useState({ key: null, data: null, err: null });
  const [qcRes, setQcRes] = useState({ key: null, report: null, error: null });
  const [sortKey, setSortKey] = useState('suspicion');
  const [sortDir, setSortDir] = useState('asc');
  const [filter, setFilter] = useState('all');
  const [expanded, setExpanded] = useState(null);
  const [version, setVersion] = useState(0);          // bumped after an exclusion → refetch the measurements only
  const [dialog, setDialog] = useState(null);         // {sid, rgb} of the reading being excluded
  const [pending, setPending] = useState(() => new Set());   // re-inclusions in flight (per reading)
  const [opError, setOpError] = useState(null);
  const openRef = useRef(true);
  useEffect(() => { openRef.current = !!key; return () => { openRef.current = false; }; }, [key]);

  useEffect(() => {
    if (!key) return;
    let alive = true;
    api.getScanPatches(chartId, ti3)
      .then((d) => alive && setPatchesRes({ key, data: d, err: null }))   // previous data kept until then
      .catch((e) => {
        // Technical detail for diagnosis (also in freeglaz.log server-side); the UI shows a localized message.
        console.warn('[measures] reading failed', ti3, e?.status, e?.detail ?? e?.message);
        if (alive) setPatchesRes({ key, data: null, err: classifyPatchesError(e) });
      });
    return () => { alive = false; };
  }, [key, chartId, ti3, version]);

  useEffect(() => {
    if (!key) return;
    let alive = true;
    api.getScanMeasureQC(chartId, ti3)
      .then((r) => alive && setQcRes({ key, report: r.report, error: null }))
      .catch((e) => alive && setQcRes({ key, report: null, error: e || new Error('qc') }));
    return () => { alive = false; };
  }, [key, chartId, ti3]);

  // Local refresh only while open; the parent (build inputs, preview) is told on every server
  // answer, open or not (phase 4E).
  const afterChange = () => { if (openRef.current) setVersion((v) => v + 1); onChanged?.(); };
  const reinclude = (sid) => {
    if (pending.has(sid)) return;                      // double click: ignored
    setOpError(null);
    setPending((s) => new Set(s).add(sid));
    api.setReadingExclusion(chartId, ti3, sid, { excluded: false })
      .then(() => afterChange())
      .catch((e) => { if (openRef.current) { setOpError(classifyExclusionError(e)); setVersion((v) => v + 1); } })
      .finally(() => { if (openRef.current) setPending((s) => { const n = new Set(s); n.delete(sid); return n; }); });
  };

  const data = patchesRes.key === key ? patchesRes.data : null;
  const err = patchesRes.key === key ? patchesRes.err : null;
  const qcLoading = qcRes.key !== key;
  const report = qcLoading ? null : qcRes.report;
  const qcState = qcPanelState({ loading: qcLoading, report, error: qcLoading ? null : qcRes.error });
  const withQc = !!report;

  const rows = useMemo(() => mergeQc(data?.patches, report), [data, report]);
  const counts = useMemo(() => filterCounts(rows), [rows]);
  const effectiveSort = sortKey === 'suspicion' && !withQc ? 'id' : sortKey;
  const allSorted = useMemo(() => sortRows(rows, effectiveSort, sortDir), [rows, effectiveSort, sortDir]);
  const visible = useMemo(() => filterRows(allSorted, filter), [allSorted, filter]);

  if (!open) return null;

  const setSort = (k) => {
    if (k === sortKey) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    else { setSortKey(k); setSortDir(k === 'de00' ? 'desc' : 'asc'); }
  };
  const exportCsv = () => {
    if (!data) return;
    // ALL rows (not only the filtered ones), in the current order, + QC columns when available.
    saveFile(`mesures_${ti3.replace(/\.ti3$/, '')}.csv`, buildCsv(allSorted, withQc), 'text/csv;charset=utf-8');
  };
  const editable = data?.scan_role === 'included';
  const nCols = withQc ? 13 : 7;

  return createPortal(
    <div className="fixed inset-0 z-[110] bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4"
         onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}>
      <div className={`bg-bg border border-border-soft rounded-[14px] shadow-2xl ${withQc ? 'w-[1280px]' : 'w-[820px]'} max-w-[97vw] max-h-[90vh] flex flex-col overflow-hidden`}>
        <div className="px-6 pt-5 pb-3 flex items-center gap-3 border-b border-border-soft">
          <div className="flex-1 min-w-0">
            <h2 className="text-[15px] font-semibold text-text-strong truncate">
              {data ? t('scan.measures.title', { count: data.n_patches }) : t('scan.measures.title_plain')}
            </h2>
            <div className="text-tiny text-text-faint font-mono truncate">{ti3}</div>
          </div>
          {data && (
            <button type="button" onClick={exportCsv} title={t('measure_qc.export_title')}
                    className="text-xs2 font-semibold inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border-soft text-text-strong hover:bg-sunken">
              <Download size={14}/> {t('profils.check_all_export')}
            </button>
          )}
          <button type="button" onClick={onClose} aria-label={t('common.close')}
                  className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-strong hover:bg-sunken">
            <X size={16}/>
          </button>
        </div>
        <div className="px-6 pt-3 space-y-2">
          <MeasureQcSummary state={qcState} measurementsShown={rows.length > 0}/>
          {err && (
            <div role="alert" data-measures-error={err.code || 'unknown'}
                 className="text-xs2 text-danger bg-danger/10 border border-danger/30 rounded-md px-3 py-2">{t(err.key)}</div>
          )}
          {!data && !err && <div className="text-xs2 text-text-faint py-2">{t('scan.measures.loading')}</div>}
          {opError && (
            <div role="alert" className="text-xs2 text-danger bg-danger/10 border border-danger/30 rounded-md px-3 py-2">
              {t(`exclusion.error.${opError.kind}`)}
            </div>
          )}
          {data && !editable && <div className="text-tiny text-text-faint">{t('exclusion.scan_excluded_hint')}</div>}
          {data && (
            <div className="flex flex-wrap items-center gap-3 pb-1">
              {withQc && (
                <span className="inline-flex rounded-md border border-border-soft overflow-hidden">
                  <Seg active={effectiveSort === 'suspicion'} onClick={() => setSortKey('suspicion')}>{t('measure_qc.sort.suspicion')}</Seg>
                  <Seg active={effectiveSort === 'id'} onClick={() => { setSortKey('id'); setSortDir('asc'); }}>{t('measure_qc.sort.natural')}</Seg>
                </span>
              )}
              <span className="inline-flex rounded-md border border-border-soft overflow-hidden">
                {FILTERS.map((f) => (
                  <Seg key={f} active={filter === f} onClick={() => setFilter(f)} title={t(`measure_qc.filter.${f}_title`)}>
                    {t(`measure_qc.filter.${f}`, { n: counts[f] })}
                  </Seg>
                ))}
              </span>
              {filter !== 'all' && visible.length === 0 && (
                <span className="text-tiny text-text-faint">{t('measure_qc.filter.empty')}</span>
              )}
            </div>
          )}
        </div>
        {data && (
          <div className="overflow-auto">
            <table className="table">
              <thead className="sticky top-0 bg-bg">
                <tr>
                  <SortHead sortKey={effectiveSort} sortDir={sortDir} onSort={setSort} k="id" label={t('profils.check_all_id')}/>
                  {withQc && <SortHead sortKey={effectiveSort} sortDir={sortDir} onSort={setSort} k="suspicion" label={t('measure_qc.table.status')}/>}
                  <th className="px-2 py-1.5 font-semibold text-text-muted text-left" title={t('exclusion.table.use_title')}>{t('exclusion.table.use')}</th>
                  <th className="px-2 py-1.5" title={t('measure_qc.table.device_swatch_title')}/>
                  {withQc && <th className="px-2 py-1.5 font-semibold text-text-muted text-left" title={t('measure_qc.table.swatch_title')}>{t('measure_qc.table.swatch')}</th>}
                  <SortHead sortKey={effectiveSort} sortDir={sortDir} onSort={setSort} k="rgb" label={t('profils.check_all_rgb')}/>
                  <SortHead sortKey={effectiveSort} sortDir={sortDir} onSort={setSort} k="labL" label="Lab"/>
                  {withQc && <SortHead sortKey={effectiveSort} sortDir={sortDir} onSort={setSort} k="de00" label={t('measure_qc.table.de00')} align="right" title={t('measure_qc.table.de00_title')}/>}
                  {withQc && <th className="px-2 py-1.5 font-semibold text-text-muted text-left">{t('measure_qc.table.position')}</th>}
                  {withQc && <th className="px-2 py-1.5 font-semibold text-text-muted text-left">{t('measure_qc.table.reason')}</th>}
                  {withQc && <th className="px-2 py-1.5 font-semibold text-text-muted text-left" title={t('measure_qc.table.reliability_title')}>{t('measure_qc.table.reliability')}</th>}
                  <th className="px-2 py-1.5 font-semibold text-text-muted text-left" title={t('exclusion.table.risk_title')}>{t('exclusion.table.risk')}</th>
                  <th className="px-2 py-1.5"/>
                </tr>
              </thead>
              <tbody className="font-mono">
                {visible.map((p) => {
                  const reasons = withQc ? reasonMessages(p.qc?.reasons) : [];
                  const first = reasons[0];
                  const rel = withQc ? reliabilityLabel(p.qc, t) : null;
                  const isOpen = expanded === p.id;
                  return (
                    <Fragment key={p.id}>
                      <tr className={`border-b border-border-soft/40 hover:bg-sunken/50 ${withQc && p.qc ? 'cursor-pointer' : ''}`}
                          onClick={() => withQc && p.qc && setExpanded(isOpen ? null : p.id)}>
                        <td className="px-2 py-1 text-text-muted whitespace-nowrap">
                          {withQc && p.qc && (isOpen ? <ChevronDown size={11} className="inline -mt-0.5"/> : <ChevronRight size={11} className="inline -mt-0.5"/>)}
                          {p.id}
                        </td>
                        {withQc && <td className="px-2 py-1 font-sans"><StatusCell qc={p.qc} t={t}/></td>}
                        <td className="px-2 py-1 font-sans whitespace-nowrap" data-use={p.excluded ? 'excluded' : 'included'}><UseCell row={p} t={t}/></td>
                        <td className="px-2 py-1">
                          <span className="inline-block w-4 h-4 rounded-sm border border-border-soft align-middle"
                                style={{ background: _rgbCss(p.rgb) }}/>
                        </td>
                        {withQc && <td className="px-2 py-1"><Swatches row={p} t={t}/></td>}
                        <td className="px-2 py-1 text-text-muted">{p.rgb.join(' ')}</td>
                        <td className="px-2 py-1 text-text-muted">{p.lab.map((v) => v.toFixed(1)).join(' ')}</td>
                        {withQc && <td className="px-2 py-1 text-right text-text-muted tabular-nums">{_n(p.qc?.de00, 1)}</td>}
                        {withQc && <td className="px-2 py-1 font-sans text-text-muted">{p.qc ? t(`measure_qc.position.${p.qc.position}`) : '—'}</td>}
                        {withQc && (
                          <td className="px-2 py-1 font-sans text-text-muted max-w-[220px] truncate"
                              title={reasons.map((r) => (r.key ? t(r.key, r.params) : r.code)).join('\n')}>
                            {first ? (first.key ? t(first.key, first.params) : first.code) : ''}
                          </td>
                        )}
                        {withQc && <td className="px-2 py-1 font-sans text-text-muted whitespace-nowrap" title={rel.title}>{rel.text}</td>}
                        <td className="px-2 py-1 font-sans whitespace-nowrap"><RiskCell row={p} t={t}/></td>
                        <td className="px-2 py-1 font-sans whitespace-nowrap text-right" onClick={(e) => e.stopPropagation()}>
                          {p.excluded ? (
                            <button type="button" disabled={!editable || pending.has(p.id)} onClick={() => reinclude(p.id)}
                                    data-action="reinclude" title={editable ? t('exclusion.reinclude_title') : t('exclusion.scan_excluded_hint')}
                                    className="text-tiny font-semibold px-2 py-0.5 rounded border border-border-soft text-text-strong hover:bg-sunken disabled:opacity-50 inline-flex items-center gap-1">
                              {pending.has(p.id) && <Loader2 size={11} className="animate-spin"/>}{t('exclusion.reinclude')}
                            </button>
                          ) : (
                            <button type="button" disabled={!editable || !!dialog} onClick={() => setDialog({ sid: p.id, rgb: p.rgb })}
                                    data-action="exclude" title={editable ? t('exclusion.exclude_title') : t('exclusion.scan_excluded_hint')}
                                    className="text-tiny font-semibold px-2 py-0.5 rounded border border-border-soft text-text-muted hover:text-text-strong hover:bg-sunken disabled:opacity-50">
                              {t('exclusion.exclude')}
                            </button>
                          )}
                        </td>
                      </tr>
                      {isOpen && p.qc && <DetailRow row={p} t={t} colSpan={nCols}/>}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {dialog && (
          <ExclusionDialog key={`${ti3}/${dialog.sid}`} chartId={chartId} ti3={ti3} sid={dialog.sid} rgb={dialog.rgb}
                           onClose={() => setDialog(null)}
                           onSettled={() => onChanged?.()}
                           onDone={() => { setDialog(null); setOpError(null); setVersion((v) => v + 1); }}/>
        )}
        <div className="px-6 py-3 border-t border-border-soft flex justify-end">
          <button type="button" onClick={onClose}
                  className="text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press">{t('common.close')}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
