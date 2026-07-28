import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Paper detail panel state (P1.D + P4-2 revision).
 *
 * - ``selected`` : mediaid of the currently selected paper (or null)
 * - ``isOpen``   : panel visible
 * - ``open(paper)`` / ``close()`` simple API
 * - Esc closes the panel and restores focus to the last activated row
 *   (passed via the 2nd arg of open).
 * - Persistence: stays open when clicking another paper in the
 *   list, only the content changes.
 *
 * P4-2 revision: alignment with the JobQueuePanel — click outside
 * also closes (notes protection is handled by a fire-and-forget
 * flush in ``usePaperNotes`` on NotesZone unmount). The
 * Esc listener skips if a modal ``[aria-modal="true"]`` is already
 * open (ConfirmModal), so the modal handles its own Esc
 * before the panel.
 */
export function useDetailPanel() {
  const [selected, setSelected] = useState(null);  // mediaid string
  const [isOpen, setIsOpen]     = useState(false);
  // DOM element to refocus on close (the row that opened the panel)
  const restoreFocusRef = useRef(null);

  const open = useCallback((paper, triggerEl = null) => {
    setSelected(paper.mediaid);
    setIsOpen(true);
    if (triggerEl) restoreFocusRef.current = triggerEl;
  }, []);

  const close = useCallback(() => {
    setIsOpen(false);
    // Restore focus on the next tick (lets React unmount the panel)
    setTimeout(() => {
      const el = restoreFocusRef.current;
      if (el && typeof el.focus === 'function') {
        try { el.focus(); } catch { /* ignore */ }
      }
    }, 0);
  }, []);

  // Esc closes — global listener while open. Skips if a modal
  // ``aria-modal="true"`` is open (lets the modal handle its Esc).
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e) => {
      if (e.key !== 'Escape') return;
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      e.preventDefault();
      close();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, close]);

  return { selected, isOpen, open, close, restoreFocusRef };
}
