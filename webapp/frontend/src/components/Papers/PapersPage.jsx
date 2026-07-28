import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { usePapers } from '../../hooks/usePapers.js';
import { usePaperFilters } from '../../hooks/usePaperFilters.js';
import { useDetailPanel } from '../../hooks/useDetailPanel.js';
import { useLoadedPaper } from '../../hooks/useLoadedPaper.js';
import FiltersSidebar from './FiltersSidebar.jsx';
import FilterChips from './FilterChips.jsx';
import PapersList from './PapersList.jsx';
import PaperDetailPanel from './PaperDetailPanel.jsx';
import CreatePaperWizard from './CreatePaperWizard.jsx';
import ProfileWizard from '../ProfileWizard/ProfileWizard.jsx';
import ArgyllProfileWizard from '../Charts/ArgyllProfileWizard.jsx';
import * as api from '../../api/client.js';
import ModifyMechanicalWizard from '../ModifyMechanicalWizard/ModifyMechanicalWizard.jsx';
import LoadingState from './states/LoadingState.jsx';
import ErrorState from './states/ErrorState.jsx';
import EmptyState from './states/EmptyState.jsx';
import NoResultsState from './states/NoResultsState.jsx';

/**
 * Papers page (P1 — spec §1).
 *
 * 3-column layout:
 *  [FiltersSidebar 260px] [PapersList flex-1] [DetailPanel 640px slide-over]
 *
 * Handled states:
 *  - loading (1st fetch)
 *  - error (fetch failed)
 *  - empty (0 custom + 0 favorite)
 *  - no-results (restrictive filters leave nothing)
 *  - loaded (standard case)
 */
