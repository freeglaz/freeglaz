import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as api from '../api/client.js';

/**
 * Calibration (CLC) orchestration hook for a given paper.
 *
 * P4.C + P4 progress-bar-stale fix.
 *
 * Combines 3 sources:
 * - **initial fetch** GET ``/calibrate/current`` on mount/mediaid change
 *   to recover the state at the moment the detail panel is opened.
 * - **SSE** on ``/calibrate/events`` while the job runs, primary source
 *   of real-time progress.
 * - **polling fallback** GET ``/calibrate/current`` every 5s while
 *   the job is ``starting`` / ``running``. Belt and braces
 *   for the case where the SSE is silent due to HTTP/1.1
 *   saturation (limit of 6 connections per origin — a parallel ICC
 *   export can consume a slot and block the SSE via HOL-blocking). The
 *   "most advanced wins" merge: if the poll sees ``done`` but the
 *   SSE is still ``running``, the poll takes over.
 *
 * @returns {{
 *   job: object|null,
 *   start: () => Promise,
 *   busy: boolean,
 *   startError: string|null,
 * }}
 */
const POLL_FALLBACK_INTERVAL_MS = 5000;


export function useCalibration(mediaid) {
  const { t } = useTranslation();
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [startError, setStartError] = useState(null);
  const sseRef = useRef(null);
  const pollTimerRef = useRef(null);
  const aliveRef = useRef(true);

  const _unsubscribe = useCallback(() => {
    if (sseRef.current) {
      try { sseRef.current.close(); } catch { /* ignore */ }
      sseRef.current = null;
    }
  }, []);

  const _stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  // Applies a received snapshot (via initial fetch OR poll fallback) to
  // state, respecting the hook's mediaid. If the snapshot indicates a
  // finished/errored job, closes the SSE.
  const _applySnapshot = useCallback((snapshotJob) => {
    if (!aliveRef.current) return;
    if (!snapshotJob || snapshotJob.mediaid !== mediaid) return;
    setJob((curr) => {
      // If the SSE state is more recent (progress > snapshot.progress)
      // we keep the SSE; otherwise we take the snapshot. The idea: don't
      // regress in case of a race.
      if (curr && _isMoreAdvanced(curr, snapshotJob)) return curr;
      return snapshotJob;
    });
    if (snapshotJob.state === 'done' || snapshotJob.state === 'error') {
      _unsubscribe();
      _stopPolling();
    }
  }, [mediaid, _unsubscribe, _stopPolling]);

  // Polling fallback: check /current every 5s while the job is
  // active on this mediaid. Wakes up a silent SSE.
  const _pollOnce = useCallback(async () => {
    if (!aliveRef.current) return;
    try {
      const res = await api.getCurrentCalibration();
      _applySnapshot(res?.job || null);
    } catch { /* silent */ }
    if (aliveRef.current) {
      pollTimerRef.current = setTimeout(_pollOnce, POLL_FALLBACK_INTERVAL_MS);
    }
  }, [_applySnapshot]);

  const _startPolling = useCallback(() => {
    _stopPolling();
    pollTimerRef.current = setTimeout(_pollOnce, POLL_FALLBACK_INTERVAL_MS);
  }, [_pollOnce, _stopPolling]);

  const _subscribe = useCallback((mid) => {
    _unsubscribe();
    sseRef.current = api.subscribeCalibrationEvents(mid, ({ type, data }) => {
      if (!aliveRef.current) return;
      if (type === 'snapshot') {
        if (data?.job) setJob(data.job);
      } else if (type === 'calibration_started') {
        setJob((curr) => ({
          ...(curr || {}),
          id: data?.id,
          mediaid: data?.mediaid,
          state: 'starting',
          progress: -1,
          process: '',
          result: null,
          error: null,
        }));
      } else if (type === 'progress') {
        setJob((curr) => curr ? {
          ...curr,
          state: 'running',
          progress: data?.percent ?? curr.progress,
          process:  data?.process ?? curr.process,
          elapsed:  data?.elapsed ?? curr.elapsed,
        } : curr);
      } else if (type === 'calibration_finished') {
        setJob((curr) => curr ? {
          ...curr,
          state: data?.outcome === 'success' ? 'done' : 'error',
          result: data?.outcome === 'success' ? {
            calibration_date: data?.clc_date,
            calibration_valid: data?.calibration_valid,
            elapsed: data?.elapsed,
          } : null,
          error: data?.outcome === 'error' ? (data?.message || t('errors.calibration')) : null,
        } : curr);
        _unsubscribe();
        _stopPolling();
      }
      // ping: silently ignored (keepalive)
    });
    _startPolling();
  }, [_unsubscribe, _stopPolling, _startPolling]);

  // ── Initial fetch + SSE reconnect if calibration in progress on this paper ──
  useEffect(() => {
    aliveRef.current = true;
    setJob(null);
    setStartError(null);

    if (!mediaid) return;

    let cancelled = false;
    api.getCurrentCalibration()
      .then((res) => {
        if (cancelled || !aliveRef.current) return;
        const current = res?.job;
        if (current && current.mediaid === mediaid) {
          setJob(current);
          // If still active, subscribe to events + start polling
          if (current.state === 'starting' || current.state === 'running') {
            _subscribe(mediaid);
          }
        }
      })
      .catch(() => { /* silent */ });

    return () => {
      cancelled = true;
      aliveRef.current = false;
      _unsubscribe();
      _stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mediaid]);

  const start = useCallback(async () => {
    if (!mediaid) return;
    setBusy(true);
    setStartError(null);
    try {
      const res = await api.startCalibration(mediaid);
      if (!aliveRef.current) return;
      setJob(res.job);
      _subscribe(mediaid);
    } catch (e) {
      if (!aliveRef.current) return;
      setStartError(e?.message || t('errors.clc_start_failed'));
    } finally {
      if (aliveRef.current) setBusy(false);
    }
  }, [mediaid, _subscribe]);

  return { job, start, busy, startError };
}


/**
 * Compares 2 snapshots of the same job. Returns true if ``a`` is more
 * advanced than ``b`` (do NOT regress from ``a`` to ``b``).
 *
 * State ordering: starting < running < done == error.
 */
function _isMoreAdvanced(a, b) {
  const order = { starting: 0, running: 1, done: 2, error: 2 };
  const oa = order[a.state] ?? 0;
  const ob = order[b.state] ?? 0;
  if (oa !== ob) return oa > ob;
  // Same state: the highest progress wins
  return (a.progress ?? -1) > (b.progress ?? -1);
}
