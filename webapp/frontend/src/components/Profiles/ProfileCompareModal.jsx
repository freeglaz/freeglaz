import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import {
  X, ChevronRight, ChevronDown, AlertTriangle, FlaskConical,
  Layers, Boxes, BarChart3, Lock, FolderOpen, Search,
  Eye, EyeOff, Plus, Check,
} from 'lucide-react';
import * as api from '../../api/client.js';
import { sliceMeshAtL } from '../../lib/gamutSlice.js';
import { colorForIndex } from '../../lib/comparePalette.js';
import { geLabel } from '../../lib/geLabel.js';
import GamutSlice2D from '../ProfileInspector/GamutSlice2D.jsx';
import {
  REFERENCE_SPACES, DEFAULT_REFERENCE_SPACE,
} from '../../lib/labChromaBackground.js';
import Gamut3DRenderer from '../ProfileInspector/Gamut3DRenderer.jsx';
import ErrorBoundary from '../ui/ErrorBoundary.jsx';
import ProfilesList from './ProfilesList.jsx';

/**
 * Profile-compare — Modal for comparing 2+ ICC profiles.
 *
 * Two steps:
 *   1. Multi-checkbox selection from the store (mirror + repo, flat)
 *   2. Result in 3 tabs: Visual 2D (a*b* slice overlaid at adjustable L*),
 *      Visual 3D (multi-mesh extension coming), Metrics (factual table
 *      + EXPERIMENTAL curvature).
 *
 * Consumes `POST /api/profiles/compare` and `GET /api/profiles/gamut`
 * (one call per profile for the 2D view). Read-only — never writes.
 *
 * Epistemic safeguard: curvature (in the Metrics tab) is displayed with
 * its EXPERIMENTAL badge and the full notice returned by the backend.
 * The toggle is OFF by default on the selection side.
 *
 * Okabe-Ito qualitative palette (cf. lib/comparePalette): 8 distinct
 * colorblind-accessible colors, assigned in selection order. Color ↔
 * profile-name legend always displayed.
 */
// Hard cap aligned with the backend (`_COMPARE_MAX_PROFILES` on the Python side).
const _COMPARE_MAX_PROFILES = 8;


