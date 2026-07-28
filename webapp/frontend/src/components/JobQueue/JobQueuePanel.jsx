import { useEffect, useRef, useState } from 'react';
import { X, Layers, Pause, Play, AlertTriangle, CheckCircle2, Trash2, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import JobItem from './JobItem.jsx';
import JobPreviewPopover from './JobPreviewPopover.jsx';
import ConfirmModal from '../ui/ConfirmModal.jsx';

// P3.H: UX pivot hover → click. The preview opens on an explicit click
// on a job, no longer on hover. Avoids the flickering observed in live
// (loading state bug in the popover component) and protects the Z9
// against unintentional hammering (hover over N external jobs in
// a few seconds = N firmware fetches). The backend cache
// preview_cache (P3.H) adds a 2nd line of defense.
const PREVIEW_POPOVER_ID = 'jq-preview-popover';

/**
 * Right slide-over panel for the Z9 print queue (14 P1.C).
 *
 * Width 460 px, full height. Semi-transparent backdrop clickable
 * to close. Esc + ⌘J close (wiring P1.F).
 *
 * @param {object} p
 * @param {boolean} p.open
 * @param {() => void} p.onClose
 * @param {object|null} p.snapshot — snapshot /api/jobs (useQueue)
 * @param {object} p.actions — { pause, resume } from useQueue
 * @param {boolean} p.rediscovering — true if _meta.consecutive_failures > N
 * @param {React.ReactNode} p.children — list content (JobItem in P1.D)
 */
export default function JobQueuePanel({
  open, onClose,
  snapshot, actions, rediscovering = false,
  restoreFocusRef,  // ref to the element to refocus on close ("Queue" button)
}) {
  const { t } = useTranslation();
  // Local panel state: delete + reprint confirmation + reprint toast + selected preview (P3.H)
  const [confirmingRemove, setConfirmingRemove]   = useState(null); // job or null
  const [confirmingReprint, setConfirmingReprint] = useState(null); // job or null
  const [confirmingClear, setConfirmingClear]     = useState(false); // Patch 3 — clear the queue
  const [clearBusy, setClearBusy]               = useState(false); // during the request
  // Clear queue result toast. Discriminated by kind:
  //   { kind: 'success', removed, failed }    → green "N removed"
  //   { kind: 'partial', removed, failed }    → amber "N removed, M failures"
  //   { kind: 'error',   message }            → red "Clear failed"
  const [clearResultToast, setClearResultToast] = useState(null);
  const [reprintToast, setReprintToast]         = useState(null); // {filename, newUuid} or null
  const [selectedUuid, setSelectedUuid]         = useState(null); // string or null
  const [selectedTop, setSelectedTop]           = useState(0);    // viewport px (at click)
  const [removalToast, setRemovalToast]         = useState(null); // string or null
  const panelRef = useRef(null);

  // ─── Esc cascade: close preview first, panel next ─────────
  // (P3.H) If a preview is open, Esc closes it without closing the
  // panel — ergonomic to close just the preview after inspection.
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key !== 'Escape') return;
      if (selectedUuid) {
        e.preventDefault();
        e.stopPropagation();
        setSelectedUuid(null);
      } else {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose, selectedUuid]);

  // ─── Focus management: on open, focus the 1st focusable ───────
  // On close, restore focus to the trigger (status bar "Queue" button).
  useEffect(() => {
    if (!open) {
      // Cleanup on close: restore focus to the trigger
      if (restoreFocusRef?.current) {
        try { restoreFocusRef.current.focus(); } catch {/* ignore */}
      }
      return;
    }
    // Focus the 1st focusable element of the panel (close button in practice).
    // Small delay to let the DOM paint.
    const timer = setTimeout(() => {
      const first = panelRef.current?.querySelector(
        'button:not([disabled]), [tabindex]:not([tabindex="-1"]):not([disabled]), a[href]'
      );
      first?.focus();
    }, 10);
    return () => clearTimeout(timer);
  }, [open, restoreFocusRef]);

  // ─── Focus trap: Tab cycles within the panel only ──────────
  const handleTabTrap = (e) => {
    if (e.key !== 'Tab' || !panelRef.current) return;
    const focusables = panelRef.current.querySelectorAll(
      'button:not([disabled]), [tabindex="0"]:not([disabled]), a[href]'
    );
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last  = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  // ─── Toggle preview on click on a job (P3.H) ────────────────────
  const handleJobSelect = (uuid, evt) => {
    if (selectedUuid === uuid) {
      // Re-click same job → close
      setSelectedUuid(null);
      return;
    }
    // New job selected → capture the position for the popover
    const top = evt?.currentTarget?.getBoundingClientRect?.()?.top ?? 0;
    setSelectedTop(top);
    setSelectedUuid(uuid);
  };

  // ─── Auto-close if the selected job disappears from the snapshot ──────
  // On polling refresh (3 s), if the job we were viewing is no longer
  // in the list (firmware-side removal), we close the preview and
  // display a brief toast to explain.
  useEffect(() => {
    if (!selectedUuid || !snapshot) return;
    const stillThere = (snapshot.jobs || []).some((j) => j.uuid === selectedUuid);
    if (!stillThere) {
      setSelectedUuid(null);
      // NB: do NOT name the handle `t` — that shadows the i18n `t` used on the
      // line above and threw a ReferenceError (TDZ) whenever a previewed job
      // left the queue.
      setRemovalToast(t('queue.removal_toast'));
      const timer = setTimeout(() => setRemovalToast(null), 2000);
      return () => clearTimeout(timer);
    }
  }, [snapshot, selectedUuid]);

  // Reset selectedUuid on panel close (state consistency)
  useEffect(() => {
    if (!open) setSelectedUuid(null);
  }, [open]);

  if (!open) return null;

  const handleReprintSuccess = (response, originalJob) => {
    if (response?.ok && response?.new_uuid) {
      setReprintToast({ filename: originalJob.name, newUuid: response.new_uuid });
      // Auto-dismiss after 4 s (spec §4.1 phase 3)
      setTimeout(() => setReprintToast(null), 4000);
    } else if (response?.ok && !response?.new_uuid) {
      // Reprint submitted but new job not yet visible
      setReprintToast({
        filename: originalJob.name,
        newUuid: null,
        warning: response.warning,
      });
      setTimeout(() => setReprintToast(null), 4000);
    } else {
      // Error — 4s toast too but with an appropriate label
      setReprintToast({
        filename: originalJob.name,
        error: response?.detail || response?.message || t('queue.reprint_toast_default_error'),
      });
      setTimeout(() => setReprintToast(null), 4000);
    }
  };

  const handleCancelReprint = async (newUuid) => {
    // Spec §4.1 phase 3: the toast's "Cancel" button = cancel on the
    // new job (the firmware doesn't allow undoing a reprint, but we
    // can cancel the new job as long as it hasn't started).
    if (!newUuid) return;
    await actions.cancel(newUuid);
    setReprintToast(null);
  };

  // ─── Handler "Clear the queue" (Patch 3 pivot) ────────────────────
  // Backend loops individual DELETE → returns {removed_count,
  // failed_count}. The toast differs depending on these counters (total
  // success, partial, or total failure).
  const handleClearConfirmed = async () => {
    setConfirmingClear(false);
    setClearBusy(true);
    try {
      const r = await actions.clear();
      if (!r?.ok) {
        // Backend error pre-loop (502/network/auth). No jobs
        // removed at all.
        setClearResultToast({
          kind: 'error',
          message: r?.detail || r?.message || t('queue.clear_toast_error_default'),
        });
      } else {
        const removed = r.removed_count ?? 0;
        const failed = r.failed_count ?? 0;
        if (failed === 0 && removed > 0) {
          setClearResultToast({ kind: 'success', removed, failed });
        } else if (failed > 0 && removed > 0) {
          setClearResultToast({ kind: 'partial', removed, failed });
        } else if (failed > 0 && removed === 0) {
          setClearResultToast({
            kind: 'error',
            message: t('queue.clear_toast_error_count', { count: failed }),
          });
        }
        // removed=0 + failed=0 = queue already empty, no toast needed
      }
      setTimeout(() => setClearResultToast(null), 4000);
    } finally {
      setClearBusy(false);
    }
  };

  const queueStatus = snapshot?.queue_status || 'Unknown';
  const total = snapshot?.number_of_jobs ?? 0;
  const activeCount = _countActive(snapshot);
  // Patch 3: removable jobs (non-Deleted) — drives the disabled state of
  // the "Clear the queue" button to avoid a useless firmware call.
  const removableCount = (snapshot?.jobs || []).filter(
    (j) => j?.status !== 'Deleted',
  ).length;
  const queueUuid = snapshot?.queue_uuid || _extractQueueUuid(snapshot);
  const queuePaused = queueStatus === 'Paused';

  return (
    <>
      {/* Semi-transparent backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/30"
        onClick={onClose}
        aria-hidden="true"/>

      {/* Slide-over panel */}
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="jq-title"
        onKeyDown={handleTabTrap}
        className="fixed top-0 right-0 bottom-0 z-50 w-[460px] bg-surface border-l border-border-soft shadow-2xl flex flex-col">
        <Header
          titleId="jq-title"
          total={total}
          activeCount={activeCount}
          removableCount={removableCount}
          queuePaused={queuePaused}
          queueUuid={queueUuid}
          rediscovering={rediscovering}
          clearBusy={clearBusy}
          onPauseToggle={queuePaused ? actions?.resume : actions?.pause}
          onClearRequest={() => setConfirmingClear(true)}
          onClose={onClose}/>

        {/* Scrollable list */}
        <div className="flex-1 overflow-y-auto no-scrollbar">
          {total === 0 ? (
            <EmptyState/>
          ) : (
            // Newest job on top, matching the HP EWS convention. The Z9
            // returns jobs oldest-first; we reverse a COPY for display
            // (never mutate the snapshot — selection/find stay by uuid).
            [...(snapshot?.jobs || [])].reverse().map((job) => (
              <JobItem
                key={job.uuid}
                job={job}
                actions={actions}
                onConfirmRemove={(j) => setConfirmingRemove(j)}
                onConfirmReprint={(j) => setConfirmingReprint(j)}
                onReprintSuccess={handleReprintSuccess}
                selected={selectedUuid === job.uuid}
                previewId={PREVIEW_POPOVER_ID}
                onSelect={handleJobSelect}/>
            ))
          )}
        </div>

        <Footer/>

        {/* Reprint toast at the bottom of the panel (in-panel, not global) */}
        {reprintToast && (
          <ReprintToast
            toast={reprintToast}
            onCancelReprint={() => handleCancelReprint(reprintToast.newUuid)}
            onDismiss={() => setReprintToast(null)}/>
        )}

        {/* Clear queue result toast (Patch 3 pivot) — kind drives
            the color and the content. */}
        {clearResultToast && (
          <ClearResultToast
            toast={clearResultToast}
            onDismiss={() => setClearResultToast(null)}/>
        )}
      </aside>

      {/* ConfirmModal for deleting a live job (ghosts are
          removed without confirmation, see spec §4 "Delete"). */}
      <ConfirmModal
        open={!!confirmingRemove}
        title={t('queue.modal_cancel_confirm_title')}
        message={t('queue.modal_remove_message', { name: confirmingRemove?.name || t('queue.modal_remove_unnamed') })}
        confirmLabel={t('queue.action_delete')}
        cancelLabel={t('common.cancel')}
        confirmKind="danger"
        onConfirm={() => {
          if (confirmingRemove) actions.remove(confirmingRemove.uuid);
          setConfirmingRemove(null);
        }}
        onCancel={() => setConfirmingRemove(null)}/>

      {/* Patch 3 — "Clear the queue" modal confirmation */}
      <ConfirmModal
        open={confirmingClear}
        title={t('queue.modal_clear_confirm_title')}
        message={t('queue.modal_clear_confirm_message', { count: removableCount })}
        confirmLabel={t('queue.modal_clear_confirm_button')}
        cancelLabel={t('common.cancel')}
        confirmKind="danger"
        onConfirm={handleClearConfirmed}
        onCancel={() => setConfirmingClear(false)}/>

      {/* "Reprint this job" modal confirmation — consumes paper + ink */}
      <ConfirmModal
        open={!!confirmingReprint}
        title={t('print.modal_reprint_confirm_title')}
        message={t('print.modal_reprint_confirm_message', {
          name: confirmingReprint?.name || t('queue.modal_remove_unnamed'),
        })}
        confirmLabel={t('print.modal_reprint_confirm_button')}
        cancelLabel={t('common.cancel')}
        confirmKind="primary"
        onConfirm={async () => {
          const job = confirmingReprint;
          setConfirmingReprint(null);
          if (!job) return;
          const r = await actions.reprint(job.uuid);
          handleReprintSuccess(r, job);
        }}
        onCancel={() => setConfirmingReprint(null)}/>

      {/* Preview popover (P3.H: click trigger, no longer hover) */}
      {(() => {
        const selectedJob = selectedUuid
          ? (snapshot?.jobs || []).find((j) => j.uuid === selectedUuid)
          : null;
        return selectedJob ? (
          <JobPreviewPopover
            id={PREVIEW_POPOVER_ID}
            job={selectedJob}
            position={{ top: selectedTop }}/>
        ) : null;
      })()}

      {/* "Job removed from the queue" toast — brief, 2 s, role="status" */}
      {removalToast && (
        <div
          role="status"
          aria-live="polite"
          className="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-surface border border-border-soft text-text-strong px-4 py-2 rounded-md shadow-lg text-xs2">
          {removalToast}
        </div>
      )}
    </>
  );
}

