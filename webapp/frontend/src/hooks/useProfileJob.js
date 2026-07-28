import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as api from '../api/client.js';

/**
 * ICC profiling SSE hook — mirror of useCalibration (P4).
 *
 * Combines initial fetch + SSE + polling fallback 5s.
 * The backend exposes the same 4 event types as calibration:
 *   snapshot, profile_started, progress, profile_finished, ping.
 *
 * The job snapshot exposes {state, progress, process, elapsed, result, error}.
 * process = "PRINTING" | "DRYING" | "SCANNING" | "COMPUTING" (uppercase firmware).
 */
const POLL_INTERVAL_MS = 5000;

export function useProfileJob() {
  const { t } = useTranslation();
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [startError, setStartError] = useState(null);
  const sseRef = useRef(null);
  const pollTimerRef = useRef(null);
  const aliveRef = useRef(true);
  const mediaidRef = useRef(null);

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

  const _applySnapshot = useCallback((snapshotJob) => {
    if (!aliveRef.current) return;
    if (!snapshotJob) return;
    setJob((curr) => {
      if (curr && _isMoreAdvanced(curr, snapshotJob)) return curr;
      return snapshotJob;
    });
    if (snapshotJob.state === 'done' || snapshotJob.state === 'error') {
      _unsubscribe();
      _stopPolling();
    }
  }, [_unsubscribe, _stopPolling]);

  const _pollOnce = useCallback(async () => {
    if (!aliveRef.current) return;
    try {
      const res = await api.getActiveProfile();
      _applySnapshot(res?.job || null);
    } catch { /* silent */ }
    if (aliveRef.current) {
      pollTimerRef.current = setTimeout(_pollOnce, POLL_INTERVAL_MS);
    }
  }, [_applySnapshot]);

  const _startPolling = useCallback(() => {
    _stopPolling();
    pollTimerRef.current = setTimeout(_pollOnce, POLL_INTERVAL_MS);
  }, [_pollOnce, _stopPolling]);

  const _subscribe = useCallback((mid) => {
    _unsubscribe();
    sseRef.current = api.subscribeProfileEvents(mid, ({ type, data }) => {
      if (!aliveRef.current) return;
      if (type === 'snapshot') {
        if (data?.job) setJob(data.job);
      } else if (type === 'profile_started') {
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
      } else if (type === 'profile_finished') {
        setJob((curr) => curr ? {
          ...curr,
          state: data?.outcome === 'success' ? 'done' : 'error',
          result: data?.outcome === 'success' ? {
            profile_icc_name: data?.profile_icc_name,
            profile_name: data?.profile_name,
            profile_date: data?.profile_date,
            gloss_enhancer: data?.gloss_enhancer,
            elapsed: data?.elapsed,
          } : null,
          error: data?.outcome === 'error' ? (data?.message || t('errors.profiling')) : null,
          process: data?.process || (curr ? curr.process : ''),
        } : curr);
        _unsubscribe();
        _stopPolling();
      }
    });
    _startPolling();
  }, [_unsubscribe, _stopPolling, _startPolling]);

  // Fetch initial — detect active job on mount
  useEffect(() => {
    aliveRef.current = true;
    let cancelled = false;
    api.getActiveProfile()
      .then((res) => {
        if (cancelled || !aliveRef.current) return;
        const current = res?.job;
        if (current) {
          setJob(current);
          mediaidRef.current = current.mediaid;
          if (current.state === 'starting' || current.state === 'running') {
            _subscribe(current.mediaid);
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
  }, []);

  const start = useCallback(async (mediaid, body) => {
    if (!mediaid) return;
    setBusy(true);
    setStartError(null);
    try {
      const res = await api.startProfile(mediaid, body);
      if (!aliveRef.current) return;
      setJob({
        id: res.job_id,
        mediaid,
        state: 'starting',
        progress: -1,
        process: '',
        elapsed: 0,
        result: null,
        error: null,
        ...body,
      });
      mediaidRef.current = mediaid;
      _subscribe(mediaid);
    } catch (e) {
      if (!aliveRef.current) return;
      setStartError(e?.message || t('errors.profiling_start_failed'));
    } finally {
      if (aliveRef.current) setBusy(false);
    }
  }, [_subscribe]);

  const reset = useCallback(() => {
    _unsubscribe();
    _stopPolling();
    setJob(null);
    setStartError(null);
    setBusy(false);
  }, [_unsubscribe, _stopPolling]);

  const isActive = job?.state === 'starting' || job?.state === 'running';

  return { job, start, busy, startError, isActive, reset };
}


function _isMoreAdvanced(a, b) {
  const order = { starting: 0, running: 1, done: 2, error: 2 };
  const oa = order[a.state] ?? 0;
  const ob = order[b.state] ?? 0;
  if (oa !== ob) return oa > ob;
  return (a.progress ?? -1) > (b.progress ?? -1);
}
