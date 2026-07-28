import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * Minimal confirmation modal. No external library: a dark backdrop + a
 * centered card. Esc / backdrop click = cancel, Enter = confirm (confirm
 * button autofocused on open).
 *
 * @param {{
 *   open: boolean,
 *   title: string,
 *   message?: string,
 *   icon?: import('react').ReactNode,   // optional pictogram left of the title (colored by the caller)
 *   confirmLabel?: string,
 *   cancelLabel?: string,
 *   confirmKind?: 'primary' | 'danger',
 *   busy?: boolean,                      // disables the buttons + freezes Enter during the action
 *   onConfirm: () => void,
 *   onCancel: () => void,
 *   thirdLabel?: string,
 *   thirdKind?: 'primary' | 'danger',
 *   onThird?: () => void,
 * }} props
 */
export default function ConfirmModal({
  open,
  title,
  message,
  icon,
  confirmLabel,
  cancelLabel,
  confirmKind  = 'primary',
  busy = false,
  onConfirm,
  onCancel,
  thirdLabel,
  thirdKind = 'primary',
  onThird,
}) {
  const { t } = useTranslation();
  // Fall back to the shared common.* labels when the caller doesn't pass one.
  const resolvedConfirmLabel = confirmLabel ?? t('common.confirm');
  const resolvedCancelLabel  = cancelLabel  ?? t('common.cancel');
  const confirmRef = useRef(null);

  // Esc → cancel. Enter → confirm. The second handler is defensive:
  // the confirm button is autofocused so Enter triggers it natively,
  // but if focus drifts (Tab to Cancel for example) we keep Enter as a
  // global shortcut for the modal.
  useEffect(() => {
    if (!open) return;
    const h = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onCancel?.();
      } else if (e.key === 'Enter') {
        if (busy) return;                         // action in progress → do not re-confirm
        // Only intercept Enter if focus is inside the modal (otherwise
        // we would disrupt an input elsewhere on the page — impossible
        // here because of the modal backdrop, but we stay defensive).
        const active = document.activeElement;
        if (active?.tagName === 'TEXTAREA') return; // multi-line input
        e.preventDefault();
        onConfirm?.();
      }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, onCancel, onConfirm, busy]);

  // Autofocus the Confirm button on open (a11y + native Enter).
  useEffect(() => {
    if (open) {
      // Small delay to let the DOM paint before focusing.
      const id = setTimeout(() => confirmRef.current?.focus(), 0);
      return () => clearTimeout(id);
    }
  }, [open]);

  if (!open) return null;

  const confirmClasses =
    confirmKind === 'danger'
      ? 'bg-danger hover:bg-danger/90 text-white'
      : 'bg-accent hover:bg-accent-press text-on-accent';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onCancel}
      role="dialog"
      aria-modal="true">
      <div
        className="bg-surface border border-border-soft rounded-xl shadow-xl p-5 w-[460px] max-w-[90vw]"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-2.5">
          {icon && <span className="mt-0.5 flex-shrink-0">{icon}</span>}
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold text-text-strong tracking-tight">{title}</h2>
            {message && (
              <p className="text-sm text-text-muted leading-relaxed mt-2 whitespace-pre-line">{message}</p>
            )}
          </div>
        </div>
        <div className="flex gap-2 justify-end mt-4">
          {resolvedCancelLabel && (
            <button
              type="button"
              onClick={onCancel}
              disabled={busy}
              className="px-3.5 py-2 rounded-md text-sm font-medium text-text-strong bg-sunken hover:bg-sunken-deep transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
              {resolvedCancelLabel}
            </button>
          )}
          {thirdLabel && onThird && (
            <button
              type="button"
              onClick={onThird}
              disabled={busy}
              className={`px-3.5 py-2 rounded-md text-sm font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50 disabled:cursor-not-allowed ${
                thirdKind === 'danger'
                  ? 'bg-danger hover:bg-danger/90 text-white'
                  : 'bg-accent hover:bg-accent-press text-on-accent'
              }`}>
              {thirdLabel}
            </button>
          )}
          <button
            ref={confirmRef}
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={`px-3.5 py-2 rounded-md text-sm font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50 disabled:cursor-not-allowed ${confirmClasses}`}>
            {resolvedConfirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