// ─── Reprint toast ────────────────────────────────────────────────────

function ReprintToast({ toast, onCancelReprint, onDismiss }) {
  const { t } = useTranslation();
  if (toast.error) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="absolute bottom-12 left-4 right-4 z-10 bg-surface border border-danger/40 rounded-md shadow-lg px-3 py-2.5 flex items-start gap-2.5">
        <AlertTriangle size={14} className="text-danger mt-0.5 flex-shrink-0" aria-hidden="true"/>
        <div className="flex-1 text-xs2 leading-snug">
          <div className="font-medium text-text-strong">{t('queue.reprint_toast_failure_title')}</div>
          <div className="text-text-muted mt-0.5">{toast.error}</div>
        </div>
        <button type="button" onClick={onDismiss} aria-label={t('common.close')}
                className="text-text-muted hover:text-text-strong">
          <X size={13} aria-hidden="true"/>
        </button>
      </div>
    );
  }
  return (
    <div
      role="status"
      aria-live="polite"
      className="absolute bottom-12 left-4 right-4 z-10 bg-surface border border-border-soft rounded-md shadow-lg px-3 py-2.5 flex items-center gap-2.5">
      <CheckCircle2 size={14} className="text-success flex-shrink-0" aria-hidden="true"/>
      <div className="flex-1 text-xs2 leading-snug">
        <span className="font-medium text-text-strong">{t('queue.reprint_toast_success_title')}</span>
        {toast.filename && (
          <span className="text-text-muted"> · {_truncate(toast.filename, 36)}</span>
        )}
        {toast.warning && (
          <div className="text-text-faint text-tiny mt-0.5">{toast.warning}</div>
        )}
      </div>
      {toast.newUuid && (
        <button
          type="button"
          onClick={onCancelReprint}
          className="text-xs2 font-medium text-text-muted hover:text-accent transition-colors">
          {t('common.cancel')}
        </button>
      )}
      <button type="button" onClick={onDismiss} aria-label={t('common.close')}
              className="text-text-muted hover:text-text-strong">
        <X size={13} aria-hidden="true"/>
      </button>
    </div>
  );
}

