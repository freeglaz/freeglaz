import { useState } from 'react';
import { RotateCcw, X, Trash2, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * A job row in the JobQueuePanel list (14 P1.D).
 *
 * Spec §3 (statuses + actions):
 *  - Active / Printing : left HP Blue band, pulsing pill, actions Cancel + Delete
 *  - Pending / Paused  : muted gray pill, actions Cancel + Delete
 *  - Completed         : success green pill, opacity 0.55, actions Reprint + Delete
 *  - Cancelled         : muted gray pill, opacity 0.55, actions Reprint + Delete
 *  - Deleted           : strikethrough name, opacity 0.55, Reprint disabled + Delete
 *
 * @param {object} p
 * @param {object} p.job — Job dict from /api/jobs
 * @param {object} p.actions — {cancel, remove, reprint} async funcs
 * @param {(job, evt) => void} p.onConfirmRemove — called to open a
 *   ConfirmModal on the parent side when we want to delete a live job
 *   (ghosts are removed without confirmation, it's just a local list
 *   removal from a UX standpoint)
 */
export default function JobItem({
  job, actions, onConfirmRemove, onConfirmReprint, onReprintSuccess,
  selected = false, previewId, onSelect,
}) {
  const { t } = useTranslation();
  const meta = _classify(job.status);
  const ghost = meta.ghost;
  const printing = meta.kind === 'active';

  // P3.H: UX pivot hover → click. Clicking on the row toggles the
  // preview. The action buttons (Reprint/Cancel/Delete) stop
  // propagation via their e.stopPropagation() in ActionButton,
  // so they don't trigger the preview.
  const handleSelect = (evt) => {
    onSelect?.(job.uuid, evt);
  };
  const handleKeyDown = (evt) => {
    // Space or Enter on the focused row → toggle preview (a11y)
    if (evt.key === ' ' || evt.key === 'Enter') {
      evt.preventDefault();
      handleSelect(evt);
    }
  };

  return (
    <article
      tabIndex={0}
      role="button"
      aria-expanded={selected}
      aria-controls={selected && previewId ? previewId : undefined}
      data-job-uuid={job.uuid}
      onClick={handleSelect}
      onKeyDown={handleKeyDown}
      className={`relative px-4 py-2.5 border-b border-border-soft/50 cursor-pointer transition-colors focus-visible:bg-sunken/50 ${selected ? 'bg-accent/5' : 'hover:bg-sunken/50'} ${ghost ? 'opacity-55' : ''}`}>
      {/* Left band: HP Blue for a printing job, or thinner for a
          selected job (preview open). If both, the active one
          dominates. */}
      {printing ? (
        <div className="absolute left-0 top-0 bottom-0 w-1 bg-accent" aria-hidden="true"/>
      ) : selected ? (
        <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-accent" aria-hidden="true"/>
      ) : null}

      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <div className={`text-xs2 font-medium text-text-strong truncate ${job.status === 'Deleted' ? 'line-through' : ''}`}
               title={job.name || ''}>
            {job.name || <span className="italic text-text-faint">{t('queue.item_unnamed_dash')}</span>}
          </div>
          <div className="text-tiny text-text-muted leading-snug mt-0.5 flex items-center gap-1.5 flex-wrap">
            <StatusPill kind={meta.kind} label={meta.labelKey ? t(meta.labelKey) : meta.fallback}/>
            {job.user && <span>· {job.user}</span>}
            {job.page_size_mm && (
              <span className="font-mono tabular-nums">
                · {Math.round(job.page_size_mm.width)} × {Math.round(job.page_size_mm.height)} mm
              </span>
            )}
            {job.submission_timestamp && (
              <span>· {_formatTime(job.submission_timestamp, t)}</span>
            )}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-0.5 -mr-1">
          {/*
            Reprint button — visibility decided by the BACKEND only
            (single source of truth, post-patch 25/05/2026). The
            ``job.can_reprint`` field reflects the strict server whitelist
            (Completed/Cancelled). For Deleted jobs, we keep a visible
            disabled button with an explanatory tooltip (discoverability
            pattern spec §4.1).
          */}
          {(job.can_reprint || job.status === 'Deleted') && (
            <ActionButton
              icon={RotateCcw}
              label={job.can_reprint
                ? t('queue.action_reprint')
                : t('queue.reprint_disabled_deleted')}
              disabled={!job.can_reprint}
              hoverColor="text-accent"
              onClick={() => {
                // Modal confirmation (consumes paper + ink).
                // Parent JobQueuePanel keeps the modal state and triggers
                // actions.reprint() after validation, same pattern as
                // delete (onConfirmRemove).
                onConfirmReprint?.(job);
              }}/>
          )}
          {meta.canCancel && (
            <ActionButton
              icon={X}
              label={t('queue.action_cancel')}
              onClick={() => actions.cancel(job.uuid)}/>
          )}
          <ActionButton
            icon={Trash2}
            label={t('queue.action_delete')}
            onClick={() => {
              if (ghost) {
                actions.remove(job.uuid);
              } else {
                onConfirmRemove?.(job);
              }
            }}/>
        </div>
      </div>
    </article>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────

function StatusPill({ kind, label }) {
  const styles = {
    active:    'bg-accent text-white animate-pulse',
    pending:   'bg-sunken-deep text-text-muted',
    completed: 'bg-success text-white',
    cancelled: 'bg-sunken-deep text-text-muted',
    deleted:   'bg-sunken text-text-faint',
  };
  // "kind" can be 'active' / 'pending' / 'ghost'; for ghost we map
  // onto the specific sub-state (completed/cancelled/deleted).
  const style = styles[kind] || styles.pending;
  return (
    <span
      role="status"
      className={`inline-flex items-center gap-1 px-1.5 py-px rounded-full text-tiny font-medium ${style}`}>
      <span className="w-1 h-1 rounded-full bg-current opacity-70"/>
      {label}
    </span>
  );
}

function ActionButton({ icon: Icon, label, onClick, disabled, hoverColor }) {
  const [busy, setBusy] = useState(false);
  const handleClick = async (e) => {
    e.stopPropagation();
    if (busy || disabled || !onClick) return;
    setBusy(true);
    try { await onClick(); }
    finally {
      // Debounce 1.2 s (freeglaz memory — minimum delay between queue operations).
      setTimeout(() => setBusy(false), 1200);
    }
  };
  // If disabled (reprint on Deleted), we keep the icon visible but grayed out
  // — discoverability pattern (see spec §4.1).
  const hoverCls = disabled
    ? ''
    : hoverColor
      ? `hover:bg-sunken hover:${hoverColor}`
      : 'hover:bg-sunken hover:text-text-strong';
  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={busy || disabled}
      aria-label={label}
      title={label}
      className={`p-1.5 rounded text-text-muted transition-colors ${hoverCls} ${disabled ? 'opacity-50 cursor-not-allowed' : ''} ${busy ? 'opacity-50' : ''}`}>
      {busy ? (
        <Loader2 size={12} strokeWidth={2} className="animate-spin" aria-hidden="true"/>
      ) : (
        <Icon size={12} strokeWidth={2} aria-hidden="true"/>
      )}
    </button>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────

/**
 * Mapping backend status → UI metadata (FR label, kind, actions).
 *
 * Observed backend statuses (see parser jobqueue.py + fixtures):
 *   Deleted, Paused, WaitingToPrint, Processing, Completed, Cancelled
 *
 * We group into 5 UI "kinds":
 *   active    → left HP Blue band, pulsing pill, Cancel + Delete
 *   pending   → no band, static pill, Cancel + Delete
 *   completed → ghost, Reprint + Delete (green)
 *   cancelled → ghost, Reprint + Delete (gray)
 *   deleted   → ghost strikethrough, Reprint disabled + Delete
 */
function _classify(status) {
  // `kind` drives the color of the StatusPill pill.
  // `ghost` drives the global 0.55 opacity of the row (see spec §3).
  // `canCancel` drives the visibility of the Cancel button.
  // `labelKey` is resolved via i18n at render (see consumer).
  switch (status) {
    case 'Processing':
      return { kind: 'active',    ghost: false, labelKey: 'queue.status_processing', canCancel: true  };
    case 'WaitingToPrint':
    case 'Paused':
      return { kind: 'pending',   ghost: false, labelKey: 'queue.status_pending',    canCancel: true  };
    case 'Completed':
      return { kind: 'completed', ghost: true,  labelKey: 'queue.status_completed',  canCancel: false };
    case 'Cancelled':
      return { kind: 'cancelled', ghost: true,  labelKey: 'queue.status_cancelled',  canCancel: false };
    case 'Deleted':
      return { kind: 'deleted',   ghost: true,  labelKey: 'queue.status_deleted',    canCancel: false };
    default:
      return { kind: 'pending',   ghost: false, labelKey: null, fallback: status || '—', canCancel: true };
  }
}

/**
 * Formats an ISO timestamp into a short relative label (localized via i18n).
 *  - < 60s         → "just now"
 *  - < 60min       → "N min ago"
 *  - < 24h         → "N h ago"
 *  - otherwise     → "N d ago"
 */
function _formatTime(iso, t) {
  if (!iso || iso.startsWith('1970')) return '';
  const ts = new Date(iso).getTime();
  if (isNaN(ts)) return '';
  const diffSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (diffSec < 60) return t('queue.time_now');
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return t('queue.time_minutes', { count: diffMin });
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return t('queue.time_hours', { count: diffH });
  const diffD = Math.floor(diffH / 24);
  return t('queue.time_days', { count: diffD });
}