export default function ProfileCompareModal({
  open, onClose, store, z9 = null, seedPaths = null,
}) {
  const { t } = useTranslation();
  // Profile-compare commit 3 — workspace without an airlock. The selection
  // is no longer an initial Set validated with a "Launch" button, it's a
  // live list built in the rail. Auto-fetch as soon as there are 2+ valid
  // profiles.
  const [paths, setPaths] = useState([]);
  const [hiddenSet, setHiddenSet] = useState(() => new Set());
  const [referenceIndex, setReferenceIndex] = useState(0);
  const [withCurvature, setWithCurvature] = useState(false);
  const [activeTab, setActiveTab] = useState('visual2d');
  const [addOpen, setAddOpen] = useState(false);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Reset on open. seedPaths pre-seeds the list (from the inspector or
  // the Profiles screen); we ALWAYS land on the workspace, never on the
  // add panel — EmptyWorkspace handles the 0/1-profile state with its own
  // "Add" CTA, the panel only opens on an explicit click on "+ Add" (rail
  // or EmptyWorkspace). Workspace first, always.
  useEffect(() => {
    if (!open) return;
    const seed = Array.isArray(seedPaths)
      ? seedPaths.slice(0, _COMPARE_MAX_PROFILES)
      : [];
    setPaths(seed);
    setHiddenSet(new Set());
    setReferenceIndex(0);
    setWithCurvature(false);
    setActiveTab('visual2d');
    setAddOpen(false);
    setResult(null);
    setLoading(false);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Esc → close (or close the add panel first, as a priority)
  useEffect(() => {
    if (!open) return;
    const h = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        if (addOpen) setAddOpen(false);
        else onClose?.();
      }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, onClose, addOpen]);

  // Store tree + path → readable-label mapping (commit 3).
  // The personal Z9 space (refined per paper) is included so its profiles
  // are comparable and labeled like the others.
  const tree = useMemo(() => _buildStoreTree(store, z9), [store, z9]);
  const pathLabels = useMemo(() => _buildPathLabels(tree), [tree]);

  // Auto-fetch /api/profiles/compare when there are 2+ profiles.
  useEffect(() => {
    if (!open) return;
    if (paths.length < 2) {
      setResult(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.compareProfiles({ paths, withCurvature })
      .then((data) => { if (!cancelled) setResult(data); })
      .catch((e) => { if (!cancelled) setError(e.message || String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, paths, withCurvature]);

  // Mutations on the comparison list.
  const addPath = (p) => {
    if (!p || paths.includes(p) || paths.length >= _COMPARE_MAX_PROFILES) return;
    setPaths([...paths, p]);
  };
  const removePath = (p) => {
    const idx = paths.indexOf(p);
    if (idx === -1) return;
    const next = paths.filter((x) => x !== p);
    setPaths(next);
    if (idx === referenceIndex) {
      setReferenceIndex(0);
    } else if (idx < referenceIndex) {
      setReferenceIndex(Math.max(0, referenceIndex - 1));
    }
    setHiddenSet((prev) => {
      const n = new Set(prev);
      n.delete(p);
      return n;
    });
  };
  const toggleVisible = (p) => {
    setHiddenSet((prev) => {
      const n = new Set(prev);
      if (n.has(p)) n.delete(p);
      else n.add(p);
      return n;
    });
  };

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[100] bg-black/50 backdrop-blur-[2px]
                 flex items-center justify-center p-4 sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-label={t('compare.title')}
      onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}>
      <div
        className="bg-bg border border-border-soft rounded-[14px] shadow-2xl
                   w-[1280px] max-w-[95vw] h-[820px] max-h-[92vh]
                   flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}>
        <Header onClose={onClose}/>
        <Workspace
          paths={paths}
          hiddenSet={hiddenSet}
          referenceIndex={referenceIndex}
          setReferenceIndex={setReferenceIndex}
          withCurvature={withCurvature}
          setWithCurvature={setWithCurvature}
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          pathLabels={pathLabels}
          store={store}
          z9={z9}
          addOpen={addOpen}
          setAddOpen={setAddOpen}
          addPath={addPath}
          removePath={removePath}
          toggleVisible={toggleVisible}
          result={result}
          loading={loading}
          error={error}/>
      </div>
    </div>,
    document.body,
  );
}


function Header({ onClose }) {
  const { t } = useTranslation();
  return (
    <div className="px-6 pt-5 pb-4 flex items-start gap-4 border-b border-border-soft">
      <div className="flex-1 min-w-0">
        <div className="text-[10px] font-bold tracking-[0.10em] uppercase
                        text-accent mb-1 font-mono">
          {t('compare.title')}
        </div>
        <h2 className="text-[18px] font-semibold text-text-strong tracking-[-0.01em]">
          {t('compare.subtitle')}
        </h2>
      </div>
      <button
        type="button"
        onClick={onClose}
        title={t('common.close')}
        className="w-8 h-8 rounded-md flex items-center justify-center text-text-muted
                   hover:text-text-strong hover:bg-sunken transition-colors flex-shrink-0">
        <X size={15} strokeWidth={1.8}/>
      </button>
    </div>
  );
}


// ─── Workspace: permanent rail + main area (commit 3) ────


function Workspace({
  paths, hiddenSet, referenceIndex, setReferenceIndex,
  withCurvature, setWithCurvature,
  activeTab, setActiveTab,
  pathLabels, store, z9,
  addOpen, setAddOpen,
  addPath, removePath, toggleVisible,
  result, loading, error,
}) {
  // Qualitative palette, stable by index (Okabe-Ito).
  const palette = useMemo(
    () => paths.map((_, i) => colorForIndex(i)),
    [paths.length],
  );
  const labels = useMemo(
    () => paths.map((p) => pathLabels.get(p)?.label || _basename(p)),
    [paths, pathLabels],
  );
  const sublabels = useMemo(
    () => paths.map((p) => pathLabels.get(p)?.sublabel || ''),
    [paths, pathLabels],
  );
  const pathsSet = useMemo(() => new Set(paths), [paths]);

  return (
    <div className="flex-1 flex min-h-0 relative">
      <CompareRail
        paths={paths}
        labels={labels}
        sublabels={sublabels}
        palette={palette}
        hiddenSet={hiddenSet}
        referenceIndex={referenceIndex}
        setReferenceIndex={setReferenceIndex}
        showReference={activeTab === 'visual3d'}
        toggleVisible={toggleVisible}
        removePath={removePath}
        withCurvature={withCurvature}
        setWithCurvature={setWithCurvature}
        onAdd={() => setAddOpen(true)}
        canAdd={paths.length < _COMPARE_MAX_PROFILES}/>

      <div className="flex-1 flex flex-col min-w-0 bg-bg">
        <WorkspaceContent
          paths={paths}
          labels={labels}
          sublabels={sublabels}
          palette={palette}
          hiddenSet={hiddenSet}
          referenceIndex={referenceIndex}
          setReferenceIndex={setReferenceIndex}
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          result={result}
          loading={loading}
          error={error}
          onAdd={() => setAddOpen(true)}/>
      </div>

      {addOpen && (
        <AddProfilesOverlay
          store={store}
          z9={z9}
          pathsSet={pathsSet}
          canAdd={paths.length < _COMPARE_MAX_PROFILES}
          remainingSlots={_COMPARE_MAX_PROFILES - paths.length}
          addPath={addPath}
          onClose={() => setAddOpen(false)}/>
      )}
    </div>
  );
}


// ─── Permanent rail: one row per profile + add + curvature ────────────


function CompareRail({
  paths, labels, sublabels, palette,
  hiddenSet, referenceIndex, setReferenceIndex, showReference,
  toggleVisible, removePath,
  withCurvature, setWithCurvature,
  onAdd, canAdd,
}) {
  const { t } = useTranslation();
  return (
    <aside className="w-[280px] shrink-0 border-r border-border-soft
                      bg-surface flex flex-col">
      <div className="px-3 py-2 border-b border-border-soft">
        <div className="text-tiny font-bold uppercase tracking-wider
                        text-text-faint">
          {t('compare.rail_title', { n: paths.length, max: _COMPARE_MAX_PROFILES })}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-1">
        {paths.map((p, i) => (
          <RailRow
            key={p}
            index={i}
            color={palette[i]}
            label={labels[i]}
            sublabel={sublabels[i]}
            hidden={hiddenSet.has(p)}
            isReference={i === referenceIndex}
            showReference={showReference}
            onSetReference={() => setReferenceIndex(i)}
            onToggleVisible={() => toggleVisible(p)}
            onRemove={() => removePath(p)}/>
        ))}

        <button
          type="button"
          onClick={onAdd}
          disabled={!canAdd}
          className="w-full mt-1 inline-flex items-center justify-center gap-1.5
                     px-3 py-2 text-xs2 border border-dashed border-border-soft
                     hover:border-accent hover:text-accent text-text-muted
                     rounded-md transition-colors
                     disabled:opacity-40 disabled:cursor-not-allowed">
          <Plus size={13}/>
          {canAdd
            ? t('compare.rail_add')
            : t('compare.rail_add_max', { max: _COMPARE_MAX_PROFILES })}
        </button>
      </div>

      <div className="px-3 py-2 border-t border-border-soft">
        <label className="flex items-center gap-2 text-tiny cursor-pointer
                          text-text-muted hover:text-text-strong">
          <input
            type="checkbox"
            checked={withCurvature}
            onChange={(e) => setWithCurvature(e.target.checked)}
            className="accent-icc-warn"/>
          <FlaskConical size={12} className="text-icc-warn"/>
          <span>{t('compare.toggle_curvature')}</span>
        </label>
      </div>
    </aside>
  );
}


function RailRow({
  index, color, label, sublabel, hidden, isReference, showReference,
  onSetReference, onToggleVisible, onRemove,
}) {
  const { t } = useTranslation();
  return (
    <div
      className={`rounded px-2 py-1.5 border transition-colors ${
        hidden
          ? 'border-border-soft/60 bg-sunken/20 opacity-60'
          : isReference && showReference
            ? 'border-accent bg-accent/5'
            : 'border-border-soft hover:bg-sunken/40'
      }`}>
      <div className="flex items-start gap-2">
        <span
          className="inline-block w-2.5 h-2.5 rounded-sm mt-1 shrink-0"
          style={{ backgroundColor: color }}
          aria-hidden="true"/>
        <div className="flex-1 min-w-0">
          <div className="text-xs2 font-medium text-text-strong truncate"
               title={label}>
            <span className="text-tiny font-mono text-text-faint mr-1">
              [{index + 1}]
            </span>
            {label}
          </div>
          {sublabel && (
            <div className="text-tiny text-text-faint truncate" title={sublabel}>{sublabel}</div>
          )}
        </div>
      </div>

      <div className="mt-1.5 flex items-center gap-1">
        {showReference && (
          <label
            className="inline-flex items-center gap-1 text-tiny cursor-pointer
                       text-text-muted hover:text-accent"
            title={t('compare.rail_reference_hint')}>
            <input
              type="radio"
              name="compare-reference"
              checked={isReference}
              onChange={onSetReference}
              className="accent-accent w-3 h-3"/>
            <span>{t('compare.rail_reference')}</span>
          </label>
        )}
        <div className="flex-1"/>
        <button
          type="button"
          onClick={onToggleVisible}
          title={hidden ? t('compare.rail_show') : t('compare.rail_hide')}
          className="w-6 h-6 inline-flex items-center justify-center rounded
                     text-text-muted hover:text-text-strong hover:bg-sunken">
          {hidden ? <EyeOff size={12}/> : <Eye size={12}/>}
        </button>
        <button
          type="button"
          onClick={onRemove}
          title={t('compare.rail_remove')}
          className="w-6 h-6 inline-flex items-center justify-center rounded
                     text-text-muted hover:text-danger hover:bg-danger/10">
          <X size={12}/>
        </button>
      </div>
    </div>
  );
}


// ─── Main content: empty state OR 3 tabs ───────────────────


const TABS = [
  { id: 'visual2d', icon: Layers,    key: 'compare.tab_visual_2d' },
  { id: 'visual3d', icon: Boxes,     key: 'compare.tab_visual_3d' },
  { id: 'metrics',  icon: BarChart3, key: 'compare.tab_metrics'   },
];


function WorkspaceContent({
  paths, labels, sublabels, palette,
  hiddenSet, referenceIndex, setReferenceIndex,
  activeTab, setActiveTab,
  result, loading, error,
  onAdd,
}) {
  const { t } = useTranslation();

  // ⚠ ALL hooks must be called BEFORE the early returns (React's Rules
  // of Hooks: same number/order of hooks on every render). The
  // `entriesForVisuals` useMemo below was initially placed after the
  // `if (paths.length < 2) return …` → result: crash "Rendered more
  // hooks than during the previous render" exactly at the 1→2 profile
  // transition, when the second useMemo appears for the first time
  // (commit 4d5f219).
  //
  // Memoization always necessary: this array is passed as a prop to
  // VisualSlice2DView / VisualGamut3DView, which put it in the
  // dependency list of a useEffect that fetches meshes. Without useMemo,
  // we'd recreate a new array of objects on every render → the views
  // refetch in a loop (~2-3 s, the fetch duration). `paths` is a stable
  // state from the parent; memoizing on `[paths]` is enough.
  const entriesForVisuals = useMemo(
    () => paths.map((p) => ({ path: p })),
    [paths],
  );

  if (paths.length < 2) {
    return (
      <EmptyWorkspace
        paths={paths}
        onAdd={onAdd}
        needed={2 - paths.length}/>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex items-start gap-3 px-8 py-10 text-danger">
        <AlertTriangle size={18} className="mt-0.5 shrink-0"/>
        <div>
          <h3 className="font-semibold mb-1">{t('compare.error_title')}</h3>
          <p className="text-xs2 font-mono text-text-muted">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="px-6 border-b border-border-soft bg-surface flex gap-1">
        {TABS.map((entry) => {
          const active = activeTab === entry.id;
          const Icon = entry.icon;
          return (
            <button
              key={entry.id}
              type="button"
              onClick={() => setActiveTab(entry.id)}
              className={`inline-flex items-center gap-1.5 px-3 py-2 text-xs2
                          border-b-2 transition-colors -mb-px ${
                            active
                              ? 'border-accent text-accent font-semibold'
                              : 'border-transparent text-text-muted hover:text-text-strong'
                          }`}>
              <Icon size={13}/>
              {t(entry.key)}
            </button>
          );
        })}
        {loading && result == null && (
          <div className="ml-2 self-center text-tiny text-text-faint inline-flex items-center gap-1.5">
            <span className="inline-block w-3 h-3 border-2 border-accent
                             border-t-transparent rounded-full animate-spin"/>
            {t('compare.metrics_loading')}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-auto min-h-0">
        {activeTab === 'visual2d' && (
          <VisualSlice2DView
            entries={entriesForVisuals}
            palette={palette}
            labels={labels}
            hiddenSet={hiddenSet}/>
        )}
        {activeTab === 'visual3d' && (
          <VisualGamut3DView
            entries={entriesForVisuals}
            palette={palette}
            labels={labels}
            hiddenSet={hiddenSet}
            referenceIndex={referenceIndex}
            onReferenceChange={setReferenceIndex}/>
        )}
        {activeTab === 'metrics' && (
          result ? (
            <MetricsTableView
              entries={result.profiles}
              result={result}
              labels={labels}
              sublabels={sublabels}/>
          ) : (
            <div className="px-6 py-10 text-text-faint text-xs2 italic">
              {t('compare.metrics_waiting')}
            </div>
          )
        )}
      </div>
    </>
  );
}


function EmptyWorkspace({ paths, onAdd, needed }) {
  const { t } = useTranslation();
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-3
                    px-8 py-10 text-center">
      <Layers size={36} className="text-text-faint"/>
      <h3 className="text-sm font-semibold text-text-strong">
        {t('compare.empty_title')}
      </h3>
      <p className="text-xs2 text-text-muted max-w-md leading-snug">
        {paths.length === 0
          ? t('compare.empty_body_zero')
          : t('compare.empty_body_one', { needed })}
      </p>
      <button
        type="button"
        onClick={onAdd}
        className="mt-2 inline-flex items-center gap-1.5 px-4 py-2 text-xs2
                   bg-accent text-white hover:bg-accent/90 rounded-md
                   font-medium transition-colors">
        <Plus size={13}/>
        {t('compare.rail_add')}
      </button>
    </div>
  );
}


// ─── Overlay add panel (commit 4) — reuses ProfilesList in picker mode
//     for strict consistency with the Profiles screen: same sections
//     (Z9 mirror / personal repo), same HP taxonomy with Custom Paper
//     first, same badge and hierarchy style. ────


function AddProfilesOverlay({
  store, z9 = null, pathsSet, canAdd, remainingSlots, addPath, onClose,
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const filtered = useMemo(
    () => _filterStoreForPicker(store, z9, search),
    [store, z9, search],
  );
  const isEmptyMirror = !(store?.mirrors || []).length;

  // Overlay positioned to the right of the 280px rail → up to the right
  // edge. Width calc(100%-280px) guarantees no truncation of long names
  // (cf. brief: not a confined 300px sidebar).
  return (
    <div className="absolute inset-y-0 right-0 w-[calc(100%-280px)]
                    bg-bg/95 backdrop-blur-sm border-l border-border-soft
                    z-20 flex flex-col"
         role="dialog"
         aria-label={t('compare.add_title')}>
      <div className="px-5 py-3 border-b border-border-soft
                      flex items-center gap-3 bg-surface">
        <div className="flex-1 min-w-0">
          <div className="text-tiny font-bold uppercase tracking-wider text-accent">
            {t('compare.add_title')}
          </div>
          <div className="text-tiny text-text-faint mt-0.5">
            {canAdd
              ? t('compare.add_hint', { remaining: remainingSlots })
              : t('compare.add_hint_full')}
          </div>
        </div>
        <div className="relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2
                                        text-text-faint pointer-events-none"/>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('compare.search_placeholder')}
            className="pl-6 pr-2 py-1 text-xs2 w-56 bg-bg border border-border-soft
                       rounded text-text-strong focus:outline-none focus:border-accent"/>
        </div>
        {/* Explicit CTA to go back to the workspace when the comparison
            is already built (≥ 2 profiles). Otherwise, only the X closes. */}
        {pathsSet && pathsSet.size >= 2 && (
          <button
            type="button"
            onClick={onClose}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs2
                       bg-accent text-white hover:bg-accent/90 rounded-md
                       font-medium transition-colors">
            {t('compare.add_view_comparison')}
          </button>
        )}
        <button
          type="button"
          onClick={onClose}
          title={t('common.close')}
          className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted
                     hover:text-text-strong hover:bg-sunken transition-colors">
          <X size={14}/>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <ErrorBoundary label={t('compare.add_error')}>
          <ProfilesList
            filtered={filtered}
            isEmptyMirror={isEmptyMirror}
            pickerMode
            pickedPaths={pathsSet}
            onPick={addPath}
            canPick={canAdd}/>
        </ErrorBoundary>
      </div>
    </div>
  );
}


/** Filters the raw store (backend structure of GET /api/profiles/store)
 *  by search text. Preserves the mirrors/papers/profiles hierarchy and
 *  repo.{printers, displays, workingspaces} expected by ProfilesList. */
function _filterStoreForPicker(store, z9, search) {
  const empty = {
    z9: [],
    mirrors: [],
    repo: { printers: [], displays: [], workingspaces: [] },
  };
  if (!store) return empty;
  const q = search.trim().toLowerCase();
  const ok = (s) => !q || (s || '').toLowerCase().includes(q);
  // Personal Z9 space: normalize path → absolute_path (picker key) as in
  // ProfilesPage, subject to the same search filter.
  const z9Serials = (z9?.serials || []).map((s) => ({
    ...s,
    papers: (s.papers || []).map((p) => ({
      ...p,
      profiles: (p.profiles || [])
        .map((prof) => ({ ...prof, absolute_path: prof.path }))
        .filter((prof) => ok(p.paper_name) || ok(prof.label) || ok(prof.filename)),
    })).filter((p) => p.profiles.length > 0),
  })).filter((s) => s.papers.length > 0);
  const mirrors = (store.mirrors || []).map((m) => {
    const papers = (m.papers || []).map((p) => {
      // Match if the paper name or a profile name (filename /
      // z9_icc_name) matches the search.
      if (ok(p.paper_name)) return p;
      const profiles = (p.profiles || []).filter(
        (prof) => ok(prof.filename) || ok(prof.z9_icc_name),
      );
      return profiles.length ? { ...p, profiles } : null;
    }).filter(Boolean);
    if (ok(m.serial)) return { ...m, papers };
    return papers.length ? { ...m, papers } : null;
  }).filter(Boolean);
  const repo = store.repo || {};
  const filterRepo = (items) => (items || []).filter(
    (p) => ok(p.display_name) || ok(p.filename) || ok(p.device || ''),
  );
  return {
    z9: z9Serials,
    mirrors,
    repo: {
      printers: filterRepo(repo.printers),
      displays: filterRepo(repo.displays),
      workingspaces: filterRepo(repo.workingspaces),
    },
  };
}


// ─── Visual 2D tab: overlaid a*b* slices at L* ──────────────────


const INTENTS_2D = ['relative', 'perceptual', 'saturation'];
const L_SNAPS = [10, 30, 50, 70, 90];


function VisualSlice2DView({ entries, palette, labels, hiddenSet }) {
  const { t } = useTranslation();
  const [L0, setL0] = useState(50);
  const [intent, setIntent] = useState('relative');
  // Reference space of the swatch background (sRGB / AdobeRGB / P3 /
  // Rec2020 / ProPhoto). Default AdobeRGB — covers most paper gamuts
  // without wasting background outside the useful area.
  const [bgSpace, setBgSpace] = useState(DEFAULT_REFERENCE_SPACE);

  // Parallel fetch of the N meshes (one per profile) for the current
  // intent. Re-fetch when entries or intent change. Cancelable via a
  // cancel flag to avoid a race if the intent changes before the fetch
  // finishes.
  const [meshes, setMeshes] = useState([]);
  const [meshesLoading, setMeshesLoading] = useState(false);
  const [meshesError, setMeshesError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setMeshesLoading(true);
    setMeshesError(null);
    setMeshes([]);
    Promise.all(
      entries.map((e) =>
        api.getProfileGamut({ path: e.path, intent })
           .catch((err) => ({ _error: err.message || String(err) })),
      ),
    ).then((results) => {
      if (cancelled) return;
      setMeshes(results);
      const firstErr = results.find((r) => r && r._error);
      if (firstErr) setMeshesError(firstErr._error);
      setMeshesLoading(false);
    }).catch((err) => {
      if (cancelled) return;
      setMeshesError(err.message || String(err));
      setMeshesLoading(false);
    });
    return () => { cancelled = true; };
  }, [entries, intent]);

  // Slices memoized at L0 — one slice per mesh, recomputed on each tick.
  const slices = useMemo(() => {
    return meshes.map((m) => {
      if (!m || m._error || !m.vertices) return null;
      return sliceMeshAtL(m.vertices, m.indices, L0, m.colors_srgb);
    });
  }, [meshes, L0]);

  // Composition for GamutSlice2D: one mesh per profile, palette color,
  // uniform strokeWidth. No per-hue interpolation here (kept for the
  // single-profile inspector) — we prioritize contour readability in the
  // multiple overlay. Hidden profiles (eye off in the rail) are removed
  // from the drawing.
  const sliceMeshes = useMemo(() => {
    const out = [];
    slices.forEach((s, i) => {
      const hidden = hiddenSet && hiddenSet.has(entries[i]?.path);
      if (hidden) return;
      if (s && s.segments && s.segments.length > 0) {
        out.push({
          name: `profile_${i}`,
          segments: s.segments,
          color: palette[i],
          strokeWidth: 1.6,
          opacity: 0.92,
        });
      }
    });
    return out;
  }, [slices, palette, hiddenSet, entries]);

  const allMeshesReady = meshes.length > 0
    && meshes.every((m) => m && !m._error && m.vertices);
  const partialResults = meshes.some((m) => m && m._error)
    && meshes.some((m) => m && !m._error && m.vertices);

  return (
    <div className="px-6 py-5 flex flex-col gap-4 h-full">
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <label className="text-tiny text-text-muted uppercase tracking-wider">
            {t('compare.toolbar_intent')}
          </label>
          <div className="inline-flex rounded border border-border-soft overflow-hidden">
            {INTENTS_2D.map((i) => (
              <button
                key={i}
                type="button"
                onClick={() => setIntent(i)}
                className={`px-2 py-1 text-xs2 transition-colors ${
                  intent === i
                    ? 'bg-accent/10 text-accent'
                    : 'text-text-muted hover:text-text-strong'
                }`}>
                {t(`compare.intent_${i}`)}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-tiny text-text-muted uppercase tracking-wider">
            {t('compare.toolbar_bg_space')}
          </label>
          <div className="inline-flex rounded border border-border-soft overflow-hidden">
            {REFERENCE_SPACES.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setBgSpace(s)}
                title={t(`compare.bg_space_${s}_hint`)}
                className={`px-2 py-1 text-xs2 transition-colors ${
                  bgSpace === s
                    ? 'bg-accent/10 text-accent'
                    : 'text-text-muted hover:text-text-strong'
                }`}>
                {t(`compare.bg_space_${s}`)}
              </button>
            ))}
          </div>
        </div>
      </div>

      <LSlider L0={L0} setL0={setL0}/>

      <div className="flex-1 bg-bg border border-border-soft rounded-xl
                      overflow-hidden p-4 flex items-start justify-center
                      relative min-h-[420px]">
        {meshesLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-bg/85 z-10">
            <span className="inline-block w-3.5 h-3.5 border-2 border-accent
                             border-t-transparent rounded-full animate-spin
                             mr-2" aria-hidden="true"/>
            <span className="text-text-muted text-xs2">
              {t('compare.visual_loading', { n: entries.length })}
            </span>
          </div>
        )}
        {meshesError && !allMeshesReady && !partialResults && (
          <div className="absolute inset-0 flex items-center justify-center px-6">
            <div className="text-danger text-xs2 text-center max-w-md">
              <div className="font-semibold mb-1">{t('compare.visual_error_title')}</div>
              <div className="text-text-muted font-mono">{meshesError}</div>
            </div>
          </div>
        )}
        {!meshesLoading && sliceMeshes.length > 0 && (
          <GamutSlice2D
            meshes={sliceMeshes}
            size={480}
            backgroundLstar={L0}
            backgroundSpace={bgSpace}/>
        )}
        {!meshesLoading && sliceMeshes.length === 0 && !meshesError && (
          <div className="text-text-faint text-xs2 italic mt-20">
            {t('compare.empty_slice', { L: L0 })}
          </div>
        )}
      </div>

      <SliceAreasLegend
        slices={slices}
        palette={palette}
        labels={labels}
        entries={entries}
        hiddenSet={hiddenSet}
        L0={L0}/>

      {partialResults && (
        <p className="text-tiny text-icc-warn px-1">
          {t('compare.visual_partial_results')}
        </p>
      )}
    </div>
  );
}


function LSlider({ L0, setL0 }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-3">
      <label className="text-tiny text-text-muted uppercase tracking-wider shrink-0">
        {t('compare.slider_label')}
      </label>
      <div className="flex-1 max-w-xl relative">
        <input
          type="range"
          min={0} max={100} step={1}
          value={L0}
          onChange={(e) => setL0(Number(e.target.value))}
          className="w-full accent-accent"
          aria-label={t('compare.slider_label')}/>
        <div className="relative h-4 text-tiny text-text-faint font-mono mt-0.5">
          {L_SNAPS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setL0(s)}
              style={{ left: `${s}%`, transform: 'translateX(-50%)' }}
              className={`absolute top-0 hover:text-text-strong transition-colors ${
                L0 === s ? 'text-accent font-semibold' : ''
              }`}>
              {s}
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-1">
        <span className="text-tiny text-text-faint font-mono">L*</span>
        <input
          type="number"
          min={0} max={100} step={1}
          value={L0}
          onChange={(e) => {
            const v = Number(e.target.value);
            if (Number.isFinite(v)) setL0(Math.max(0, Math.min(100, v)));
          }}
          className="w-14 px-1.5 py-0.5 text-xs2 font-mono bg-sunken/60
                     border border-border-soft rounded text-text-strong
                     focus:outline-none focus:border-accent"/>
      </div>
    </div>
  );
}


function SliceAreasLegend({ slices, palette, labels, entries, hiddenSet, L0 }) {
  const { t, i18n } = useTranslation();
  const fmt = (v) => {
    if (v == null || !isFinite(v)) return null;
    return v.toLocaleString(
      i18n.language === 'fr' ? 'fr-FR' : 'en-US',
      { maximumFractionDigits: 0 },
    );
  };
  return (
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-tiny
                    text-text-muted px-1">
      <span>
        <span className="text-text-faint">L*&nbsp;=&nbsp;</span>
        <span className="text-text-strong font-mono">{L0}</span>
      </span>
      {slices.map((s, i) => {
        // Profile hidden in the rail → we don't show its area (the
        // contour isn't drawn either, keeping the legend consistent).
        if (hiddenSet && entries && hiddenSet.has(entries[i]?.path)) return null;
        const area = s?.area;
        const v = fmt(area);
        return (
          <span key={i} className="flex items-center gap-1.5">
            <span className="inline-block w-3"
                  style={{ borderTop: `2px solid ${palette[i]}` }}/>
            <span className="text-text-faint font-mono">[{i + 1}]</span>
            <span className="text-text-strong truncate max-w-[160px]" title={labels[i]}>
              {labels[i]}
            </span>
            {v !== null ? (
              <span className="text-text-muted font-mono">
                · {v}<span className="text-text-faint"> Lab²</span>
              </span>
            ) : (
              <span className="text-text-faint italic">
                · {t('compare.area_uncomputable')}
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}


// ─── Visual 3D tab: overlaid gamut volumes (multi-mesh) ─────────


const INTENTS_3D = ['relative', 'perceptual', 'saturation'];


function VisualGamut3DView({
  entries, palette, labels, hiddenSet,
  referenceIndex = 0, onReferenceChange = null,
}) {
  const { t } = useTranslation();
  const [intent, setIntent] = useState('relative');
  const [resetCounter, setResetCounter] = useState(0);
  // Index of the profile rendered as a solid painted in real colors. The
  // other profiles are rendered as wireframe in their identity color.
  // If the parent doesn't control referenceIndex (commit 3 will wire it
  // from the rail), we keep a local state.
  const [localRefIndex, setLocalRefIndex] = useState(referenceIndex);
  const effectiveRefIndex = onReferenceChange ? referenceIndex : localRefIndex;
  const setRefIndex = onReferenceChange || setLocalRefIndex;

  const [meshes, setMeshes] = useState([]);
  const [meshesLoading, setMeshesLoading] = useState(false);
  const [meshesError, setMeshesError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setMeshesLoading(true);
    setMeshesError(null);
    setMeshes([]);
    Promise.all(
      entries.map((e) =>
        api.getProfileGamut({ path: e.path, intent })
           .catch((err) => ({ _error: err.message || String(err) })),
      ),
    ).then((results) => {
      if (cancelled) return;
      setMeshes(results);
      const firstErr = results.find((r) => r && r._error);
      if (firstErr) setMeshesError(firstErr._error);
      setMeshesLoading(false);
    }).catch((err) => {
      if (cancelled) return;
      setMeshesError(err.message || String(err));
      setMeshesLoading(false);
    });
    return () => { cancelled = true; };
  }, [entries, intent]);

  // Building the primaryMeshes for Gamut3DRenderer:
  // - 1 reference profile rendered as a SOLID PAINTED in real colors
  //   (mesh vertex colors_srgb preserved, no override), opacity 0.45 so
  //   the wireframes passing through are visible.
  // - the others as WIREFRAME in their Okabe-Ito identity color.
  // - hidden profiles (eye off in the rail) removed from the scene.
  // The rail's reference radio changes the index → recomposes.
  const primaryMeshes = useMemo(() => {
    return meshes
      .map((m, i) => {
        if (!m || m._error || !m.vertices) return null;
        if (hiddenSet && hiddenSet.has(entries[i]?.path)) return null;
        if (i === effectiveRefIndex) {
          return {
            mesh: m,
            opacity: 0.45,
            renderStyle: 'solid',
          };
        }
        return {
          mesh: m,
          color: palette[i],
          renderStyle: 'wireframe',
        };
      })
      .filter(Boolean);
  }, [meshes, palette, effectiveRefIndex, hiddenSet, entries]);

  const partialResults = meshes.some((m) => m && m._error)
    && meshes.some((m) => m && !m._error && m.vertices);

  return (
    <div className="px-6 py-5 flex flex-col gap-3 h-full">
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <label className="text-tiny text-text-muted uppercase tracking-wider">
            {t('compare.toolbar_intent')}
          </label>
          <div className="inline-flex rounded border border-border-soft overflow-hidden">
            {INTENTS_3D.map((i) => (
              <button
                key={i}
                type="button"
                onClick={() => setIntent(i)}
                className={`px-2 py-1 text-xs2 transition-colors ${
                  intent === i
                    ? 'bg-accent/10 text-accent'
                    : 'text-text-muted hover:text-text-strong'
                }`}>
                {t(`compare.intent_${i}`)}
              </button>
            ))}
          </div>
        </div>
        <div className="flex-1"/>
        <button
          type="button"
          onClick={() => setResetCounter((c) => c + 1)}
          className="text-tiny text-text-muted hover:text-text-strong
                     px-2 py-1 border border-border-soft rounded
                     hover:border-border-strong transition-colors">
          {t('compare.reset_view')}
        </button>
      </div>

      <p className="text-tiny text-text-faint px-1 leading-snug">
        {t('compare.visual_3d_hint')}
      </p>

      <div className="flex-1 bg-bg border border-border-soft rounded-xl
                      overflow-hidden relative min-h-[440px]">
        {meshesLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-bg/85 z-10">
            <span className="inline-block w-3.5 h-3.5 border-2 border-accent
                             border-t-transparent rounded-full animate-spin
                             mr-2" aria-hidden="true"/>
            <span className="text-text-muted text-xs2">
              {t('compare.visual_loading', { n: entries.length })}
            </span>
          </div>
        )}
        {meshesError && primaryMeshes.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center px-6">
            <div className="text-danger text-xs2 text-center max-w-md">
              <div className="font-semibold mb-1">{t('compare.visual_error_title')}</div>
              <div className="text-text-muted font-mono">{meshesError}</div>
            </div>
          </div>
        )}
        {!meshesLoading && primaryMeshes.length > 0 && (
          <Gamut3DRenderer
            mode="marching_cubes"
            primaryMeshes={primaryMeshes}
            showReference={false}
            renderStyle="both"
            resetCounter={resetCounter}
            className="w-full h-full"/>
        )}
        {!meshesLoading && primaryMeshes.length === 0 && !meshesError && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-text-faint text-xs2 italic">
              {t('compare.visual_3d_empty')}
            </p>
          </div>
        )}
      </div>

      {partialResults && (
        <p className="text-tiny text-icc-warn px-1">
          {t('compare.visual_partial_results')}
        </p>
      )}
    </div>
  );
}


// ─── Metrics tab: factual table + EXPERIMENTAL curvature ──────


function MetricsTableView({ entries, result, labels, sublabels }) {
  const { t } = useTranslation();
  const colCount = entries.length;
  const colMin = `minmax(180px, 1fr)`;
  const gridStyle = {
    display: 'grid',
    gridTemplateColumns: `180px repeat(${colCount}, ${colMin})`,
    gap: '0',
  };
  return (
    <div className="px-6 py-4 space-y-5">
      <div style={gridStyle} className="text-xs2">
        <div className="font-semibold text-text-strong p-2 border-b border-border-soft">
          {t('compare.column_label')}
        </div>
        {entries.map((e, i) => (
          <div key={i} className="p-2 border-b border-border-soft">
            <div className="text-tiny font-mono text-accent">[{i + 1}]</div>
            <div className="font-semibold text-text-strong truncate"
                 title={labels?.[i] || _basename(e.path)}>
              {labels?.[i] || _basename(e.path)}
            </div>
            {sublabels?.[i] && (
              <div className="text-tiny text-text-muted truncate"
                   title={sublabels[i]}>
                {sublabels[i]}
              </div>
            )}
          </div>
        ))}
      </div>

      <Section title={t('compare.section_identity')}>
        <Row label={t('compare.row_version')} values={entries.map((e) => e.icc_version)} grid={gridStyle}/>
        <Row label={t('compare.row_class')} values={entries.map((e) => e.device_class_label || e.device_class)} grid={gridStyle}/>
        <Row label={t('compare.row_colorspace')} values={entries.map((e) => e.color_space)} grid={gridStyle}/>
        <Row label={t('compare.row_pcs')} values={entries.map((e) => e.pcs)} grid={gridStyle}/>
        <Row label={t('compare.row_type')} values={entries.map((e) => e.profile_type_guess)} grid={gridStyle}/>
        <Row label={t('compare.row_size')} values={entries.map((e) => `${e.file_size.toLocaleString()} o`)} grid={gridStyle}/>
        <Row label={t('compare.row_description')} values={entries.map((e) => e.description || '—')} grid={gridStyle} truncate/>
      </Section>

      <Section title={t('compare.section_wpbp')}>
        <Row label={t('compare.row_wp_lab')} values={entries.map((e) => _fmtWhiteLab(e.whitepoint_lab, t))} grid={gridStyle}/>
        <Row label={t('compare.row_bp_lab')} values={entries.map((e) => _fmtLab(e.blackpoint_lab))} grid={gridStyle}/>
      </Section>

      <Section title={t('compare.section_gamut')}>
        {['perceptual', 'relative', 'saturation'].map((k) => (
          <Row
            key={k}
            label={t(`compare.row_gamut_${k}`)}
            values={entries.map((e) => _fmtVolume(e.gamut?.[k]))}
            grid={gridStyle}/>
        ))}
      </Section>

      <Section title={t('compare.section_lut')}>
        <Row label={t('compare.row_lut_grid')} values={entries.map((e) => e.lut ? `${e.lut.grid_size}³` : '—')} grid={gridStyle}/>
        <Row label={t('compare.row_lut_l_range')} values={entries.map((e) => _fmtRange(e.lut?.lab_l_range))} grid={gridStyle}/>
        <Row label={t('compare.row_lut_a_range')} values={entries.map((e) => _fmtRange(e.lut?.lab_a_range))} grid={gridStyle}/>
        <Row label={t('compare.row_lut_b_range')} values={entries.map((e) => _fmtRange(e.lut?.lab_b_range))} grid={gridStyle}/>
      </Section>

      {result.curvature_notice && (
        <div className="border border-icc-warn/40 bg-icc-warn/10 rounded-md">
          <div className="px-3 py-2 border-b border-icc-warn/40 flex items-center gap-2">
            <FlaskConical size={14} className="text-icc-warn"/>
            <div className="text-xs2 font-semibold text-icc-warn">
              {t('compare.curvature_banner')}
            </div>
          </div>
          <div className="px-3 py-2 text-tiny text-text-muted">
            {result.curvature_notice}
          </div>
          <div className="p-2">
            <Row label={t('compare.row_curv_grid')} values={entries.map((e) => _curv(e, 'grid_native'))} grid={gridStyle}/>
            <Row label={t('compare.row_curv_moy')} values={entries.map((e) => _curvFloat(e, 'moy'))} grid={gridStyle}/>
            <Row label={t('compare.row_curv_med')} values={entries.map((e) => _curvFloat(e, 'med'))} grid={gridStyle}/>
            <Row label={t('compare.row_curv_p95')} values={entries.map((e) => _curvFloat(e, 'p95'))} grid={gridStyle}/>
            <Row label={t('compare.row_curv_max')} values={entries.map((e) => _curvFloat(e, 'max'))} grid={gridStyle}/>
          </div>
          <div className="px-3 py-2 border-t border-icc-warn/30 text-tiny
                          text-text-faint italic">
            {t('compare.curvature_legend')}
          </div>
        </div>
      )}
    </div>
  );
}


function Section({ title, children }) {
  return (
    <section>
      <h3 className="text-tiny uppercase tracking-wider text-text-faint
                     font-semibold mb-1 px-2">{title}</h3>
      <div className="border border-border-soft rounded-md overflow-hidden">
        {children}
      </div>
    </section>
  );
}


function Row({ label, values, grid, truncate = false }) {
  return (
    <div style={grid}
         className="text-xs2 border-b border-border-soft last:border-b-0">
      <div className="px-2 py-1.5 text-text-faint bg-sunken/30 font-medium">
        {label}
      </div>
      {values.map((v, i) => (
        <div
          key={i}
          className={`px-2 py-1.5 font-mono text-text-strong border-l
                      border-border-soft ${truncate ? 'truncate' : ''}`}
          title={truncate && typeof v === 'string' ? v : undefined}>
          {v ?? '—'}
        </div>
      ))}
    </div>
  );
}


// ─── Helpers ───────────────────────────────────────────────────────────


function _basename(path) {
  if (!path) return '';
  const parts = String(path).split('/');
  return parts[parts.length - 1] || path;
}


function _fmtLab(lab) {
  if (!Array.isArray(lab) || lab.length !== 3) return '—';
  const [L, a, b] = lab;
  return `L*${L.toFixed(1)} a${_sign(a)} b${_sign(b)}`;
}


const _WHITE_L_MIN = 50;

function _fmtWhiteLab(lab, t) {
  if (!Array.isArray(lab) || lab.length !== 3) return '—';
  const [L] = lab;
  const text = _fmtLab(lab);
  if (L >= _WHITE_L_MIN) return text;
  return (
    <span className="inline-flex items-center gap-1 text-icc-warn"
          title={t('compare.wp_invalid_tooltip')}>
      {text}
      <AlertTriangle size={11} className="shrink-0"/>
      <span className="text-tiny">{t('compare.wp_invalid')}</span>
    </span>
  );
}


function _sign(v) {
  if (typeof v !== 'number') return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(1);
}


function _fmtVolume(v) {
  return typeof v === 'number' ? Math.round(v).toLocaleString() : '—';
}


function _fmtRange(r) {
  if (!Array.isArray(r) || r.length !== 2) return '—';
  return `[${r[0].toFixed(1)} .. ${r[1].toFixed(1)}]`;
}


function _curv(e, k) {
  const c = e.curvature;
  if (!c || c.status !== 'ok') return c?.status || '—';
  return c[k] ?? '—';
}


function _curvFloat(e, k) {
  const c = e.curvature;
  if (!c || c.status !== 'ok') return c?.status || '—';
  const v = c[k];
  return typeof v === 'number' ? v.toFixed(3) : '—';
}


// HP taxonomy (consistent with ProfilesList.jsx 17.2.x). Custom Paper
// first, then the 7 factory categories in their standard HP order.
const CUSTOM_CATEGORY = 'Custom Paper';
const HP_FACTORY_CATEGORY_ORDER = [
  'Photo Paper',
  'Fine Art Material',
  'Bond and Coated Paper',
  'Film',
  'Backlit Material',
  'Self-Adhesive material',
  'Banner & Sign material',
];


/**
 * Builds the selection tree: Z9 mirrors grouped by category/paper +
 * personal repo split into 3 sub-categories. Each profile carries a
 * readable label and badges (GE slot, custom/factory, device for the
 * printers repo).
 */
function _buildStoreTree(store, z9 = null) {
  const empty = {
    mirrors: [],
    repo: { printers: [], displays: [], workingspaces: [] },
    z9Flat: [],
  };
  if (!store) return empty;

  // Personal Z9 space: flat entries for the path → label mapping.
  const z9Flat = [];
  for (const s of (z9?.serials || [])) {
    for (const p of (s.papers || [])) {
      for (const prof of (p.profiles || [])) {
        if (!prof.path) continue;
        z9Flat.push({
          path: prof.path,
          zone: 'z9',
          label: prof.label || prof.filename,
          sublabel: [p.paper_name, prof.gloss_slot && geLabel(prof.gloss_slot), prof.method_flags]
            .filter(Boolean).join(' · '),
        });
      }
    }
  }

  const mirrors = (store.mirrors || []).map((m) => {
    const byCategory = {};
    for (const p of (m.papers || [])) {
      const cat = p.category_name || CUSTOM_CATEGORY;
      (byCategory[cat] = byCategory[cat] || []).push(p);
    }
    const orderedNames = _orderedCategories(byCategory);
    const categories = orderedNames.map((catName) => {
      const papers = (byCategory[catName] || []).map((p) => ({
        slug: p.slug,
        paperName: p.paper_name || p.slug,
        profiles: (p.profiles || [])
          .filter((prof) => !!prof.absolute_path)
          .map((prof) => _mirrorProfileEntry(prof, p)),
      })).filter((p) => p.profiles.length > 0);
      return { name: catName, papers };
    }).filter((c) => c.papers.length > 0);
    return { serial: m.serial, categories };
  }).filter((m) => m.categories.length > 0);

  const repo = store.repo || {};
  const printers = (repo.printers || [])
    .filter((p) => !!p.absolute_path)
    .map((p) => _repoProfileEntry(p, 'printers'));
  const displays = (repo.displays || [])
    .filter((p) => !!p.absolute_path)
    .map((p) => _repoProfileEntry(p, 'displays'));
  const workingspaces = (repo.workingspaces || [])
    .filter((p) => !!p.absolute_path)
    .map((p) => _repoProfileEntry(p, 'workingspaces'));

  return {
    mirrors,
    repo: { printers, displays, workingspaces },
    z9Flat,
  };
}


function _orderedCategories(byCategory) {
  const out = [];
  if (CUSTOM_CATEGORY in byCategory) out.push(CUSTOM_CATEGORY);
  out.push(...HP_FACTORY_CATEGORY_ORDER.filter((c) => c in byCategory));
  const unknown = Object.keys(byCategory)
    .filter((c) => c !== CUSTOM_CATEGORY && !HP_FACTORY_CATEGORY_ORDER.includes(c))
    .sort((a, b) => a.localeCompare(b));
  out.push(...unknown);
  return out;
}


function _mirrorProfileEntry(prof, paper) {
  const label = prof.z9_icc_name || prof.filename;
  const ge = prof.gloss_enhancer; // "FULLPAGE" | "OFF" | "SINGLE" | null
  const geBadge = ge === 'FULLPAGE'
    ? { label: geLabel(ge), tone: 'success' }
    : ge === 'OFF'
      ? { label: geLabel(ge), tone: 'neutral' }
      : ge
        ? { label: ge, tone: 'neutral' }
        : null;
  const customBadge = prof.custom
    ? { label: 'custom', tone: 'accent' }
    : { label: 'factory', tone: 'neutral' };
  return {
    path: prof.absolute_path,
    zone: 'mirror',
    paperName: paper.paper_name || paper.slug,
    label,
    badges: [geBadge, customBadge].filter(Boolean),
    sublabel: `${paper.paper_name || paper.slug} · ${ge || ''} · ${
      prof.custom ? 'custom' : 'factory'
    }`,
  };
}


function _repoProfileEntry(prof, category) {
  const label = prof.display_name || prof.filename;
  const badges = [];
  if (prof.device) {
    badges.push({ label: prof.device, tone: 'neutral' });
  }
  return {
    path: prof.absolute_path,
    zone: 'repo',
    category,
    device: prof.device || null,
    label,
    badges,
    sublabel: prof.device ? `${category} · ${prof.device}` : category,
  };
}


/** Mapping path → { label, sublabel } for the result's color legend. */
function _buildPathLabels(tree) {
  const m = new Map();
  for (const mirror of tree.mirrors) {
    for (const c of mirror.categories) {
      for (const p of c.papers) {
        for (const prof of p.profiles) {
          m.set(prof.path, { label: prof.label, sublabel: prof.sublabel });
        }
      }
    }
  }
  for (const cat of ['printers', 'displays', 'workingspaces']) {
    for (const prof of tree.repo[cat]) {
      m.set(prof.path, { label: prof.label, sublabel: prof.sublabel });
    }
  }
  for (const prof of (tree.z9Flat || [])) {
    m.set(prof.path, { label: prof.label, sublabel: prof.sublabel });
  }
  return m;
}