function _truncate(s, n) {
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

// ─── Clear result toast (Patch 3 pivot) ───────────────────────────────

function ClearResultToast({ toast, onDismiss }) {
  const { t } = useTranslation();
  if (toast.kind === 'success') {
    return (
      <div
        role="status"
        aria-live="polite"
        className="absolute bottom-12 left-4 right-4 z-10 bg-success text-white rounded-md shadow-lg px-3 py-2.5 flex items-center gap-2.5">
        <CheckCircle2 size={14} className="flex-shrink-0" aria-hidden="true"/>
        <span className="flex-1 text-xs2 font-medium leading-snug">
          {t('queue.clear_toast_success_label', { count: toast.removed })}
        </span>
        <button type="button" onClick={onDismiss} aria-label={t('common.close')}
                className="text-white/80 hover:text-white">
          <X size={13} aria-hidden="true"/>
        </button>
      </div>
    );
  }
  if (toast.kind === 'partial') {
    return (
      <div
        role="status"
        aria-live="polite"
        className="absolute bottom-12 left-4 right-4 z-10 bg-surface border border-icc-warn/40 rounded-md shadow-lg px-3 py-2.5 flex items-start gap-2.5">
        <AlertTriangle size={14} className="text-icc-warn mt-0.5 flex-shrink-0" aria-hidden="true"/>
        <div className="flex-1 text-xs2 leading-snug">
          <div className="font-medium text-text-strong">
            {t('queue.clear_toast_partial_title', { count: toast.removed, removed: toast.removed, failed: toast.failed })}
          </div>
          <div className="text-text-muted mt-0.5">
            {t('queue.clear_toast_partial_body')}
          </div>
        </div>
        <button type="button" onClick={onDismiss} aria-label={t('common.close')}
                className="text-text-muted hover:text-text-strong">
          <X size={13} aria-hidden="true"/>
        </button>
      </div>
    );
  }
  // kind === 'error'
  return (
    <div
      role="status"
      aria-live="polite"
      className="absolute bottom-12 left-4 right-4 z-10 bg-surface border border-danger/40 rounded-md shadow-lg px-3 py-2.5 flex items-start gap-2.5">
      <AlertTriangle size={14} className="text-danger mt-0.5 flex-shrink-0" aria-hidden="true"/>
      <div className="flex-1 text-xs2 leading-snug">
        <div className="font-medium text-text-strong">{t('queue.clear_toast_error_default')}</div>
        <div className="text-text-muted mt-0.5">{toast.message}</div>
      </div>
      <button type="button" onClick={onDismiss} aria-label={t('common.close')}
              className="text-text-muted hover:text-text-strong">
        <X size={13} aria-hidden="true"/>
      </button>
    </div>
  );
}

// ─── Header ───────────────────────────────────────────────────────────

function Header({
  titleId, total, activeCount, removableCount, queuePaused, queueUuid,
  rediscovering, clearBusy, onPauseToggle, onClearRequest, onClose,
}) {
  const { t } = useTranslation();
  const clearDisabled = clearBusy || rediscovering || removableCount === 0;
  return (
    <div className="px-4 pt-3 pb-3 border-b border-border-soft">
      {/* Title + global actions + close button */}
      <div className="flex items-center gap-2 mb-2">
        <Layers size={15} strokeWidth={2.5} className="text-text-strong" aria-hidden="true"/>
        <h2 id={titleId} className="text-[15px] font-semibold text-text-strong tracking-tight">
          {t('queue.title')}
        </h2>
        <span className="text-xs2 text-text-muted ml-1.5 tabular-nums">
          {t('queue.header_total', { count: total })} · {t('queue.header_active', { count: activeCount })}
        </span>
        <div className="flex-1"/>
        {/* Patch 3 — Clear the queue. Discreet danger button, separated
            from the close button by a divider. Disabled if nothing to remove. */}
        <button
          type="button"
          onClick={onClearRequest}
          disabled={clearDisabled}
          aria-label={
            removableCount === 0
              ? t('queue.header_clear_disabled_aria')
              : t('queue.header_clear_enabled_aria', { count: removableCount })
          }
          title={t('queue.panel_clear_tooltip')}
          className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs2 font-medium border transition-colors ${
            clearDisabled
              ? 'border-border-soft text-text-faint cursor-not-allowed opacity-60'
              : 'border-danger/30 text-danger hover:bg-danger/10'
          }`}>
          {clearBusy ? (
            <Loader2 size={11} strokeWidth={2.5} className="animate-spin" aria-hidden="true"/>
          ) : (
            <Trash2 size={11} strokeWidth={2.5} aria-hidden="true"/>
          )}
          {t('queue.header_clear_label')}
        </button>
        <div className="w-px h-4 bg-border-soft mx-0.5"/>
        <button
          type="button"
          onClick={onClose}
          aria-label={t('queue.panel_close_aria')}
          className="p-1 rounded hover:bg-sunken text-text-muted hover:text-text-strong transition-colors">
          <X size={15} strokeWidth={2} aria-hidden="true"/>
        </button>
      </div>

      {/* Pause/resume line + state pill + mono UUID */}
      <div className="flex items-center gap-2">
        <PauseResumeButton
          paused={queuePaused}
          disabled={rediscovering}
          onClick={onPauseToggle}/>
        <span className="flex items-center gap-1.5 text-xs2 text-text-muted">
          <span className={`w-1.5 h-1.5 rounded-full ${queuePaused ? 'bg-icc-warn' : 'bg-success'}`}/>
          {queuePaused ? t('queue.queue_state_paused') : t('queue.queue_state_active')}
        </span>
        <div className="flex-1"/>
        {queueUuid && (
          <span
            className="font-mono text-tiny text-text-faint"
            title={t('queue.header_queue_uuid_tooltip', { uuid: queueUuid })}>
            q-{queueUuid.slice(0, 4)}…{queueUuid.slice(-2)}
          </span>
        )}
      </div>

      {/* Re-discover banner */}
      {rediscovering && (
        <div
          role="alert"
          className="mt-2.5 flex items-start gap-2 px-2.5 py-2 bg-icc-warn/10 border border-icc-warn/30 rounded-md text-xs2">
          <AlertTriangle size={13} className="text-icc-warn mt-0.5 flex-shrink-0" aria-hidden="true"/>
          <span className="text-text-strong leading-snug">
            <span className="font-semibold">{t('queue.header_rediscovering_title')}</span>{' '}
            <span className="text-text-muted">{t('queue.header_rediscovering_body')}</span>
          </span>
        </div>
      )}
    </div>
  );
}

function PauseResumeButton({ paused, disabled, onClick }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const handleClick = async () => {
    if (busy || disabled || !onClick) return;
    setBusy(true);
    try { await onClick(); }
    finally {
      // Debounce 1.2 s to avoid frantic re-clicking (see spec §4).
      setTimeout(() => setBusy(false), 1200);
    }
  };
  const Icon = paused ? Play : Pause;
  const label = paused ? t('queue.pause_button_resume') : t('queue.pause_button_pause');
  const styleCls = paused
    ? 'bg-accent hover:bg-accent-press text-white'
    : 'bg-transparent border border-border-soft hover:bg-sunken text-text-strong';
  const opacityCls = (busy || disabled) ? 'opacity-50 cursor-not-allowed' : '';
  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={busy || disabled}
      aria-label={label}
      className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs2 font-medium transition-colors ${styleCls} ${opacityCls}`}>
      <Icon size={11} strokeWidth={2.5} aria-hidden="true"/>
      {label}
    </button>
  );
}

