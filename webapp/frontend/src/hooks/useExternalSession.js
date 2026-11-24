import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../api/client.js';
import { commandBody, createSnapshotReader, isStale, newer } from '../lib/externalSession.js';

/**
 * External measurement session of a chart (chartread-fgK), on the model of useProfileJob:
 * initial GET, SSE snapshots, polling fallback, re-attachment on mount (the session lives in
 * the backend, so a reload or a closed window finds it again).
 *
 * - Snapshots are merged by seq: an older one never replaces a newer one.
 * - Every command sends the {expect_state, seq} of the snapshot it was decided on. A
 *   stale_command refusal reloads the snapshot and is reported as `notice: 'stale'`, not as an
 *   error: nothing happened in the backend.
 * - Unmounting closes the subscription only: it never pauses or stops a measurement.
 * - Snapshot reads are applied in the order they were asked (createSnapshotReader). poll() is
 *   the wizard's presence watch: it joins a read in flight, and a failure is reported to the
 *   caller only (it is not a refused action).
 * - start() ignores a second call while the first is on its way (the backend would refuse it
 *   with run_active, after showing an error for nothing).
 */
const POLL_MS = 2000;

export function useExternalSession(chartId) {
  // The snapshot is kept with the chart it belongs to: switching charts shows nothing until the
  // new chart's snapshot arrives (no stale state carried over, no reset effect needed).
  const [held, setHeld] = useState({ chartId: null, snap: null });
  const snap = held.chartId === chartId ? held.snap : null;
  const [error, setError] = useState(null);       // last refused action (err with .detail)
  const [notice, setNotice] = useState(null);     // 'stale' | 'sse_lost' | null
  const [busy, setBusy] = useState(false);
  const snapRef = useRef(null);
  const aliveRef = useRef(true);
  const sseOkRef = useRef(false);
  const chartRef = useRef(chartId);
  const startingRef = useRef(false);
  const readerRef = useRef(null);                 // {chartId, reader}: made on first use, per chart

  const apply = useCallback((incoming) => {
    // A late answer about the previous chart is dropped (seq values are not comparable across charts).
    if (!aliveRef.current || !incoming || chartRef.current !== chartId) return;
    const next = newer(snapRef.current, incoming);
    snapRef.current = next;
    setHeld({ chartId, snap: next });
  }, [chartId]);

  const reader = useCallback(() => {
    if (readerRef.current?.chartId !== chartId) {
      readerRef.current = { chartId, reader: createSnapshotReader(() => api.getExternalMeasure(chartId), apply) };
    }
    return readerRef.current.reader;
  }, [chartId, apply]);

  const refresh = useCallback(async () => {
    if (!chartId) return null;
    try {
      return await reader().read();
    } catch (e) {
      if (aliveRef.current) setError(e);
      return null;
    }
  }, [chartId, reader]);

  const poll = useCallback(async () => {
    if (!chartId) return null;
    try {
      return await reader().shared();
    } catch {
      return null;
    }
  }, [chartId, reader]);

  useEffect(() => {
    aliveRef.current = true;
    chartRef.current = chartId;
    snapRef.current = null;
    sseOkRef.current = false;
    if (!chartId) return undefined;
    refresh();
    const sse = api.subscribeExternalMeasure(chartId, {
      onSnapshot: (s) => {
        sseOkRef.current = true;
        setNotice((n) => (n === 'sse_lost' ? null : n));
        apply(s);
      },
      onError: () => {
        sseOkRef.current = false;                 // the browser retries; polling meanwhile
        setNotice((n) => n || 'sse_lost');
      },
    });
    // Polling fallback: only while the event stream is down.
    const poll = setInterval(() => { if (!sseOkRef.current) refresh(); }, POLL_MS);
    return () => {
      aliveRef.current = false;
      try { sse.close(); } catch { /* ignore */ }
      clearInterval(poll);
    };
  }, [chartId, apply, refresh]);

  const run = useCallback(async (fn) => {
    setBusy(true);
    setError(null);
    try {
      const s = await fn();
      if (s) apply(s);
      setNotice((n) => (n === 'stale' ? null : n));
      return s;
    } catch (e) {
      if (isStale(e)) {
        setNotice('stale');
        await refresh();
      } else if (aliveRef.current) {
        setError(e);
        refresh();
      }
      return null;
    } finally {
      if (aliveRef.current) setBusy(false);
    }
  }, [apply, refresh]);

  const create = useCallback((filterOption = null) =>
    run(() => api.createExternalMeasure(chartId, filterOption)), [chartId, run]);
  const post = useCallback((action, extra = {}) =>
    run(() => api.postExternalMeasure(chartId, action, commandBody(snapRef.current, extra))), [chartId, run]);
  const start = useCallback(async () => {
    if (startingRef.current) return null;
    startingRef.current = true;
    try {
      return await post('start');
    } finally {
      startingRef.current = false;
    }
  }, [post]);
  const command = useCallback((name) => post('command', { command: name }), [post]);
  const correct = useCallback((passLabel) => post('correct', { pass_label: passLabel }), [post]);
  const finalize = useCallback(() => post('finalize'), [post]);
  const abandon = useCallback(() => post('abandon'), [post]);

  return { snap, error, notice, busy, refresh, poll, create, start, command, correct, finalize, abandon,
           clearError: () => setError(null) };
}