export default function PapersPage({ loadedPaperMediaid = null, offline = false, z9Configured = true }) {
  const { t } = useTranslation();
  const loadedPaper = useLoadedPaper();   // = status.paper (single source, App.jsx)
  const { papers, loading, error, refresh, toggleFavorite } = usePapers();
  const { state, dispatch, filterAndSort, chips, hasActiveFilters } = usePaperFilters();
  const detail = useDetailPanel();

  const [autoInstall, setAutoInstall] = useState(null);

  // Empty state: expand the HP factory section on button click
  const [forceFactoryOpen, setForceFactoryOpen] = useState(false);
  // P3.C2 + bugfix: creation wizard. State separate from
  // ``wizardDonor`` because the wizard can open without a
  // pre-filled donor (from the global "Create a custom paper" button in
  // the Custom section header). Donor pre-filled when triggered from
  // a paper's detail panel.
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardDonor, setWizardDonor] = useState(null);
  const openWizard = (donor = null) => {
    setWizardDonor(donor);
    setWizardOpen(true);
  };
  const closeWizard = () => {
    setWizardOpen(false);
    setWizardDonor(null);
    refresh();
  };
  // P5.C: ICC profiling wizard — same pattern as wizardOpen/wizardDonor.
  const [profileWizardOpen, setProfileWizardOpen] = useState(false);
  const [profileWizardPaper, setProfileWizardPaper] = useState(null);
  const [profileWizardGe, setProfileWizardGe] = useState(undefined);
  const openProfileWizard = (paper, ge) => {
    setProfileWizardPaper(paper);
    setProfileWizardGe(ge);
    setProfileWizardOpen(true);
  };
  // Argyll/custom profiling: same entry point as HP (action on the slot),
  // launched from the "Argyll" column of the HP session choice. Driven by {paper, ge}.
  const [argyllWizard, setArgyllWizard] = useState(null);   // { paper, ge, mode } | null
  const openArgyllWizard = (mode, paper, ge) => {
    closeProfileWizard();                                    // closes the HP column
    setArgyllWizard({ paper, ge, mode });
  };
  const closeArgyllWizard = () => setArgyllWizard(null);
  const closeProfileWizard = () => {
    setProfileWizardOpen(false);
    setProfileWizardPaper(null);
    setProfileWizardGe(undefined);
    refresh();
  };

  const [modifyWizardOpen, setModifyWizardOpen] = useState(false);
  const [modifyWizardPaper, setModifyWizardPaper] = useState(null);
  const openModifyWizard = (paper) => { setModifyWizardPaper(paper); setModifyWizardOpen(true); };
  const closeModifyWizard = () => { setModifyWizardOpen(false); setModifyWizardPaper(null); refresh(); };

  // One-off notice (favorite rollback error, ICC success/error).
  // App.jsx pattern — local state + auto-dismiss.
  // Shape: { kind: 'error' | 'success', message: string } or null.
  const [notice, setNotice] = useState(null);
  const noticeTimerRef = useRef(null);
  const showNotice = (kindOrMessage, maybeMessage) => {
    // Supports 2 signatures:
    //   showNotice(message) → kind=error (compat with legacy favorites call)
    //   showNotice(kind, message) → P2 ICC success/error toasts
    const next = maybeMessage !== undefined
      ? { kind: kindOrMessage, message: maybeMessage }
      : { kind: 'error', message: kindOrMessage };
    setNotice(next);
    if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = setTimeout(() => setNotice(null), 4000);
  };
  useEffect(() => () => {
    if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current);
  }, []);

  const handleSelect = (paper, event) => {
    // event may be null if called by other flows; we grab the
    // event's currentTarget so we can restore focus.
    const triggerEl = event?.currentTarget || event?.target || null;
    detail.open(paper, triggerEl);
  };

  // P4.C: open a paper referenced via the URL hash
  // (#paper=MEDIAID), used by the "CLC in progress" badge in the
  // status bar to bring the user back to the right detail panel.
  useEffect(() => {
    if (!papers || papers.length === 0) return;
    const tryOpenFromHash = () => {
      const hash = window.location.hash;
      const mi = hash.match(/^#paper=([0-9A-Fa-f]{2,32})&install=(off|on|single)$/);
      if (mi) {
        const target = papers.find((p) => p.mediaid === mi[1]);
        if (target) {
          setAutoInstall({ mediaid: mi[1], ge: mi[2] });
          detail.open(target);
          history.replaceState(null, '', window.location.pathname);
        }
        return;
      }
      const mp = hash.match(/^#paper=([0-9A-Fa-f]{2,32})$/);
      if (mp) {
        const target = papers.find((p) => p.mediaid === mp[1]);
        if (target) {
          detail.open(target);
          history.replaceState(null, '', window.location.pathname);
        }
        return;
      }
      const mpr = hash.match(/^#profile=([0-9A-Fa-f]{2,32})$/);
      if (mpr) {
        const target = papers.find((p) => p.mediaid === mpr[1]);
        if (target) {
          openProfileWizard(target);
          history.replaceState(null, '', window.location.pathname);
        }
        return;
      }
      if (hash === '#create-custom') {
        openWizard(null);
        history.replaceState(null, '', window.location.pathname);
      }
    };
    tryOpenFromHash();
    window.addEventListener('hashchange', tryOpenFromHash);
    return () => window.removeEventListener('hashchange', tryOpenFromHash);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [papers]);

  // Toggle favorite with error handling (toast). The hook does
  // the optimistic update + rollback; we just have to notify.
  const handleToggleFavorite = async (mediaid) => {
    const res = await toggleFavorite(mediaid);
    if (!res?.ok) {
      showNotice(res?.message
        ? t('papers.favorite_save_failed', { message: res.message })
        : t('papers.favorite_save_failed_generic'));
    }
  };

  // Resolves selected mediaid → current paper object (always the latest
  // paper state, optimistic updates included).
  const selectedPaper = useMemo(() => {
    if (!detail.selected || !papers) return null;
    return papers.find((p) => p.mediaid === detail.selected) || null;
  }, [detail.selected, papers]);

  // "N papers match" counter for the chips (filtered custom + favorites)
  const filteredCount = useMemo(() => {
    if (!papers) return 0;
    const filterable = papers.filter((p) => p.custom || p.favorite);
    return filterAndSort(filterable).length;
  }, [papers, filterAndSort]);

  // ── Global states ───────────────────────────────────────────────

  if (loading && !papers) {
    return (
      <div className="flex-1 flex min-w-0">
        <FiltersSidebar
          state={state} dispatch={dispatch}
          hasActiveFilters={hasActiveFilters}
          papers={[]}/>
        <LoadingState/>
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex-1 flex min-w-0">
        <FiltersSidebar
          state={state} dispatch={dispatch}
          hasActiveFilters={hasActiveFilters}
          papers={[]}/>
        <ErrorState message={error} onRetry={refresh}/>
      </div>
    );
  }

  const allPapers = papers || [];
  const customs = allPapers.filter((p) => p.custom);
  const favorites = allPapers.filter((p) => p.favorite);
  const isEmpty = customs.length === 0 && favorites.length === 0;

  // No results: there are customs/favorites but the filters let
  // nothing through. Different from empty (which is 0 custom firmware-side).
  const isNoResults = !isEmpty && hasActiveFilters && filteredCount === 0;

  return (
    <div className="flex-1 flex min-w-0">
      <FiltersSidebar
        state={state} dispatch={dispatch}
        hasActiveFilters={hasActiveFilters}
        papers={allPapers}/>

      <main className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center gap-3 px-[22px] h-[75px] border-b border-border-soft bg-surface sticky top-0 z-10">
          {/* Action on the LEFT, ghost style (no border) — consistent with
              "Import a profile" from Profiles. "Loaded paper" status line
              below (REAL state: loaded / none / Z9 unreachable). */}
          <div className="flex flex-col gap-0.5 flex-shrink-0">
            <button
              type="button"
              onClick={() => openWizard(null)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs2 font-medium
                         bg-accent/10 text-accent hover:bg-accent/15 rounded-md transition-colors self-start">
              <Plus size={14} strokeWidth={2.2}/>
              {t('nav.create_custom')}
            </button>
            <LoadedPaperStatusLine
              loadedPaper={loadedPaper} offline={offline} z9Configured={z9Configured}/>
          </div>
          <FilterChips
            chips={chips}
            matchCount={filteredCount}
            onRemove={(chip) => dispatch({ type: 'REMOVE_FILTER', kind: chip.kind, value: chip.value })}
            onClearAll={() => dispatch({ type: 'RESET' })}/>
          <div className="flex-1"/>
        </div>

        {isEmpty ? (
          <EmptyState onBrowseFactory={() => setForceFactoryOpen(true)}/>
        ) : isNoResults ? (
          <NoResultsState onReset={() => dispatch({ type: 'RESET' })}/>
        ) : (
          <PapersList
            papers={allPapers}
            filterAndSort={filterAndSort}
            selectedMediaid={detail.selected}
            onSelect={handleSelect}
            onToggleFavorite={handleToggleFavorite}
            onCreateCustom={openWizard}
            factoryDefaultOpen={forceFactoryOpen}/>
        )}
      </main>

      <PaperDetailPanel
        open={detail.isOpen}
        paper={selectedPaper}
        offline={offline}
        loadedPaperMediaid={loadedPaperMediaid}
        autoInstallSlot={autoInstall && selectedPaper && autoInstall.mediaid === selectedPaper.mediaid ? autoInstall.ge : null}
        onClose={() => { setAutoInstall(null); detail.close(); }}
        onToggleFavorite={handleToggleFavorite}
        onCreateCustom={openWizard}
        onProfilePaper={openProfileWizard}
        onModifyMechanical={openModifyWizard}
        onDeleted={() => {
          detail.close();
          refresh();
        }}
        onIccChanged={refresh}
        onNotice={showNotice}/>

      <CreatePaperWizard
        open={wizardOpen}
        initialDonor={wizardDonor}
        allPapers={allPapers}
        loadedPaperMediaid={loadedPaperMediaid}
        onClose={closeWizard}
        onCreated={() => { /* refresh done on close to avoid breaking the CLC SSE */ }}
        onNotice={showNotice}/>

      <ProfileWizard
        open={profileWizardOpen}
        paper={profileWizardPaper}
        initialGlossEnhancer={profileWizardGe}
        loadedPaperMediaid={loadedPaperMediaid}
        onClose={closeProfileWizard}
        onRequestCalibration={(paper) => {
          closeProfileWizard();
          detail.open(paper);
        }}
        onNavigateToPaper={(mediaid) => {
          window.location.hash = `#paper=${mediaid}`;
        }}
        onArgyll={(mode, ge) => openArgyllWizard(mode, profileWizardPaper, ge)}/>

      <ArgyllProfileWizard
        open={!!argyllWizard}
        paper={argyllWizard?.paper}
        ge={argyllWizard?.ge}
        mode={argyllWizard?.mode}
        onClose={closeArgyllWizard}
        onDone={() => refresh()}/>

      <ModifyMechanicalWizard
        open={modifyWizardOpen}
        paper={modifyWizardPaper}
        onClose={closeModifyWizard}
        onCalibrate={(p) => { closeModifyWizard(); detail.open(p); }}
        onProfile={(p) => { closeModifyWizard(); openProfileWizard(p); }}
        onChanged={refresh}/>

      {/* Notice toast (green success, red error) — App.jsx pattern */}
      {notice && (
        <div
          role={notice.kind === 'error' ? 'alert' : 'status'}
          aria-live={notice.kind === 'error' ? 'assertive' : 'polite'}
          className={`fixed top-4 left-1/2 -translate-x-1/2 z-[60] max-w-[520px] px-4 py-2.5 rounded-md shadow-lg text-sm font-medium flex items-center gap-2 ${
            notice.kind === 'success'
              ? 'bg-success text-white'
              : 'bg-surface border border-danger/40 text-text-strong'
          }`}>
          {notice.kind === 'success' ? (
            <CheckCircle2 size={14} strokeWidth={2.5} className="flex-shrink-0" aria-hidden="true"/>
          ) : (
            <AlertTriangle size={14} strokeWidth={2.5} className="text-danger flex-shrink-0" aria-hidden="true"/>
          )}
          <span className="flex-1 leading-snug">{notice.message}</span>
        </div>
      )}
    </div>
  );
}


/**
 * Header "loaded paper" status line (panel c). REAL state, 3 cases — distinguishes
 * "no paper" (online, confirmed empty) from "Z9 not responding" (unknown):
 *   - offline (Z9_UNREACHABLE) or not configured → unknown, NEVER "none"
 *   - a paper loaded → "Loaded paper: <name>"
 *   - online, nothing loaded → "No paper loaded"
 * Single source: loadedPaper = status.paper (cf. useLoadedPaper / inspector badge).
 */
function LoadedPaperStatusLine({ loadedPaper, offline, z9Configured }) {
  const { t } = useTranslation();
  let text;
  if (offline || !z9Configured) {
    text = t('papers.header_z9_unknown');
  } else if (loadedPaper) {
    text = t('papers.header_loaded', { name: loadedPaper.name });
  } else {
    text = t('papers.header_none');
  }
  return (
    <span className="text-tiny text-text-faint leading-tight truncate max-w-[280px]"
          title={text}>
      {text}
    </span>
  );
}