// ─── Empty / Footer ───────────────────────────────────────────────────

function EmptyState() {
  const { t } = useTranslation();
  return (
    <div className="h-full flex flex-col items-center justify-center px-6 text-center">
      <div className="w-14 h-14 rounded-full bg-sunken flex items-center justify-center mb-3">
        <Layers size={28} strokeWidth={1.5} className="text-text-faint" aria-hidden="true"/>
      </div>
      <div className="text-sm font-medium text-text-strong mb-1.5">{t('queue.empty_state_title')}</div>
      <p className="text-xs2 text-text-muted leading-snug max-w-[280px]">
        {t('queue.empty_state_body')}
      </p>
    </div>
  );
}

function Footer() {
  const { t } = useTranslation();
  return (
    <div className="px-4 py-2 border-t border-border-soft flex items-center gap-3 text-tiny text-text-faint">
      <span><kbd className="font-mono text-text-muted">⌘J</kbd> {t('queue.footer_kbd_toggle')}</span>
      <span><kbd className="font-mono text-text-muted">Esc</kbd> {t('queue.footer_kbd_close')}</span>
      <div className="flex-1"/>
      <span className="text-text-faint">{t('queue.footer_autorefresh')}</span>
    </div>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────

function _countActive(snapshot) {
  const jobs = snapshot?.jobs || [];
  return jobs.filter((j) => {
    const s = j?.status || '';
    return s !== 'Completed' && s !== 'Cancelled' && s !== 'Deleted';
  }).length;
}

function _extractQueueUuid(snapshot) {
  // The backend snapshot doesn't expose queue_uuid directly, but we
  // can reconstruct it from a job's preview_uri (path
  // /JQ/JobQueue/<queue_uuid>/Job/...). Best-effort.
  const j = (snapshot?.jobs || []).find((x) => x?.preview_uri);
  if (!j) return null;
  const m = j.preview_uri.match(/JobQueue\/([0-9a-fA-F-]{36})\//);
  return m ? m[1] : null;
}
