import { useEffect, useState, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { ScanLine, FileSearch, Search, X, ChevronRight, ChevronDown, Archive, Trash2, CheckCircle2 } from 'lucide-react';
import * as api from '../../api/client.js';
import ConfirmModal from '../ui/ConfirmModal.jsx';
import ChartCard from '../Charts/ChartCard.jsx';
import { geLabel } from '../../lib/geLabel.js';
import ScanDeltaCard from '../Charts/ScanDeltaCard.jsx';
import ScanKeepList from '../Charts/ScanKeepList.jsx';
import QCTableModal from '../Charts/QCTableModal.jsx';
import ProfileBuildPanel from '../Charts/ProfileBuildPanel.jsx';
import MeasurementsModal from '../Charts/MeasurementsModal.jsx';
import ProfileInspectorModal from '../ProfileInspector/ProfileInspectorModal.jsx';
import ArgyllProfileWizard from '../Charts/ArgyllProfileWizard.jsx';

/**
 * "Measurements" tab — MANAGEMENT layer, paper-agnostic, always available.
 * Pure consultation (A3 scans + A1 ΔE concordance + derived profile even orphan A4);
 * actions DELEGATE to the scan wizard (which holds the `isLoaded` lock). Reuses
 * ChartCard / ScanDeltaCard / QCTableModal (rejection per patch × scan) / ProfileInspectorModal.
 */
// Groups charts by PAPER (paper_media_id) — header = readable paper name + secondary
// mediaID. Charts sorted by date desc within the group; groups by the most recent chart.
function _groupByPaper(charts) {
  const map = new Map();
  for (const c of charts) {
    const id = c.paper_media_id || c.paper || 'unknown';
    if (!map.has(id)) map.set(id, { mediaId: id, name: c.paper || id, charts: [] });
    map.get(id).charts.push(c);
  }
  const groups = [...map.values()];
  for (const g of groups) {
    g.charts.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
    // GE subgroups (first-order dimension): "GE ON" (gloss_enhancer FULLPAGE) then
    // "GE OFF" (everything else, default). Presentation via geLabel; internal values intact.
    g.subgroups = [
      { key: 'on', ge: 'FULLPAGE', charts: g.charts.filter((c) => c.gloss_enhancer === 'FULLPAGE') },
      { key: 'off', ge: 'OFF', charts: g.charts.filter((c) => c.gloss_enhancer !== 'FULLPAGE') },
    ].filter((s) => s.charts.length > 0);
  }
  groups.sort((a, b) => (b.charts[0]?.created_at || '').localeCompare(a.charts[0]?.created_at || ''));
  return groups;
}

// Binary scanned / not-scanned filter — based on n_scans (raw, _raw_ti3_paths), SAME criterion
// as the "N scans" count shown in the row (badge↔filter consistency). NOT c.scanned, which
// also counts the _avg/_qcfilt/_multisource derivatives (sol_chart) → could say "scanned"
// with n_scans==0. The stage badge (profiled/printed/created) is removed: the count is enough.
function _scanKey(c) {
  return (c.n_scans ?? 0) > 0 ? 'scanned' : 'unscanned';
}
const _SCAN_FILTERS = ['scanned', 'unscanned'];

export default function MesuresPage({ offline }) {
  const { t } = useTranslation();
  const [charts, setCharts] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);   // chart_id of the detail
  const [detail, setDetail] = useState(null);        // {identity, scans, profile}
  const [delta, setDelta] = useState(null);          // concordance (lazy, ≥2 scans)
  const [allOpen, setAllOpen] = useState(false);
  const [inspect, setInspect] = useState(null);      // {path} | null
  const [scanWizard, setScanWizard] = useState(null);// {paper, ge} | null
  const [busy, setBusy] = useState(false);
  const [buildPhase, setBuildPhase] = useState(null);// preparing|building during a profile build
  const [buildResult, setBuildResult] = useState(null);// result of the last build (success) → completion message
  const [conflict, setConflict] = useState(null);            // collision (409) → 3-choice pop-up (backend detail)
  const [nameNotice, setNameNotice] = useState(null);        // ASCII/length only (UNDER the field)
  // Build controls (panel SHARED with the wizard): base + colprof flags + presets + help.
  const [profileBase, setProfileBase] = useState('average');
  // Multi-source sources selected in the panel (current chart implicit).
  const [extraSources, setExtraSources] = useState({ extra_chart_ids: [], source_profiles: [] });
  const [colprofFlags, setColprofFlags] = useState('');
  const [colprofPresets, setColprofPresets] = useState([]);
  const [colprofHelp, setColprofHelp] = useState(null);
  const [showColprofHelp, setShowColprofHelp] = useState(false);
  const [colprofPresetKey, setColprofPresetKey] = useState('');
  const [openGroups, setOpenGroups] = useState(null);// Set(expanded mediaId) | null = default
  const [viewTi3, setViewTi3] = useState(null);      // ti3 shown in the single-scan viewer
  const [confirm, setConfirm] = useState(null);      // {type:'chart'|'lighten'|'scan', ti3?} | null
  // Sidebar filters (Papers pattern) on REAL fields: search (chart_id/paper)
  // + stage + PAPER (checkboxes). NB: the paper filter is a VISIBILITY
  // (show/hide), distinct from the list's per-paper grouping (which stays) —
  // grouping ≠ filtering.
  const [search, setSearch] = useState('');
  const [stages, setStages] = useState([]);          // [] = all stages
  const [paperIds, setPaperIds] = useState([]);      // [] = all papers
  const hasActiveFilters = search.trim() !== '' || stages.length > 0 || paperIds.length > 0;

  // List of papers ACTUALLY present (same key as _groupByPaper: mediaId+name).
  // Dynamic: reflects the library, not a fixed list.
  const paperOptions = useMemo(() => {
    const map = new Map();
    for (const c of charts || []) {
      const mid = c.paper_media_id || c.paper || 'unknown';
      if (!map.has(mid)) map.set(mid, { mediaId: mid, name: c.paper || mid, count: 0 });
      map.get(mid).count += 1;
    }
    return [...map.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [charts]);

  // Filtering BEFORE grouping (real fields only).
  const filtered = useMemo(() => {
    let list = charts || [];
    const q = search.trim().toLowerCase();
    if (q) list = list.filter((c) =>
      (c.chart_id || '').toLowerCase().includes(q) || (c.paper || '').toLowerCase().includes(q));
    if (stages.length) list = list.filter((c) => stages.includes(_scanKey(c)));
    if (paperIds.length) list = list.filter((c) => paperIds.includes(c.paper_media_id || c.paper || 'unknown'));
    return list;
  }, [charts, search, stages, paperIds]);

  // Grouping by paper (expanders). Default: all expanded if ≤3 groups, otherwise 1st only.
  const groups = useMemo(() => _groupByPaper(filtered), [filtered]);
  const defaultOpen = useMemo(
    () => new Set(groups.length <= 3 ? groups.map((g) => g.mediaId) : groups.slice(0, 1).map((g) => g.mediaId)),
    [groups]);
  const effectiveOpen = openGroups ?? defaultOpen;
  const toggleGroup = (id) => setOpenGroups(() => {
    const next = new Set(effectiveOpen);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const reload = useCallback(() => {
    api.getCharts()
      .then((d) => setCharts(d.charts || []))
      .catch((e) => { setError(e.message); setCharts([]); });
  }, []);
  useEffect(() => { reload(); }, [reload]);

  // Detail (A3+A4) + LAZY concordance (A1) if ≥2 KEPT scans (recomputed after toggle).
  const loadDetail = useCallback((id) => {
    if (!id) { setDetail(null); setDelta(null); return; }
    return api.getChartDetail(id).then((d) => {
      setDetail(d);
      const nKept = (d.scans || []).filter((s) => s.kept).length;
      if (nKept >= 2) return api.getScanDelta(id).then(setDelta).catch(() => setDelta(null));
      setDelta(null);
    }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { setDetail(null); setDelta(null); setError(null); setBuildResult(null); loadDetail(selected); }, [selected, loadDetail]);

  // Software mutation: include/exclude a scan → recomputes concordance + detail (no Z9).
  const onSetRole = (ti3, role) => {
    if (!selected || busy) return;
    setBusy(true); setError(null);
    api.setScanRole(selected, ti3, role)
      .then(() => loadDetail(selected))
      .catch((e) => setError(e?.message || t('scan.toggle_failed')))
      .finally(() => setBusy(false));
  };

  // colprof presets/flags (agreed default prefilled, resolved backend-side) — SAME source as the
  // wizard (getChartColprofPresets) → zero recipe divergence.
  useEffect(() => {
    api.getChartColprofPresets()
      .then((d) => { setColprofPresets(d.presets || []); setColprofFlags(d.default_flags || '-v -qh'); })
      .catch(() => setColprofFlags('-v -qh'));
  }, []);
  const loadColprofHelp = () => {
    setShowColprofHelp((v) => !v);
    if (colprofHelp == null) {
      api.getChartColprofHelp().then((d) => setColprofHelp(d.help)).catch(() => setColprofHelp('—'));
    }
  };
  const saveColprofPreset = () => {
    if (!colprofPresetKey.trim() || !colprofFlags.trim()) return;
    api.saveChartColprofPreset({ key: colprofPresetKey.trim(), flags: colprofFlags.trim(), description: '' })
      .then((d) => { setColprofPresets(d.presets || colprofPresets); setColprofPresetKey(''); })
      .catch((e) => setError(e?.message || 'preset'));
  };

  // EXPLICIT (re)build of the profile on the current INCLUDED set (excluding rejects, never auto).
  // 100% SOFTWARE (colprof ~2.5 min) as a BACKGROUND JOB — NO Z9, NO wizard. Builds if it
  // doesn't exist, rebuilds to include changes. Base + flags = panel (visible).
  // onConflict: null (default, collision → pop-up) | 'replace' | 'keep_both' (replay from the pop-up).
  const buildProfile = (onConflict = null) => {
    if (!selected || busy || !detail) return;
    const nKept = (detail.scans || []).filter((s) => s.kept).length;
    const body = { profile_base: nKept >= 2 ? profileBase : 'last' };
    if (colprofFlags.trim()) body.colprof_flags = colprofFlags.trim();
    if (extraSources.extra_chart_ids.length) body.extra_chart_ids = extraSources.extra_chart_ids;
    if (extraSources.source_profiles.length) body.source_profiles = extraSources.source_profiles;
    if (extraSources.name) body.name = extraSources.name;     // custom name (null if auto default)
    // TYPE guard: on_conflict is only valid as a collision choice
    // (string 'replace'/'keep_both'). Without this, the build button wired onClick={onBuild}
    // passes the click's SyntheticEvent (circular refs) → JSON.stringify(body) broke.
    if (typeof onConflict === 'string') body.on_conflict = onConflict;  // replay after pop-up choice
    setBusy(true); setError(null); setBuildResult(null);
    setConflict(null); setNameNotice(null); setBuildPhase('preparing');
    api.buildProfileAndWait(selected, body, setBuildPhase)
      .then((result) => { setBuildResult(result || null); loadDetail(selected); reload(); })
      .catch((e) => {
        if (e?.status === 409 && e.detail?.error === 'name_conflict') {  // collision → 3-choice pop-up
          setConflict(e.detail);
        } else if (e?.status === 422) {                        // length (Pydantic) → UNDER the field
          setNameNotice(t('mesures.profile_name_maxlen'));
        } else if (e?.status === 400) {                        // invalid name (chars) → backend detail UNDER the field
          setNameNotice(e.message);
        } else { setError(e?.message || t('mesures.rebuild_failed')); }
      })
      .finally(() => { setBusy(false); setBuildPhase(null); });
  };

  // Re-attach: a build launched then reload → we recover the RUNNING job (no stuck button).
  useEffect(() => {
    if (!selected) return undefined;
    let cancelled = false;
    api.getProfileStatus(selected).then((st) => {
      if (cancelled || st.state !== 'running') return;
      setBusy(true); setBuildPhase(st.phase || 'building');
      api.pollProfileBuild(selected, setBuildPhase)
        .then((result) => { if (!cancelled) { setBuildResult(result || null); loadDetail(selected); reload(); } })
        .catch((e) => { if (!cancelled) setError(e?.message || t('mesures.rebuild_failed')); })
        .finally(() => { if (!cancelled) { setBusy(false); setBuildPhase(null); } });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [selected, loadDetail, reload, t]);

  // Action: DELEGATE to the scan wizard (it holds isLoaded; Measurements bypasses nothing).
  // Fix A: we ALSO pass the chart (chart) → the wizard opens DIRECTLY on
  // it (scanready phase), without an intermediate list, whatever its stage.
  const openScan = () => {
    if (!detail) return;
    setScanWizard({
      paper: { mediaid: detail.identity.paper_media_id, name: detail.identity.paper },
      // Real GE of the chart (was hardcoded `false` → scan screen always showed
      // "GE OFF" regardless of the chart). GE does not affect the scan itself, but
      // the displayed state must reflect the chart's true gloss_enhancer.
      ge: detail.identity.gloss_enhancer,
      chart: detail.identity,
    });
  };

  // Deletions (DESTRUCTIVE → confirmation required). Software, no Z9.
  // - chart: erases everything (TIFF+ti3+meta) → back to list; - lighten: erases the TIFF, keeps ti3;
  // - scan: erases ONE measurement set (ti3+cgats), irreversible → recompute detail.
  const runConfirm = () => {
    if (!confirm || !selected || busy) return;
    const { type, ti3 } = confirm;
    setBusy(true); setError(null);
    const act = type === 'chart' ? api.deleteChart(selected)
              : type === 'lighten' ? api.lightenChart(selected)
              : api.deleteScan(selected, ti3);
    act.then(() => {
      setConfirm(null);
      if (type === 'chart') { setSelected(null); reload(); }   // chart erased → list
      else { loadDetail(selected); reload(); }
    }).catch((e) => setError(e?.message || t('mesures.delete_failed')))
      .finally(() => setBusy(false));
  };

  // Current detail (right panel) — null-safe (the drawer is only rendered if detail).
  const id = detail?.identity;
  const prof = detail?.profile;
  const nIncluded = (detail?.scans || []).filter((s) => s.kept).length;   // scans feeding the profile

  // ─── Papers pattern: [filters sidebar] [full-width list] [detail = right drawer] ───
  return (
    <div className="flex-1 flex min-w-0">

      {/* Filters sidebar — REAL fields (chart_id/paper search + scans) */}
      <aside aria-label={t('mesures.filter_scans')}
             className="w-[260px] flex-shrink-0 border-r border-border-soft bg-surface flex flex-col">
        <div className="flex-1 overflow-y-auto no-scrollbar px-4 py-4 space-y-5">
          <div className="relative">
            <Search size={12} strokeWidth={2}
                    className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-faint pointer-events-none" aria-hidden="true"/>
            <input
              type="text" value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder={t('mesures.filter_search')} aria-label={t('mesures.filter_search')}
              className="w-full pl-7 pr-7 py-1.5 rounded-md bg-sunken border border-transparent focus:border-accent focus:bg-surface text-xs2 text-text-strong placeholder:text-text-faint outline-none transition-colors"/>
            {search && (
              <button type="button" onClick={() => setSearch('')} aria-label={t('common.close')}
                      className="absolute right-1 top-1/2 -translate-y-1/2 p-1 rounded text-text-faint hover:text-text-strong hover:bg-surface transition-colors">
                <X size={11} aria-hidden="true"/>
              </button>
            )}
          </div>
          <div>
            <div className="text-[11px] font-semibold tracking-[0.08em] uppercase text-text-muted mb-2">
              {t('mesures.filter_scans')}
            </div>
            <div className="space-y-1.5">
              {_SCAN_FILTERS.map((k) => {
                const on = stages.includes(k);
                const count = (charts || []).filter((c) => _scanKey(c) === k).length;
                const label = t(`mesures.scan_filter.${k}`);
                return (
                  <label key={k} className="flex items-center gap-2 cursor-pointer text-xs2 text-text-strong">
                    <input type="checkbox" checked={on} className="accent-accent"
                           onChange={() => setStages((prev) => on ? prev.filter((s) => s !== k) : [...prev, k])}/>
                    <span className="flex-1">{label}</span>
                    <span className="text-tiny text-text-faint font-mono tabular-nums">{count}</span>
                  </label>
                );
              })}
            </div>
          </div>
          {/* PAPER filter (checkboxes) — DYNAMIC (real papers); visibility, not
              grouping. Shown only if there are ≥2 papers (otherwise pointless). */}
          {paperOptions.length >= 2 && (
            <div>
              <div className="text-[11px] font-semibold tracking-[0.08em] uppercase text-text-muted mb-2">
                {t('mesures.filter_paper')}
              </div>
              <div className="space-y-1.5">
                {paperOptions.map((p) => {
                  const on = paperIds.includes(p.mediaId);
                  return (
                    <label key={p.mediaId} className="flex items-center gap-2 cursor-pointer text-xs2 text-text-strong">
                      <input type="checkbox" checked={on} className="accent-accent"
                             onChange={() => setPaperIds((prev) => on ? prev.filter((m) => m !== p.mediaId) : [...prev, p.mediaId])}/>
                      <span className="flex-1 truncate" title={p.name}>{p.name}</span>
                      <span className="text-tiny text-text-faint font-mono tabular-nums">{p.count}</span>
                    </label>
                  );
                })}
              </div>
            </div>
          )}
        </div>
        <div className="px-4 py-3 border-t border-border-soft">
          <button type="button" onClick={() => { setSearch(''); setStages([]); setPaperIds([]); }} disabled={!hasActiveFilters}
                  className="w-full px-3 py-1.5 rounded-md text-xs2 font-medium border border-border-strong text-text-strong hover:bg-sunken disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
            {t('mesures.filter_reset')}
          </button>
        </div>
      </aside>

      {/* Full-width list (grouped by paper) */}
      <main className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center px-[22px] h-[75px] border-b border-border-soft bg-surface sticky top-0 z-10">
          <div className="min-w-0">
            <h1 className="text-[15px] font-semibold text-text-strong leading-tight">{t('mesures.title')}</h1>
            <p className="text-tiny text-text-faint leading-tight mt-0.5 truncate">{t('mesures.intro')}</p>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-6 space-y-3">
          {error && <div className="text-xs2 text-danger bg-danger/10 rounded-md px-3 py-2">{error}</div>}
          {charts == null ? (
            <div className="state"><span className="text-sm">{t('mesures.loading')}</span></div>
          ) : charts.length === 0 ? (
            <div className="state">
              <span className="ic"><FileSearch size={22} aria-hidden="true"/></span>
              <span className="text-sm">{t('mesures.no_charts')}</span>
            </div>
          ) : filtered.length === 0 ? (
            <div className="state"><span className="text-sm">{t('mesures.no_results')}</span></div>
          ) : (
            groups.map((g) => {
              const open = effectiveOpen.has(g.mediaId);
              return (
                <div key={g.mediaId} className="space-y-1.5">
                  {/* Paper group header — readable name + secondary mediaID (technical) */}
                  <button type="button" onClick={() => toggleGroup(g.mediaId)}
                          className="w-full flex items-center gap-2 text-left px-1 py-1.5 rounded-md hover:bg-sunken/50">
                    {open ? <ChevronDown size={15} className="text-text-faint flex-shrink-0"/>
                          : <ChevronRight size={15} className="text-text-faint flex-shrink-0"/>}
                    <span className="font-semibold text-text-strong text-sm truncate">{g.name}</span>
                    <span className="font-mono text-tiny text-text-faint truncate">{g.mediaId}</span>
                    <span className="ml-auto text-tiny text-text-faint flex-shrink-0">
                      {t('mesures.group_count', { count: g.charts.length })}
                    </span>
                  </button>
                  {open && g.subgroups.map((sg) => (
                    <div key={sg.key} className="space-y-1.5">
                      {/* GE subgroup (first-order dimension) — clear label via geLabel */}
                      <div className="px-1 pt-0.5 text-tiny font-semibold uppercase tracking-wide text-text-faint">
                        {geLabel(sg.ge)}
                      </div>
                      {sg.charts.map((c) => (
                          <ChartCard key={c.chart_id} chart={c} onClick={() => setSelected(c.chart_id)}
                            selected={c.chart_id === selected}
                            info={<>
                              {c.patch_count != null && `${t('mesures.patches', { count: c.patch_count })} · `}
                              {t('mesures.scans_count', { count: c.n_scans ?? 0 })}
                              {c.created_at && ` · ${c.created_at.slice(0, 10)}`}
                            </>}
                            actions={<span className="text-text-faint text-xs2 pr-1">→</span>}/>
                      ))}
                    </div>
                  ))}
                </div>
              );
            })
          )}
        </div>
      </main>

      {/* Detail = right side panel (drawer, Papers/Profiles pattern) */}
      {selected && detail && (
        <>
          <div className="fixed inset-0 z-40 bg-black/10" onClick={() => setSelected(null)} aria-hidden="true"/>
          <aside role="complementary" aria-label={id.chart_id}
                 className="fixed top-12 right-0 bottom-0 z-50 w-[640px] bg-surface border-l border-border-soft shadow-2xl flex flex-col animate-slidein">
            <div className="px-6 pt-5 pb-4 border-b border-border-soft flex items-start gap-3 flex-shrink-0">
              <div className="flex-1 min-w-0">
                <h2 className="text-[15px] font-semibold text-text-strong font-mono break-all">{id.chart_id}</h2>
                <p className="text-tiny text-text-faint mt-0.5">
                  {[id.paper, geLabel(id.gloss_enhancer), id.format, id.patch_count != null && t('mesures.patches', { count: id.patch_count }), id.purpose,
                    id.printed_at && t('mesures.printed_at', { date: id.printed_at })].filter(Boolean).join(' · ')}
                </p>
              </div>
              <button type="button" onClick={() => setSelected(null)} title={t('common.close')}
                      className="w-8 h-8 rounded-md flex items-center justify-center text-text-muted hover:text-text-strong hover:bg-sunken transition-colors flex-shrink-0">
                <X size={15} strokeWidth={1.8}/>
              </button>
            </div>
            <div key={id.chart_id} className="flex-1 overflow-y-auto p-6 space-y-5 animate-fadein">

              {/* PARENT SECTION — Scans (list + KEEP/EXCLUDE toggle) AND repeatability grouped in ONE block.
                  STRONG header (text-[13px]/strong); no rule above (follows the drawer header). */}
              <section className="space-y-2">
                <div className="text-[13px] font-semibold uppercase tracking-wide text-text-strong">{t('mesures.section_scans', { count: detail.scans.length })}</div>
                {detail.scans.length === 0
                  ? <p className="text-xs2 text-text-faint">{t('mesures.no_scans')}</p>
                  : <ScanKeepList scans={detail.scans} onSetRole={onSetRole} disabled={busy}
                      onView={(ti3) => setViewTi3(ti3)}
                      onDelete={(ti3) => setConfirm({ type: 'scan', ti3 })}/>}
                {/* ΔE repeatability = SUB-section of Scans (CHILD header, weak style) */}
                {delta && (
                  <div className="space-y-1.5 pt-1">
                    <div className="text-tiny font-semibold uppercase tracking-wide text-text-faint">{t('mesures.section_concordance')}</div>
                    <ScanDeltaCard delta={delta} onSeeAll={() => setAllOpen(true)}/>
                  </div>
                )}
              </section>

              {/* Profile build — the title tops the build panel + the completion message.
                  (The old single-chart "derived profile" block is removed: obsoleted by multi-source —
                  a chart serves several builds, or no profile of its own if it's an ingredient.) */}
              <section className="space-y-2 pt-4 border-t border-border-soft">
                <div className="text-[13px] font-semibold uppercase tracking-wide text-text-strong">{t('mesures.section_profile')}</div>
                {/* SHARED build panel (base + colprof flags + visible command + button) — same
                    component as the wizard. Available from ≥1 included scan; software, background job. */}
                {nIncluded >= 1 && (
                  <div className="pt-1">
                    <ProfileBuildPanel
                      nIncluded={nIncluded}
                      base={profileBase} setBase={setProfileBase}
                      colprofFlags={colprofFlags} setColprofFlags={setColprofFlags}
                      colprofPresets={colprofPresets}
                      colprofPresetKey={colprofPresetKey} setColprofPresetKey={setColprofPresetKey}
                      saveColprofPreset={saveColprofPreset}
                      loadColprofHelp={loadColprofHelp} showColprofHelp={showColprofHelp} colprofHelp={colprofHelp}
                      onBuild={buildProfile} busy={busy} buildPhase={buildPhase}
                      buildLabel={prof.built ? t('mesures.rebuild') : t('mesures.build')}
                      chartId={selected} onSelectionChange={setExtraSources}
                      nameNotice={nameNotice}/>
                  </div>
                )}
                {/* Completion message (success) — name + personal repo location + installable. FAILURE
                    goes through the error banner (setError = job's result.error). */}
                {buildResult && !buildPhase && (
                  <div className="mt-2 rounded-md border border-ok/30 bg-ok/5 px-3 py-2.5 space-y-1.5">
                    <div className="flex items-center gap-1.5 text-xs2 font-semibold text-ok">
                      <CheckCircle2 size={14}/> {t('mesures.build_done')}
                    </div>
                    {buildResult.ranged?.filename ? (
                      <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-0.5 text-tiny">
                        <dt className="text-text-muted">{t('mesures.build_done_file')}</dt>
                        <dd className="font-mono text-text-strong break-all">{buildResult.ranged.filename}</dd>
                        <dt className="text-text-muted">{t('mesures.build_done_location')}</dt>
                        <dd className="font-mono text-text-faint break-all">{buildResult.ranged_icc_path}</dd>
                      </dl>
                    ) : (
                      <p className="text-tiny text-text-faint">{t('mesures.build_done_not_ranged')}</p>
                    )}
                    {buildResult.installable && (
                      <p className="text-tiny text-text-faint">{t('mesures.build_done_installable')}</p>
                    )}
                    {/* Direct access to the inspector of the just-created profile (reuses setInspect +
                        ProfileInspectorModal). Gated: no filed path (unknown serial) → no button. */}
                    {buildResult.ranged_icc_path && (
                      <button type="button" onClick={() => setInspect({ path: buildResult.ranged_icc_path })}
                              className="inline-flex items-center gap-1.5 text-xs2 text-accent hover:bg-accent/10 px-2 py-1 rounded-md">
                        <FileSearch size={13}/> {t('mesures.inspect')}
                      </button>
                    )}
                  </div>
                )}
              </section>

              {/* Actions — scan DELEGATES to the wizard (isLoaded); disk management (DESTRUCTIVE) here */}
              <section className="pt-2 border-t border-border-soft space-y-2">
                <button type="button" onClick={openScan}
                        className="inline-flex items-center gap-1.5 text-xs2 font-medium bg-accent/10 text-accent px-3 py-1.5 rounded-md hover:bg-accent/15">
                  <ScanLine size={14}/> {t('mesures.open_in_scan_flow')}
                </button>
                <p className="text-tiny text-text-faint">{t('mesures.open_in_scan_flow_hint')}</p>
                <div className="flex items-center gap-2 pt-1">
                  {!id.lightened && (
                    <button type="button" onClick={() => setConfirm({ type: 'lighten' })} disabled={busy}
                            className="inline-flex items-center gap-1.5 text-tiny text-text-muted hover:text-text-strong border border-border-soft px-2.5 py-1 rounded-md disabled:opacity-50">
                      <Archive size={13}/> {t('mesures.lighten')}
                    </button>
                  )}
                  <button type="button" onClick={() => setConfirm({ type: 'chart' })} disabled={busy}
                          className="inline-flex items-center gap-1.5 text-tiny text-danger hover:bg-danger/10 border border-danger/30 px-2.5 py-1 rounded-md disabled:opacity-50">
                    <Trash2 size={13}/> {t('mesures.delete_chart')}
                  </button>
                </div>
              </section>
            </div>
          </aside>
        </>
      )}

      {/* Modals (page level) — only shown if a chart is selected */}
      {allOpen && delta && detail && (
        <QCTableModal open chartId={id.chart_id} title={id.chart_id}
          onChanged={() => loadDetail(selected)}
          onClose={() => { setAllOpen(false); loadDetail(selected); }}/>
      )}
      {viewTi3 && detail && (
        <MeasurementsModal open chartId={id.chart_id} ti3={viewTi3} onClose={() => setViewTi3(null)}/>
      )}
      {confirm && (
        <ConfirmModal
          open
          title={t(`mesures.confirm.${confirm.type}_title`)}
          message={t(`mesures.confirm.${confirm.type}_body`)
            + (confirm.type === 'scan' && confirm.ti3 ? `\n${confirm.ti3}` : '')}
          confirmLabel={t(`mesures.confirm.${confirm.type}_confirm`)}
          cancelLabel={t('common.cancel')}
          confirmKind={confirm.type !== 'lighten' ? 'danger' : 'primary'}
          busy={busy}
          onConfirm={runConfirm}
          onCancel={() => setConfirm(null)}/>
      )}
      {/* Collision (409) → OS 3-choice pop-up. Cancel = nothing; Keep both = -N;
          Replace (danger) = overwrites + preserves tags/notes. Replay = same build + on_conflict. */}
      {conflict && (
        <ConfirmModal
          open
          title={t('mesures.profile_conflict_title')}
          message={t('mesures.profile_conflict_body', { name: conflict.name })}
          cancelLabel={t('common.cancel')}
          thirdLabel={t('mesures.profile_conflict_keep_both')}
          confirmLabel={t('mesures.profile_conflict_replace')}
          confirmKind="danger"
          busy={busy}
          onCancel={() => setConflict(null)}
          onThird={() => { setConflict(null); buildProfile('keep_both'); }}
          onConfirm={() => { setConflict(null); buildProfile('replace'); }}/>
      )}
      {inspect && <ProfileInspectorModal open source={inspect} onClose={() => setInspect(null)}/>}
      {scanWizard && (
        <ArgyllProfileWizard open mode="scan" paper={scanWizard.paper} ge={scanWizard.ge}
          initialChart={scanWizard.chart}
          onClose={() => { setScanWizard(null); reload(); }}/>
      )}
    </div>
  );
}
