import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as api from '../api/client.js';

/**
 * Central hook for the papers list (P1.C).
 *
 * Strategy: single initial fetch on mount, manual refresh via
 * ``refresh()``. No SSE in P1 (spec §4 of the brief — deferred to V2,
 * papers change rarely).
 *
 * Favorite toggle: optimistic update (UI updates before the backend
 * response), rollback on error. Toasts pattern.
 *
 * @returns {object}
 *  - ``papers`` : array of Paper, or null until the 1st fetch has
 *    succeeded
 *  - ``loading`` : true during the 1st initial fetch
 *  - ``error`` : string or null on fetch failure
 *  - ``refresh()`` : triggers a re-fetch (used after backend
 *    mutations that would change the list — for P1, mainly on resume
 *    after error)
 *  - ``toggleFavorite(mediaid)`` : optimistic, returns {ok, newState}
 *    (true=favorited). On error, the caller must roll back on the UI side.
 *  - ``stats`` : derived {total, custom, factory}
 */
export function usePapers() {
  const { t } = useTranslation();
  const [papers, setPapers] = useState(null);  // null = not loaded yet
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const aliveRef = useRef(true);

  const fetchOnce = useCallback(async () => {
    setError(null);
    try {
      const data = await api.getPapers();
      if (!aliveRef.current) return;
      setPapers(data.papers || []);
    } catch (e) {
      if (!aliveRef.current) return;
      setError(e?.message || t('errors.papers_load'));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    fetchOnce();
    return () => { aliveRef.current = false; };
  }, [fetchOnce]);

  const refresh = useCallback(() => {
    setLoading(true);
    fetchOnce();
  }, [fetchOnce]);

  /**
   * Favorite toggle with optimistic update.
   * Updates ``papers`` immediately (for the UI), then calls
   * the API. If the API fails, rolls back the local state and returns
   * ``{ok: false}`` to the caller so it can show a toast.
   */
  const toggleFavorite = useCallback(async (mediaid) => {
    let prevState = null;
    setPapers((curr) => {
      if (!curr) return curr;
      return curr.map((p) => {
        if (p.mediaid !== mediaid) return p;
        prevState = p.favorite;
        return { ...p, favorite: !p.favorite };
      });
    });
    try {
      const res = await api.togglePaperFavorite(mediaid);
      return { ok: true, favorite: res.favorite };
    } catch (e) {
      // Rollback
      setPapers((curr) => {
        if (!curr) return curr;
        return curr.map((p) =>
          p.mediaid === mediaid ? { ...p, favorite: prevState } : p,
        );
      });
      return { ok: false, message: e?.message || t('errors.network') };
    }
  }, []);

  // Derived values for the status bar and the sections
  const stats = papers
    ? {
        total: papers.length,
        custom: papers.filter((p) => p.custom).length,
        factory: papers.filter((p) => p.factory).length,
      }
    : { total: 0, custom: 0, factory: 0 };

  return { papers, loading, error, refresh, toggleFavorite, stats };
}
