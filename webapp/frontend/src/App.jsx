import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import Viewer from './components/Viewer/Viewer.jsx';
import Sidebar from './components/Sidebar/Sidebar.jsx';
import StatusBar from './components/StatusBar/StatusBar.jsx';
import ConfirmModal from './components/ui/ConfirmModal.jsx';
import JobQueuePanel from './components/JobQueue/JobQueuePanel.jsx';
import { CheckCircle2, XCircle } from 'lucide-react';
import { useStatus } from './hooks/useStatus.js';
import { usePrintJob } from './hooks/usePrintJob.js';
import { useFileLoader } from './hooks/useFileLoader.js';
import { useTheme } from './hooks/useTheme.js';
import { useQueue } from './hooks/useQueue.js';
import { useRoute } from './hooks/useRoute.js';
import { takeDroppedFile } from './lib/fileIO.js';
import TopNav from './components/TopNav/TopNav.jsx';
import PapersPage from './components/Papers/PapersPage.jsx';
import SettingsPage from './components/Settings/SettingsPage.jsx';
import PrintersModal from './components/Settings/PrintersModal.jsx';
import LogsPage from './components/Logs/LogsPage.jsx';
import ProfilesPage from './components/Profiles/ProfilesPage.jsx';
import MesuresPage from './components/Mesures/MesuresPage.jsx';
import ConvertPage from './components/Convert/ConvertPage.jsx';
import Splash from './components/Splash/Splash.jsx';
import { LoadedPaperProvider } from './hooks/useLoadedPaper.js';
import { deriveUIState, isWorkerActive, UIState } from './lib/state-machine.js';
import { postPrint, postPrintCancel, wakeZ9, disableDemo } from './api/client.js';
import { uiGEToBackend } from './api/mappings.js';
import { rollCenterX, isCenteredX } from './lib/placement.js';

const DEFAULT_PARAMS = { gloss: 'image', quality: 'HIGH', copies: 1 };
// `positionX` / `positionY` are **absolute positions in mm from the
// top-left corner of the sheet**, consistent with native PJL Z9 and the
// graphics standards (Affinity, InDesign, Photoshop). `null` = auto-centered
// position (computed from the paper × image dimensions at
// render time). For the backend payload, we convert to a delta from the
// default centering via `positionToOffset` at send time.
// Cf. bug B13 (Docs/freeglaz_Webapp_Roadmap.md).
const DEFAULT_ADVANCED = {
  positionX: null, positionY: null,  // null = auto-centered
  maxDetail: true,
  dryTime: 'Normal',
  renderMode: 'Couleur',
  // Orientation of the image content, degrees (0|90|180|270). `orientationTouched`
  // locks the auto-fit: as soon as the user rotates manually, the auto
  // never takes over again (otherwise it would re-rotate the image on recompute).
  orientation: 0,
  orientationTouched: false,
};

/** Auto center (mm) — null if paper / image dims unavailable. */
function autoCenter(sheet_mm, image_mm) {
  if (typeof sheet_mm !== 'number' || typeof image_mm !== 'number') return 0;
  return (sheet_mm - image_mm) / 2;
}

/** Suggested orientation (auto-fit): 90 if the image and the sheet do not have the
 * same landscape/portrait orientation, 0 otherwise. Returns 0 if dims are incomplete.
 * SHEET only (the ROLL is ambiguous, handled on the caller side). */
function suggestOrientation(sheet_w, sheet_h, image_w, image_h) {
  const ok = [sheet_w, sheet_h, image_w, image_h].every(
    (n) => typeof n === 'number' && n > 0,
  );
  if (!ok) return 0;
  return (sheet_w >= sheet_h) === (image_w >= image_h) ? 0 : 90;
}

