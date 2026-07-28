import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as api from '../api/client.js';

/**
 * Markdown notes management hook for a paper (P1.D — spec §5
 * Zone 5).
 *
 * - Loads the notes on mount (and on mediaid change)
 * - Auto-save debounce 1.2s after the last keystroke
 * - Save state indicator: 'idle' | 'pending' | 'saved' | 'error'
 *
 * No optimistic update — we wait for the backend response before
 * switching to 'saved' (the PUT is fast ~50 ms, no need to
 * complicate the UI).
 *
 * @param {string|null} mediaid — current paper. Null = no loading.
 * @returns {{notes, setNotes, status, error}}
 */
export function usePaperNotes(mediaid) {
  const { t } = useTranslation();
  const [notes, setNotesState] = useState('');
  const [status, setStatus]   = useState('idle');  // idle|pending|saved|error
  const [error, setError]     = useState(null);
  const lastSavedRef = useRef('');  // text actually saved to the backend
  const aliveRef = useRef(true);
  const debounceTimerRef = useRef(null);
  // Snapshot read by the cleanup useEffect to flush on unmount (Bug P4-2:
  // closing the panel before the 1.2s debounce expired → we lost
  // the in-progress characters). This ref is synced on every render.
  const flushStateRef = useRef({ mediaid, notes: '', lastSaved: '' });
  flushStateRef.current = {
    mediaid, notes,
    lastSaved: lastSavedRef.current,
  };

  // Loads the notes on mount / mediaid change
  useEffect(() => {
    aliveRef.current = true;
    if (!mediaid) {
      setNotesState('');
      lastSavedRef.current = '';
      setStatus('idle');
      return;
    }
    setStatus('idle');
    setError(null);
    api.getPaperNotes(mediaid)
      .then((res) => {
        if (!aliveRef.current) return;
        const v = res?.notes || '';
        setNotesState(v);
        lastSavedRef.current = v;
      })
      .catch((e) => {
        if (!aliveRef.current) return;
        setError(e?.message || t('errors.notes_load'));
      });
    return () => { aliveRef.current = false; };
  }, [mediaid]);

  // setNotes(value) — call this from the textarea. Triggers the
  // auto-save debounce.
  const setNotes = useCallback((value) => {
    setNotesState(value);
    if (!mediaid) return;
    // "Changes in progress" marker if different from the last saved
    if (value !== lastSavedRef.current) {
      setStatus('pending');
    }
    // Reset debounce timer
    if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
    debounceTimerRef.current = setTimeout(async () => {
      if (!aliveRef.current) return;
      // Avoid a useless PUT if the user typed then erased back to exactly the same
      if (value === lastSavedRef.current) {
        setStatus('saved');
        return;
      }
      try {
        await api.putPaperNotes(mediaid, value);
        if (!aliveRef.current) return;
        lastSavedRef.current = value;
        setStatus('saved');
        setError(null);
      } catch (e) {
        if (!aliveRef.current) return;
        setStatus('error');
        setError(e?.message || t('errors.notes_save'));
      }
    }, 1200);
  }, [mediaid]);

  // Cleanup debounce timer on unmount + fire-and-forget flush of pending
  // notes. Case: closing the detail panel before the 1.2s debounce ends.
  useEffect(() => () => {
    if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
    const s = flushStateRef.current;
    if (s.mediaid && s.notes !== s.lastSaved) {
      // Fire-and-forget: we don't wait for the response, the hook is unmounting
      api.putPaperNotes(s.mediaid, s.notes).catch(() => { /* silent */ });
    }
  }, []);

  return { notes, setNotes, status, error };
}
