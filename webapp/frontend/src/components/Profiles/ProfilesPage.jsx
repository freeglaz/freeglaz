import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw, Plus, AlertTriangle, GitCompare } from 'lucide-react';
import { useProfilesStore } from '../../hooks/useProfilesStore.js';
import ProfilesFiltersSidebar from './ProfilesFiltersSidebar.jsx';
import ProfilesList from './ProfilesList.jsx';
import ProfileDetailPanel from './ProfileDetailPanel.jsx';
import ImportProfileDialog from './ImportProfileDialog.jsx';
import ProfileCompareModal from './ProfileCompareModal.jsx';
import ArgyllProfileWizard from '../Charts/ArgyllProfileWizard.jsx';
import ProfileInspectorModal from '../ProfileInspector/ProfileInspectorModal.jsx';

/**
 * Profiles page (17.2).
 *
 * 3-column layout (Papers P1 model):
 *  [FiltersSidebar 260px] [ProfilesList flex-1] [DetailPanel 640px slide-over]
 *
 * Two distinct zones:
 *  - Z9 mirror (read-only, lock, copy-to-repo)
 *  - Personal repo (read/write, import/delete/move via the import modal and the DetailPanel)
 *
 * Browsable offline — the local store is the source of truth. The Z9 is
 * only required for `Sync`.
 */