// Mapping UI state (camelCase + FR labels) → backend PrintParams
// (snake_case + enums). Centralized here rather than scattered. The
// absolute position → delta conversion uses the effective dimensions passed as
// arguments — the Pydantic backend always expects a delta from the
// default centering (PrintParams.offset_x_mm), webapp API unchanged.
function toBackendParams(params, advanced, autoX, autoY) {
  const effX = advanced.positionX ?? autoX;
  const effY = advanced.positionY ?? autoY;
  return {
    gloss_enhancer: uiGEToBackend(params.gloss),  // 'image' → 'FULLPAGE', 'off' → 'OFF'
    quality:        params.quality,
    copies:         params.copies,
    offset_x_mm:    effX - autoX,  // delta for the backend
    offset_y_mm:    effY - autoY,
    orientation:    advanced.orientation,
    max_detail:     advanced.maxDetail ? 'ON' : 'OFF',
    drytime:        advanced.dryTime === 'Étendu' ? 'EXTENDED' : 'NORMAL',
    rendermode:     advanced.renderMode === 'Niveaux de gris' ? 'GRAYSCALE' : 'COLOR',
  };
}

export default function App() {
  const { t } = useTranslation();
  useTheme(); // applies the .dark class on <html>

  const { path, navigate } = useRoute();
  const { status, stale, reconnect } = useStatus();
  const { file, error, load, loadFromId, clear, updatePreview, loading: fileLoading } = useFileLoader();
  const loadRef = useRef(load);   // stable handle for the Dock-drop listener (registered once, below)
  loadRef.current = load;
  const queue                  = useQueue();
  const [queueOpen, setQueueOpen] = useState(false);
  const queueTriggerRef           = useRef(null);

  // Boot from `freeglaz open <file>`: the CLI has already uploaded the file
  // (POST /api/files) and opened the browser at /print?file_id=…&name=…
  // One-shot on mount: we hydrate the state from that file_id (skips the
  // postFile), exactly like after a drag → same state, same render. Then
  // we remove the query from the URL (replaceState) so that a refresh does not retry
  // a load (consistent with the hash cleanup on the Papers page).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const bootFileId = params.get('file_id');
    if (!bootFileId) return;
    const bootName = params.get('name') || undefined;
    loadFromId(bootFileId, bootName);
    window.history.replaceState(null, '', window.location.pathname);
  }, [loadFromId]);

  // Desktop/macOS: a TIFF dropped on the Dock icon. The native side (webapp/
  // desktop.py) stored the file and fired `freeglaz:open-file`; we pull the
  // bytes and feed them to the SAME loader as a drag into the window. Registered
  // once; inert in a browser, where the event never fires.
  useEffect(() => {
    async function onOpenFile() {
      const dropped = await takeDroppedFile();
      if (!dropped) return;
      loadRef.current(new File([dropped.bytes], dropped.name, { type: 'image/tiff' }));
    }
    window.addEventListener('freeglaz:open-file', onOpenFile);
    return () => window.removeEventListener('freeglaz:open-file', onOpenFile);
  }, []);

  // P4: Z9 wake-up. waking=true during the operation (blocks the button),
  // wakeNotice carries the post-operation toast (success or failure).
  const [waking, setWaking]       = useState(false);
  const [wakeNotice, setWakeNotice] = useState(null);  // { kind: 'success'|'error', message }

  // IP configuration onboarding: z9_configured === false (DEDICATED signal, distinct from
  // offline) = NO printer resolved → auto-opening of the modal (1×) +
  // graceful blocking banner. status undefined or configured (.env/store.json) ⇒
  // no onboarding (dev case of a user with Z9_HOST).
  const unconfigured = status?.z9_configured === false;
  const [printersOnboarding, setPrintersOnboarding] = useState(false);
  const autoOpenedRef = useRef(false);

  // Argyll CMS availability (resolved at backend startup, carried on status).
  // Non-blocking: the app runs, but profiling features are unavailable until
  // fixed. Dismissible for the session.
  const [argyllDismissed, setArgyllDismissed] = useState(false);
  const argyllMissing = status?.argyll && !status.argyll.ok && !argyllDismissed;
  useEffect(() => {
    if (unconfigured && !autoOpenedRef.current) {
      autoOpenedRef.current = true;
      setPrintersOnboarding(true);
    } else if (!unconfigured) {
      autoOpenedRef.current = false;     // reconfigured → re-armable if it becomes empty again
    }
  }, [unconfigured]);

  // Leave offline demo mode → back to the real client (or onboarding if none).
  // Reload for the same reason as entering demo: the existing status SSE stays
  // bound to the old subscriber, so a fresh page gets the post-demo state.
  const handleExitDemo = async () => {
    try {
      await disableDemo();
    } finally {
      window.location.reload();
    }
  };

  const handleWake = async () => {
    if (waking) return;
    setWaking(true);
    try {
      const res = await wakeZ9();
      if (res.ok && res.status === 'awake') {
        setWakeNotice({ kind: 'success',
                        message: t('wake.toast_awake', { s: res.elapsed_seconds }) });
        // Force a status + queue refresh so the UI goes back to "Ready"
        queue.refresh?.();
      } else if (res.ok && res.status === 'timeout') {
        setWakeNotice({ kind: 'error',
                        message: res.detail || t('wake.timeout') });
      } else if (res.ok && res.status === 'unreachable') {
        setWakeNotice({ kind: 'error',
                        message: res.detail || t('wake.unreachable') });
      } else {
        setWakeNotice({ kind: 'error',
                        message: res.message || t('wake.unreachable') });
      }
    } finally {
      setWaking(false);
      // Auto-dismiss toast after 5s (leaves time to read the detail)
      setTimeout(() => setWakeNotice(null), 5000);
    }
  };

  // Global ⌘J / Ctrl+J shortcut to toggle the queue panel (spec §1)
  useEffect(() => {
    const onKey = (e) => {
      // ⌘J on macOS, Ctrl+J on Linux/Windows. metaKey = ⌘; ctrlKey = Ctrl.
      // We don't trigger if focus is on an input (the user may be typing).
      const target = e.target;
      const isInput = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA');
      if (isInput) return;
      if ((e.metaKey || e.ctrlKey) && e.key === 'j' && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        setQueueOpen((o) => !o);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const [params,   setParams]   = useState(DEFAULT_PARAMS);
  const [advanced, setAdvanced] = useState(DEFAULT_ADVANCED);
  const [jobId,    setJobId]    = useState(null);
  const { progress, state: jobState, error: jobError } = usePrintJob(jobId);
  // Confirmation modal for Cancel
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [confirmingPrint, setConfirmingPrint]   = useState(false);
  // Info toast for the 409 post_send_no_remote_cancel case
  const [cancelNotice, setCancelNotice] = useState(null);

  // Success toast « Print sent ✓ » shown for ~2.5 s after the
  // worker DONE, just before the UI reset to A_EMPTY (B16).
  const [sentNotice, setSentNotice] = useState(null);
  // errNotice carries a persistent print-failure message (backend reason).
  const [errNotice, setErrNotice] = useState(null);
  // Ref to drive the post-send reset timer: we capture the ID of the
  // printed file so the reset does NOT overwrite a new file
  // that the user may have dropped during the 2.5 s feedback.
  const printedFileIdRef = useRef(null);
  const currentFileIdRef = useRef(null);

  // Auto center for the current paper / image pair. Used both
  // for the render (display position in the sidebar when positionX is null)
  // and for the absolute → delta conversion for the backend payload.
  const sheet_w = status?.paper?.width_mm;
  const sheet_h = status?.paper?.height_mm;
  const image_w = file?.info?.width_mm;
  const image_h = file?.info?.height_mm;
  // EFFECTIVE dims according to the orientation: 90/270 swaps w/h. The auto center
  // must be computed on the rotated footprint to stay consistent with
  // compute_geometry on the backend (which receives the transposed dims).
  const oriented = advanced.orientation === 90 || advanced.orientation === 270;
  const effImageW = oriented ? image_h : image_w;
  const effImageH = oriented ? image_w : image_h;
  // Approach B: the auto X AND Y anchors come from the BACKEND (single source =
  // exact PDF/PRN value). Economical ROLL = 5 mm (flush left/top), SHEET =
  // the center. We no longer recompute them on the front — it was this « SHEET-
  // style » recompute that lied in ROLL (X: 254.8 instead of 5; Y: 0 instead of 5,
  // because height_mm is null in ROLL → autoCenter returned 0). autoCenter
  // fallback only before the 1st preview (transient; positionX/Y are
  // null at that point → the delta is 0 whatever the value). The backend
  // placement stays UNCHANGED (GE-safe): we expose + consume, nothing else.
  const autoX = file?.preview?.geometry?.auto_x_mm ?? autoCenter(sheet_w, effImageW);
  const autoY = file?.preview?.geometry?.auto_y_mm ?? autoCenter(sheet_h, effImageH);

  const isRoll = status?.paper?.kind === 'roll';
  const geom = file?.preview?.geometry;
  // "Stale geometry": the current preview was computed for an orientation
  // AND/OR a paper DIFFERENT from the current selection. The paper change is
  // not instantaneous (re-fetch), and rotation transposes the footprint on the
  // front before the backend responds. While it is stale, we trust NEITHER the
  // fit NOR the centering: Print is disabled and the centering verdict is
  // frozen. It clears on its own when the fresh preview (current orientation +
  // paper) arrives (geom coincides again). Comparisons on the backend dims
  // (image_width_mm/sheet_*) vs the current values; 0.5 mm tolerance against
  // float noise.
  const orientStale = Boolean(geom && effImageW != null
    && Math.abs((geom.image_width_mm ?? effImageW) - effImageW) > 0.5);
  const paperStale = Boolean(geom && status?.paper && (
    ((geom.media_source === 'ROLL') !== isRoll)
    || (sheet_w != null && Math.abs((geom.sheet_width_mm ?? sheet_w) - sheet_w) > 0.5)
    // ROLL: sheet_height is derived from the image (changes with it) → we only
    // compare height in sheet mode.
    || (!isRoll && sheet_h != null
        && Math.abs((geom.sheet_height_mm ?? sheet_h) - sheet_h) > 0.5)
  ));
  const geomStale = Boolean(file?.preview && (orientStale || paperStale));

  // Roll = fixed width, free length: X centering defined, Y not (no reference
  // height). X center derived from the BACKEND dims
  // (sheet_width_mm/image_width_mm) — SAME source as effX/autoX below → the two
  // operands of isCenteredX have the SAME freshness (no more intermittent false
  // verdict after rotation). Front fallback before the 1st preview.
  const rollCenterX_mm = isRoll
    ? rollCenterX(geom?.sheet_width_mm ?? sheet_w, geom?.image_width_mm ?? effImageW)
    : null;
  // Effective X position actually used (positionX takes precedence over the auto anchor).
  const effX = advanced.positionX ?? autoX;
  // Centered now? (sheet: both axes null = auto; roll: X on the horizontal
  // center). Values of the same freshness (all backend/effX).
  const isCenteredNow = isRoll
    ? isCenteredX(effX, rollCenterX_mm)
    : (advanced.positionX == null && advanced.positionY == null);
  // "Center" button: greyed out ONLY when we are CERTAIN it is already
  // centered = FRESH geometry AND centered. While it is stale (recompute window
  // after rotation/paper change), we do NOT grey it out — the user must be able
  // to (re)center; wrongly greyed = the "2nd recentering blocked" bug observed.
  // (The safe-by-default guard on stale data concerns Print, not Center.)
  const centerDisabled = !geomStale && isCenteredNow;
  // Honest "centered" indicator (Viewer): lit only if FRESH and actually
  // centered. Sheet = offset-aware backend flags; roll = X only.
  const centeredIndicator = !geomStale && (isRoll
    ? isCenteredNow
    : Boolean(geom?.centered_x && geom?.centered_y));

  // Re-fetch /api/print/preview when the user changes the print
  // parameters (position, gloss, quality, etc.). Debounce 300 ms: avoids
  // spamming the backend on every keystroke in the numeric inputs. The
  // setTimeout is cleanly cancelled on every change (useEffect cleanup).
  const fileId = file?.info?.id;
  // STABLE paper identity for the re-fetch dep: a change of loaded paper
  // (format/type/roll↔sheet) must trigger a geometry recompute — otherwise the
  // preview + overflow guard stay stale (Bug A). ⚠️ NOT the `status.paper`
  // object itself (re-created on every SSE poll → infinite re-fetch). Rounded
  // dims absorb float noise across polls while still changing on a real change.
  const paperKey = status?.paper
    ? `${status.paper.mediaid}|${status.paper.kind}|`
      + `${Math.round(status.paper.width_mm || 0)}|${Math.round(status.paper.height_mm || 0)}`
    : null;
  useEffect(() => {
    if (!fileId) return;
    const handle = setTimeout(() => {
      updatePreview(toBackendParams(params, advanced, autoX, autoY), fileId);
    }, 300);
    return () => clearTimeout(handle);
    // paperKey: re-fetch on a paper change (Bug A). Stable string, not the object.
  }, [fileId, params, advanced, autoX, autoY, updatePreview, paperKey]);

  // GE state hygiene (O1): a paper the firmware reports as NOT
  // gloss-enhancer-capable must not keep a residual gloss='image' inherited
  // from a previous (capable) paper. The control is also disabled in the UI,
  // but the state itself is forced back to 'off' so the sent job is coherent.
  useEffect(() => {
    // Default-False: a paper not explicitly GE-capable (False OR unknown) must
    // not keep gloss='image'. Only an explicitly capable paper keeps the choice.
    if (status?.paper && status.paper.gloss_enhancer_supported !== true
        && params.gloss !== 'off') {
      setParams((p) => ({ ...p, gloss: 'off' }));
    }
  }, [status?.paper, status?.paper?.gloss_enhancer_supported, params.gloss]);

  // Auto-fit orientation — on LOADING a new image. SHEET
  // only (the ROLL is ambiguous → orientation 0, manual only). Also resets
  // orientationTouched to false: each new image re-arms the auto.
  // Deliberately keyed on `fileId` alone (= new image): manual must
  // take precedence afterward, we don't want to re-rotate the image on every
  // dims/params recompute.
  useEffect(() => {
    if (!fileId) return;
    const kind = status?.paper?.kind || 'sheet';
    const sug = kind === 'sheet'
      ? suggestOrientation(sheet_w, sheet_h, image_w, image_h)
      : 0;
    setAdvanced((a) => ({
      ...a, orientation: sug, orientationTouched: false,
      positionX: null, positionY: null,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileId]);

  // Keep a synchronous ref of the current file so the post-send
  // reset timer (B16) can verify it won't overwrite a
  // file that the user may have dropped during the feedback.
  useEffect(() => { currentFileIdRef.current = file?.info?.id ?? null; }, [file?.info?.id]);

  // Worker DONE → toast "Print sent ✓" then UI reset to A_EMPTY (B16).
  // The worker finishes ~10 s after the PRN send to the Z9 buffer. The Z9
  // keeps physically printing for several minutes but the tracking
  // happens in the z9_activity sidebar / StatusBar — the main UI is
  // freed for a next job.
  //
  // Subtlety: if the user drops a new file during the
  // feedback (2.5 s window), we must NOT overwrite that new file.
  // We compare the printed vs current ID at the moment the timer fires.
  useEffect(() => {
    // FAILED: surface the reason (backend message/code) in a persistent
    // notice — never a silent reset. The user keeps their file to retry.
    if (jobState === 'failed') {
      setErrNotice(jobError || t('print.toast_error_generic'));
      setJobId(null);
      return;
    }
    // CANCELLED (user-initiated): just release the worker subscription,
    // no error feedback; the file is kept.
    if (jobState === 'cancelled') {
      setJobId(null);
      return;
    }
    if (jobState !== 'completed') return;

    // DONE: toast "Print sent ✓" then UI reset to A_EMPTY (B16).
    // NB: the timeout handle must NOT be named `t` — that shadows the i18n
    // `t` in this block and threw a ReferenceError (TDZ) on line above.
    printedFileIdRef.current = currentFileIdRef.current;
    setSentNotice(t('print.toast_sent'));
    const timer = setTimeout(() => {
      setSentNotice(null);
      setJobId(null);
      // Reset UI only if the user hasn't dropped something else
      // in the meantime. Otherwise we respect their new file.
      if (currentFileIdRef.current === printedFileIdRef.current) {
        clear();
      }
      printedFileIdRef.current = null;
    }, 2500);
    return () => clearTimeout(timer);
  }, [jobState, jobError, clear]);

  const workerActive = isWorkerActive(jobId, jobState);
  const state = deriveUIState({ status, file, workerActive });

  // 2 steps: click « Print » → confirmation modal.
  // If confirmed → backend call. The Enter key confirms the modal (cf.
  // ConfirmModal: autofocus + global Enter handler).
  function requestPrint() {
    if (!file) return;
    setConfirmingPrint(true);
  }

  async function handlePrintConfirmed() {
    setConfirmingPrint(false);
    if (!file) return;
    const { job_id } = await postPrint({
      file_id: file.info.id,
      params: toBackendParams(params, advanced, autoX, autoY),
    });
    setJobId(job_id);
  }

  // 2 steps: click « Cancel » → confirmation modal.
  // If confirmed → backend call.
  function requestCancel() {
    if (jobId) setConfirmingCancel(true);
  }

  async function handleCancelConfirmed() {
    setConfirmingCancel(false);
    if (!jobId) return;
    const result = await postPrintCancel(jobId);
    if (result.code === 'post_send_no_remote_cancel') {
      // Backend returns 409 — the print has gone through on the Z9, nothing left to
      // cancel remotely. Displays a clear message, keeps jobId so as not to
      // break the UI while the Z9 is still printing.
      setCancelNotice(result.message);
      return;
    }
    // ok or network-error case: releases the worker subscription.
    setJobId(null);
  }

  // Mouse drag on the image in the viewer. PaperPreview commits the
  // desired absolute position — we store it as-is in the state.
  // Avoids race conditions from fast double-drag (absolute commit rather
  // than cumulative delta).
  function handlePositionCommit(absX_mm, absY_mm, axis) {
    // Roll X-only drag (axis === 'x'): commit X, leave Y on auto (null).
    // Committing a Y here would FREEZE it at its current value — unwanted on a
    // free-length roll where Y has no meaningful reference. Sheet: both axes.
    setAdvanced((a) => axis === 'x'
      ? { ...a, positionX: absX_mm }
      : { ...a, positionX: absX_mm, positionY: absY_mm });
  }
  // Double-click in the viewer OR « Center » button in the sidebar.
  // Sheet: back to the auto-centered position (null = use the auto computation).
  // Roll: center HORIZONTALLY only (explicit X), Y stays auto (null) — a
  // free-length roll has no reference height, vertical centering is undefined.
  function handlePositionReset() {
    if (isRoll) {
      setAdvanced((a) => ({ ...a, positionX: rollCenterX_mm, positionY: null }));
    } else {
      setAdvanced((a) => ({ ...a, positionX: null, positionY: null }));
    }
  }
  // « rotate 90° » button: accumulate 0→90→180→270→0. Marks orientationTouched
  // → the auto-fit no longer takes over (manual = last word). We also switch
  // the position back to auto-centered: a rotated image no longer makes sense at
  // the old absolute position (transposed footprint).
  function handleRotate90() {
    setAdvanced((a) => ({
      ...a,
      orientation: (a.orientation + 90) % 360,
      orientationTouched: true,
      positionX: null, positionY: null,
    }));
  }

  // Simple routing Print / Papers / Settings.
  // StatusBar + JobQueuePanel + ConfirmModals stay common to the 3
  // pages (the user keeps access to the current queue).
  const isConvertRoute = path === '/convert';
  const isPapersRoute = path === '/papers';
  const isMesuresRoute = path === '/mesures';
  const isProfilsRoute = path === '/profils';
  const isLogsRoute = path === '/logs';
  const isSettingsRoute = path === '/settings';
  const [showSplash, setShowSplash] = useState(true);

  // Dynamic tab title
  useEffect(() => {
    document.title = isSettingsRoute
      ? `${t('settings.title')} — freeglaz`
      : isProfilsRoute
        ? t('nav.title_profils')
        : isPapersRoute
          ? t('nav.title_papers')
          : t('nav.title_print');
  }, [isPapersRoute, isProfilsRoute, isSettingsRoute, t]);

  return (
    <LoadedPaperProvider value={status?.paper || null}>
    <div className="flex flex-col h-screen bg-bg">
      {stale && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 px-4 py-2 text-sm font-medium bg-warn text-black">
          <span>{t('status_bar.stale_banner')}</span>
          <button
            type="button"
            onClick={reconnect}
            className="px-3 py-1 rounded bg-black/20 hover:bg-black/30 whitespace-nowrap">
            {t('common.retry')}
          </button>
        </div>
      )}
      {argyllMissing && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 px-4 py-2 text-sm font-medium bg-warn text-black">
          <span>{t('status_bar.argyll_banner', { missing: status.argyll.missing.join(', ') })}</span>
          <button
            type="button"
            onClick={() => setArgyllDismissed(true)}
            aria-label={t('common.close')}
            className="px-3 py-1 rounded bg-black/20 hover:bg-black/30 whitespace-nowrap">
            {t('common.close')}
          </button>
        </div>
      )}
      {unconfigured && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 px-4 py-2 text-sm font-medium bg-accent text-on-accent">
          <span>{t('settings.printers.blocked_message')}</span>
          <button
            type="button"
            onClick={() => setPrintersOnboarding(true)}
            className="px-3 py-1 rounded bg-black/20 hover:bg-black/30 whitespace-nowrap">
            {t('settings.printers.configure_button')}
          </button>
        </div>
      )}
      <PrintersModal open={printersOnboarding} onClose={() => setPrintersOnboarding(false)}/>
      <TopNav
        path={path}
        onNavigate={navigate}
        state={state}/>
      <div className="flex-1 flex min-h-0">
        {isConvertRoute ? (
          <ConvertPage
            paper={status?.paper}
            offline={(status?.alerts || []).some((a) => a.code === 'Z9_UNREACHABLE')}/>
        ) : isSettingsRoute ? (
          <SettingsPage/>
        ) : isLogsRoute ? (
          <LogsPage/>
        ) : isProfilsRoute ? (
          <ProfilesPage/>
        ) : isMesuresRoute ? (
          <MesuresPage
            loadedPaperMediaid={status?.paper?.mediaid || null}
            offline={(status?.alerts || []).some((a) => a.code === 'Z9_UNREACHABLE')}/>
        ) : isPapersRoute ? (
          <PapersPage
            loadedPaperMediaid={status?.paper?.mediaid || null}
            offline={(status?.alerts || []).some((a) => a.code === 'Z9_UNREACHABLE')}
            z9Configured={status?.z9_configured !== false}/>
        ) : (
          <>
            <Viewer
              state={state}
              file={file}
              paper={status?.paper}
              advanced={advanced}
              loading={fileLoading}
              autoX={autoX}
              autoY={autoY}
              centered={centeredIndicator}
              onFileDrop={load}
              onPositionCommit={handlePositionCommit}
              onPositionReset={handlePositionReset}/>
            <Sidebar
              state={state}
              paper={status?.paper}
              file={file}
              params={params}     onParamsChange={setParams}
              advanced={advanced} onAdvancedChange={setAdvanced}
              autoX={autoX}       autoY={autoY}
              onPositionReset={handlePositionReset}
              centerDisabled={centerDisabled}
              onRotate90={handleRotate90}
              progress={progress}
              geomStale={geomStale}
              onPrint={requestPrint}
              onCancel={requestCancel}/>
          </>
        )}
      </div>
      <StatusBar
        state={state}
        paper={status?.paper}
        inks={status?.inks}
        job={status?.current_job}
        progress={progress}
        z9Activity={status?.z9_activity}
        queueSnapshot={queue.snapshot}
        queueOpen={queueOpen}
        queueButtonRef={queueTriggerRef}
        onToggleQueue={() => setQueueOpen((o) => !o)}
        z9State={status?.z9_state}
        waking={waking}
        onWake={handleWake}
        onOpenCalibrationPaper={(mediaid) => {
          window.location.hash = `#paper=${mediaid}`;
          if (path !== '/papers') navigate('/papers');
        }}
        onOpenProfilingWizard={(job) => {
          window.location.hash = `#profile=${job.mediaid}`;
          if (path !== '/papers') navigate('/papers');
        }}
        demo={status?.demo === true}
        onExitDemo={handleExitDemo}/>
      {/* Drag-drop error toast, minimal */}
      {error && (
        <div
          role="alert"
          aria-live="assertive"
          className="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-danger text-white px-4 py-2 rounded-md shadow-lg text-sm">
          {error}
        </div>
      )}
      {/* Success toast « Print sent ✓ » shown ~2.5 s after the
          worker DONE, before the UI reset to A_EMPTY (B16). */}
      {sentNotice && (
        <div
          role="status"
          aria-live="polite"
          className="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-success text-white px-4 py-2.5 rounded-md shadow-lg text-sm font-medium flex items-center gap-2">
          <CheckCircle2 size={16} strokeWidth={2.5} aria-hidden="true"/>
          <span>{sentNotice}</span>
        </div>
      )}
      {/* Post-send info toast (printing on the Z9, no remote cancel) */}
      {cancelNotice && (
        <div
          role="status"
          aria-live="polite"
          className="fixed top-4 left-1/2 -translate-x-1/2 z-50 max-w-[520px] bg-surface border border-icc-warn/40 text-text-strong px-4 py-3 rounded-md shadow-lg text-sm flex items-start gap-3">
          <span className="flex-1 leading-snug">{cancelNotice}</span>
          <button
            type="button"
            onClick={() => setCancelNotice(null)}
            aria-label={t('common.close')}
            className="text-text-muted hover:text-text-strong text-xs font-medium">
            {t('common.ok')}
          </button>
        </div>
      )}
      {/* Print failure — persistent, carries the backend reason (R1). */}
      {errNotice && (
        <div
          role="alert"
          aria-live="assertive"
          className="fixed top-4 left-1/2 -translate-x-1/2 z-50 max-w-[520px] bg-surface border border-danger/50 text-text-strong px-4 py-3 rounded-md shadow-lg text-sm flex items-start gap-3">
          <XCircle size={16} strokeWidth={2.5} className="text-danger shrink-0 mt-0.5" aria-hidden="true"/>
          <div className="flex-1 leading-snug">
            <div className="font-medium">{t('print.toast_error')}</div>
            <div className="text-text-muted mt-0.5 break-words">{errNotice}</div>
          </div>
          <button
            type="button"
            onClick={() => setErrNotice(null)}
            aria-label={t('common.close')}
            className="text-text-muted hover:text-text-strong text-xs font-medium">
            {t('common.ok')}
          </button>
        </div>
      )}
      {/* P4: Z9 wake result toast (auto-dismiss 5s on the handler side) */}
      {wakeNotice && (
        <div
          role="status"
          aria-live="polite"
          className={`fixed top-4 left-1/2 -translate-x-1/2 z-50 max-w-[520px] px-4 py-2.5 rounded-md shadow-lg text-sm font-medium flex items-center gap-2 ${
            wakeNotice.kind === 'success'
              ? 'bg-success text-white'
              : 'bg-surface border border-danger/40 text-text-strong'
          }`}>
          <span className="flex-1 leading-snug">{wakeNotice.message}</span>
          <button
            type="button"
            onClick={() => setWakeNotice(null)}
            aria-label={t('common.close')}
            className={`text-xs font-medium ${wakeNotice.kind === 'success' ? 'text-white/80 hover:text-white' : 'text-text-muted hover:text-text-strong'}`}>
            {t('common.ok')}
          </button>
        </div>
      )}
      <JobQueuePanel
        open={queueOpen}
        onClose={() => setQueueOpen(false)}
        snapshot={queue.snapshot}
        actions={queue.actions}
        rediscovering={queue.rediscovering}
        restoreFocusRef={queueTriggerRef}/>
      <ConfirmModal
        open={confirmingCancel}
        title={t('print.modal_cancel_title')}
        message={t('print.modal_cancel_message')}
        confirmLabel={t('print.modal_cancel_confirm')}
        cancelLabel={t('print.modal_cancel_keep')}
        confirmKind="danger"
        onConfirm={handleCancelConfirmed}
        onCancel={() => setConfirmingCancel(false)}/>
      <ConfirmModal
        open={confirmingPrint}
        title={t('print.modal_print_confirm_title')}
        message={t('print.modal_print_confirm_message')}
        confirmLabel={t('print.modal_print_confirm_button')}
        cancelLabel={t('common.cancel')}
        confirmKind="primary"
        onConfirm={handlePrintConfirmed}
        onCancel={() => setConfirmingPrint(false)}/>

      {showSplash && <Splash onDismiss={() => setShowSplash(false)}/>}
    </div>
    </LoadedPaperProvider>
  );
}
