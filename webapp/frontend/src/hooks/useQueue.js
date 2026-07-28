import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as api from '../api/client.js';

/**
 * Central hook for the Z9 print queue (Phase 3).
 *
 * Strategy: 3s polling on GET /api/jobs. No SSE — the queue
 * changes rarely (a few times per minute at peak usage), a
 * regular poll is simpler, more robust and more code-efficient
 * than a dedicated backend SSE endpoint.
 *
 * The hook exposes:
 *  - `snapshot` : last snapshot received from the backend (jobs, queue_status,
 *    _meta with consecutive_failures, etc.). null until a poll
 *    has succeeded.
 *  - `actions` : { pause, resume, cancel(uuid), remove(uuid),
 *                  reprint(uuid) } — async wrappers that return the
 *    backend response; no debounce here (handled by the components).
 *  - `rediscovering` : boolean — true when the backend signals it's
 *    struggling to reach the queue (consecutive_failures > N).
 *  - `refresh` : trigger an immediate poll (useful after an action to
 *    see the effect without waiting for the next tick).
 *  - `error` : last network error message, null if OK.
 *
 * @returns {object} State + actions
 */
export function useQueue({ pollIntervalMs = 3000, rediscoverThreshold = 3 } = {}) {
  const { t } = useTranslation();
  const [snapshot, setSnapshot] = useState(null);
  const [error, setError] = useState(null);
  const aliveRef = useRef(true);
  const timerRef = useRef(null);

  const fetchOnce = useCallback(async () => {
    try {
      const snap = await api.getJobs();
      if (!aliveRef.current) return;
      setSnapshot(snap);
      setError(null);
    } catch (e) {
      if (!aliveRef.current) return;
      setError(e?.message || t('errors.network'));
    }
  }, []);

  // Polling + cleanup
  useEffect(() => {
    aliveRef.current = true;
    fetchOnce();
    timerRef.current = setInterval(fetchOnce, pollIntervalMs);
    return () => {
      aliveRef.current = false;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [fetchOnce, pollIntervalMs]);

  // Manual refresh — after an action, we want to see the result
  // immediately without waiting for the next tick.
  const refresh = useCallback(() => { fetchOnce(); }, [fetchOnce]);

  // ─── Actions ─────────────────────────────────────────────────
  // Wrappers that call the API and trigger a post-action refresh
  // to sync the UI quickly. No debounce here (the button
  // component is responsible for its own busy state).
  const wrap = useCallback((apiCall) => async (...args) => {
    const result = await apiCall(...args);
    refresh();
    return result;
  }, [refresh]);

  const actions = {
    pause:   wrap(api.pauseQueue),
    resume:  wrap(api.resumeQueue),
    cancel:  wrap(api.cancelJob),
    remove:  wrap(api.removeJob),
    reprint: wrap(api.reprintJob),
    clear:   wrap(api.clearQueue),
  };

  // Re-discover signal: the backend doesn't break on a stale queue
  // UUID (automatic re-discovery on the lib side on 404 Unkownw QueueID),
  // but we have an observation proxy via _meta.consecutive_failures. If
  // the subscriber has failed N times in a row, we show the
  // orange "Re-sync in progress" banner and disable the actions.
  const consecutiveFailures = snapshot?._meta?.consecutive_failures ?? 0;
  const rediscovering = consecutiveFailures >= rediscoverThreshold;

  // Patch 3 pivot 25/05/2026: we filter out ``Deleted`` jobs from the
  // list exposed to the UI (and from the total/active counter) to align
  // the freeglaz webapp behavior with that of the HP EWS, which hides
  // them by default. Observed bug: "1 residual Deleted job" that
  // stayed visible after a Clear queue — disappears mechanically.
  // The raw snapshot remains available via ``rawSnapshot`` in case
  // a future "View history" mode wants to re-display them.
  const visibleSnapshot = snapshot ? {
    ...snapshot,
    jobs: (snapshot.jobs || []).filter((j) => j?.status !== 'Deleted'),
    // Counters adjusted for consistency with the displayed list.
    // ``number_of_jobs`` from the backend reflects the firmware total (including
    // Deleted) — we overwrite it with the visible total.
    number_of_jobs: (snapshot.jobs || []).filter((j) => j?.status !== 'Deleted').length,
  } : null;

  return {
    snapshot: visibleSnapshot,
    rawSnapshot: snapshot,
    error, actions, refresh, rediscovering,
  };
}
