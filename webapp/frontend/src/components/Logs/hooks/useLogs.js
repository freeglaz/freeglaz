import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../../../api/client.js';

const POLL_INTERVAL_MS = 5000;

function compactLogs(logs) {
  const out = [];
  let i = 0;
  while (i < logs.length) {
    const e = logs[i];
    if (e.severity === 'Debug') {
      let j = i;
      while (j < logs.length) {
        const ej = logs[j];
        if (ej.severity !== 'Debug' || ej.event_code !== e.event_code) break;
        const dt = Math.abs(new Date(e.timestamp).getTime() - new Date(ej.timestamp).getTime());
        if (dt > 30_000) break;
        j++;
      }
      if (j - i >= 4) {
        out.push({ type: 'cluster', events: logs.slice(i, j) });
        i = j;
        continue;
      }
    }
    out.push({ type: 'event', event: e });
    i++;
  }
  return out;
}

export function useLogs({ severity, hideRoutine = true, liveTail = false }) {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef(null);
  const aliveRef = useRef(true);

  const fetch_ = useCallback(async () => {
    try {
      const sevParam = severity?.length ? severity.join(',') : undefined;
      const data = await api.getRecentLogs({ severity: sevParam });
      if (!aliveRef.current) return;
      setLogs(data || []);
    } catch {
      // silent
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [severity]);

  useEffect(() => {
    aliveRef.current = true;
    setLoading(true);
    fetch_();
    return () => { aliveRef.current = false; };
  }, [fetch_]);

  useEffect(() => {
    if (!liveTail) { if (timerRef.current) clearInterval(timerRef.current); return; }
    timerRef.current = setInterval(fetch_, POLL_INTERVAL_MS);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [liveTail, fetch_]);

  const compacted = compactLogs(logs);
  const visible = hideRoutine
    ? compacted.filter((item) => item.type === 'event' ? item.event.severity !== 'Debug' : false)
    : compacted;

  const counts = {
    fatal: logs.filter((l) => l.severity === 'Fatal').length,
    error: logs.filter((l) => l.severity === 'Error').length,
    warning: logs.filter((l) => l.severity === 'Warning').length,
    info: logs.filter((l) => l.severity === 'Info').length,
    debug: logs.filter((l) => l.severity === 'Debug').length,
  };

  return { logs, visible, counts, loading, refresh: fetch_ };
}