export default function ProfilesPage() {
  const { t } = useTranslation();
  const {
    store, z9, loading, error, reload,
    syncMirror, syncing, syncResult, syncError,
  } = useProfilesStore();

  // #3 — on entering the page, refresh the mirror to show the freshest
  // possible slot state. NORMAL sync (fast: version check + fetch of
  // added/removed papers). We do NOT FORCE here: a forced sync = full
  // re-export of ALL slots (~minutes) → unacceptable on every open.
  // IN-APP profile changes are already refreshed by the refetch-on-event
  // (#1); for a front-panel change, the "Refresh" button forces a full
  // re-verification.
  useEffect(() => {
    syncMirror({ force: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Filters
  const [filterZone, setFilterZone] = useState('all');   // 'all' | 'mirror' | 'repo'
  const [filterCategory, setFilterCategory] = useState('all'); // 'all' | 'printers' | 'displays' | 'workingspaces'
  const [filterDevice, setFilterDevice] = useState('all');     // 'all' | <device name>
  const [filterTags, setFilterTags] = useState([]);            // selected classification tags (repo_z9)
  const [filterTagMode, setFilterTagMode] = useState('and');   // 'and' (all) | 'or' (at least one)
  const toggleFilterTag = (tg) => setFilterTags((cur) =>
    cur.includes(tg) ? cur.filter((x) => x !== tg) : [...cur, tg]);
  const [search, setSearch] = useState('');

  // Selection (detail panel)
  const [selected, setSelected] = useState(null);
  const closeDetail = () => setSelected(null);

  // Import modal
  const [importOpen, setImportOpen] = useState(false);

  // Compare modal (profile-compare). `compareSeedPaths` pre-seeds the
  // selection when opened from the inspector ("Compare to…").
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareSeedPaths, setCompareSeedPaths] = useState(null);
  const openCompare = (seed = null) => {
    setCompareSeedPaths(seed);
    setCompareOpen(true);
  };
  const closeCompare = () => {
    setCompareOpen(false);
    setCompareSeedPaths(null);
  };

  // INDEPENDENT validation (profcheck b, Mechanism 1) — reuses the chart wizard
  // (composes an independent chart → print → scan → terminal profcheck). Target = a
  // printer profile tied to a Z9 paper (mirror/repo z9). Paper/GE DERIVED from the profile.
  const [validateTarget, setValidateTarget] = useState(null);
  const openValidate = (sel) => {
    closeDetail();
    setValidateTarget({
      paper: { media_id: sel.mediaId || sel.media_id, name: sel.paperName || sel.paper_name },
      ge: sel.gloss_enhancer || sel.gloss_slot,
      profile: { absolute_path: sel.absolute_path || sel.path,
                 label: sel.label || sel.filename, filename: sel.filename },
    });
  };
  const closeValidate = () => setValidateTarget(null);

  // Inspector modal (reuses 16)
  const [inspectSource, setInspectSource] = useState(null);
  const openInspector = (absPath) => setInspectSource({ path: absPath });
  const closeInspector = () => setInspectSource(null);
  // "Compare to…" from the inspector: closes the inspector, opens the
  // compare modal with this profile pre-selected.
  const compareFromInspector = (absPath) => {
    closeInspector();
    openCompare([absPath]);
  };

  // List of repo devices for the selector (printers/<device>/)
  const repoDevices = useMemo(() => {
    if (!store?.repo?.printers) return [];
    const s = new Set();
    for (const p of store.repo.printers) {
      if (p.device) s.add(p.device);
    }
    return Array.from(s).sort();
  }, [store]);

  // Applying the filters
  const filtered = useMemo(() => {
    if (!store) return null;
    const q = search.trim().toLowerCase();
    const matchSearch = (txt) => !q || (txt || '').toLowerCase().includes(q);

    // Tags filter (repo_z9 ONLY): AND = all included, OR = at least one. If tags are
    // active, we hide mirror + repo-devices (they have no purpose_tags).
    const tagsActive = filterTags.length > 0;
    const matchTags = (prof) => !tagsActive || (filterTagMode === 'and'
      ? filterTags.every((tg) => (prof.purpose_tags || []).includes(tg))
      : filterTags.some((tg) => (prof.purpose_tags || []).includes(tg)));

    // Personal Z9 space: we normalize `path` → `absolute_path` (the key
    // expected by ProfileRow for selection/picker) without altering the
    // other sidecar fields. Subject to the same zone/search filters as the repo.
    const z9Serials = (filterZone === 'mirror'
      || (filterCategory !== 'all' && filterCategory !== 'z9'))
      ? []
      : (z9?.serials || []).map((s) => ({
        ...s,
        papers: (s.papers || []).map((p) => ({
          ...p,
          profiles: (p.profiles || [])
            .map((prof) => ({ ...prof, absolute_path: prof.path }))
            .filter((prof) =>
              (matchSearch(p.paper_name) || matchSearch(prof.label)
               || matchSearch(prof.filename)) && matchTags(prof)),
        })).filter((p) => p.profiles.length > 0),
      })).filter((s) => s.papers.length > 0);

    const out = {
      z9: z9Serials,
      mirrors: (filterZone === 'repo' || tagsActive) ? [] : (store.mirrors || []).map((m) => ({
        ...m,
        papers: (m.papers || []).map((p) => ({
          ...p,
          profiles: (p.profiles || []).filter((prof) =>
            matchSearch(p.paper_name) || matchSearch(prof.filename)
            || matchSearch(prof.z9_icc_name),
          ),
        })).filter((p) => p.profiles.length > 0),
      })).filter((m) => m.papers.length > 0),
      repo: {
        printers: tagsActive || filterZone === 'mirror' || (filterCategory !== 'all' && filterCategory !== 'printers')
          ? [] : (store.repo?.printers || []).filter((p) =>
            (filterDevice === 'all' || p.device === filterDevice)
            && (matchSearch(p.display_name) || matchSearch(p.filename)),
          ),
        displays: tagsActive || filterZone === 'mirror' || (filterCategory !== 'all' && filterCategory !== 'displays')
          ? [] : (store.repo?.displays || []).filter((p) =>
            matchSearch(p.display_name) || matchSearch(p.filename),
          ),
        workingspaces: tagsActive || filterZone === 'mirror' || (filterCategory !== 'all' && filterCategory !== 'workingspaces')
          ? [] : (store.repo?.workingspaces || []).filter((p) =>
            matchSearch(p.display_name) || matchSearch(p.filename),
          ),
      },
    };
    return out;
  }, [store, z9, filterZone, filterCategory, filterDevice, search, filterTags, filterTagMode]);

  // Union of classification tags (purpose_tags) of ALL repo_z9 profiles (edit
  // autocomplete + filter list). Derived from the full store (independent of the current filters).
  const allTags = useMemo(() => {
    const set = new Set();
    for (const s of (z9?.serials || []))
      for (const p of (s.papers || []))
        for (const prof of (p.profiles || []))
          for (const tg of (prof.purpose_tags || [])) set.add(tg);
    return [...set].sort((a, b) => a.localeCompare(b));
  }, [z9]);

  const hasMirror = (store?.mirrors || []).length > 0;
  const firstMirror = hasMirror ? store.mirrors[0] : null;

  return (
    <>
    <div className="flex-1 flex min-h-0">
      <ProfilesFiltersSidebar
        filterZone={filterZone} setFilterZone={setFilterZone}
        filterCategory={filterCategory} setFilterCategory={setFilterCategory}
        filterDevice={filterDevice} setFilterDevice={setFilterDevice}
        repoDevices={repoDevices}
        search={search} setSearch={setSearch}
        allTags={allTags} filterTags={filterTags} onToggleTag={toggleFilterTag}
        filterTagMode={filterTagMode} setFilterTagMode={setFilterTagMode}
        store={store} z9={z9}/>

      <main className="flex-1 min-w-0 flex flex-col bg-bg overflow-hidden">
        <Toolbar
          mirror={firstMirror}
          syncing={syncing}
          syncResult={syncResult}
          syncError={syncError}
          onSync={() => syncMirror({ force: true })}
          onImport={() => setImportOpen(true)}
          onCompare={() => openCompare(null)}/>

        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="flex items-center justify-center py-16 text-text-muted text-xs2">
              <span className="inline-block w-3.5 h-3.5 border-2 border-accent
                               border-t-transparent rounded-full animate-spin mr-2"
                    aria-hidden="true"/>
              {t('profils.loading')}
            </div>
          )}
          {error && (
            <div className="m-4 p-3 bg-danger/10 border border-danger/30 rounded
                            text-danger text-xs2 flex items-start gap-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0"/>
              <div>
                <div className="font-medium">{t('profils.error_title')}</div>
                <div className="font-mono">{error}</div>
              </div>
            </div>
          )}
          {!loading && !error && filtered && (
            <ProfilesList
              filtered={filtered}
              selected={selected}
              onSelect={setSelected}
              isEmptyMirror={!hasMirror}/>
          )}
        </div>
      </main>

      <ProfileDetailPanel
        selected={selected}
        onClose={closeDetail}
        onOpenInspector={(abs) => openInspector(abs)}
        onValidate={openValidate}
        onAction={async () => { await reload(); closeDetail(); }}
        allTags={allTags} onStoreRefresh={reload}/>
    </div>

    {importOpen && (
      <ImportProfileDialog
        onClose={() => setImportOpen(false)}
        onUploaded={async () => {
          setImportOpen(false);
          await reload();
        }}/>
    )}

    <ProfileInspectorModal
      open={!!inspectSource}
      onClose={closeInspector}
      source={inspectSource}
      onCompare={compareFromInspector}/>

    <ProfileCompareModal
      open={compareOpen}
      onClose={closeCompare}
      store={store}
      z9={z9}
      seedPaths={compareSeedPaths}/>


    <ArgyllProfileWizard
      open={!!validateTarget}
      mode="validate"
      paper={validateTarget?.paper}
      ge={validateTarget?.ge}
      profile={validateTarget?.profile}
      onClose={closeValidate}/>
    </>
  );
}


function Toolbar({
  mirror, syncing, syncResult, syncError, onSync, onImport, onCompare,
}) {
  const { t } = useTranslation();
  let syncLabel;
  if (syncing) {
    syncLabel = t('profils.syncing');
  } else if (syncError) {
    syncLabel = t('profils.sync_offline');
  } else if (mirror?.last_sync_at) {
    syncLabel = t('profils.sync_last', { when: _formatRelative(mirror.last_sync_at, t) });
  } else {
    syncLabel = t('profils.sync_never');
  }
  return (
    <div className="sticky top-0 z-10 bg-surface border-b border-border-soft
                    px-5 h-[75px] flex items-center gap-3">
      <button
        type="button"
        onClick={onImport}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs2
                   bg-accent/10 text-accent hover:bg-accent/15 rounded-md
                   font-medium transition-colors">
        <Plus size={14} strokeWidth={2.2}/>
        {t('profils.toolbar_import')}
      </button>
      <button
        type="button"
        onClick={onCompare}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs2
                   bg-accent/10 text-accent hover:bg-accent/15 rounded-md
                   font-medium transition-colors">
        <GitCompare size={13}/>
        {t('profils.toolbar_compare')}
      </button>
      <div className="flex-1"/>
      {/* #5 — sync status dot: green = fresh (Z9 reachable), amber =
          unverified / offline (local cache OK for browsing, but freshness
          not guaranteed — uncertainty, not danger), pulsing blue = in
          progress. Browsing always allowed offline (cache = local truth). */}
      <div className="flex items-center gap-1.5 text-tiny text-text-faint">
        <span
          className={`inline-block w-2 h-2 rounded-full ${
            syncing ? 'bg-accent animate-pulse'
              : syncError ? 'bg-icc-warn'
                : mirror?.last_sync_at ? 'bg-icc-ok'
                  : 'bg-icc-warn'
          }`}/>
        {syncLabel}
      </div>
      <button
        type="button"
        onClick={onSync}
        disabled={syncing}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs2
                   bg-accent/10 text-accent hover:bg-accent/15 rounded-md
                   font-medium transition-colors disabled:opacity-50">
        <RefreshCw size={12} className={syncing ? 'animate-spin' : ''}/>
        {t('profils.toolbar_sync')}
      </button>
      {syncResult?.changed && !syncing && (
        <span className="text-tiny text-success font-mono">
          ✓ {syncResult.n_profiles_fetched} {t('profils.sync_profiles_fetched')}
        </span>
      )}
    </div>
  );
}


function _formatRelative(iso, t) {
  try {
    const d = new Date(iso);
    const min = Math.round((Date.now() - d.getTime()) / 60000);
    if (min < 1) return t('profils.rel_now');
    if (min < 60) return t('profils.rel_minutes', { count: min });
    const h = Math.round(min / 60);
    if (h < 24) return t('profils.rel_hours', { count: h });
    const j = Math.round(h / 24);
    return t('profils.rel_days', { count: j });
  } catch {
    return iso;
  }
}
