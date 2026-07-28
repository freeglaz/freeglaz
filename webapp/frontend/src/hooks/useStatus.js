import { useEffect, useRef, useState, useCallback } from 'react';
import { subscribeStatusEvents } from '../api/client.js';

/**
 * Subscribes to the SSE stream /api/status/events (inc 10) with polling fallback.
 * Three possible event types, merged into a single ``status`` state:
 *   - status_full  → full replacement (snapshot on connect / reconnect)
 *   - status_diff  → shallow merge ({...s, ...data})
 *   - z9_state     → state-machine hint ("error", "printing", ...) shallow
 *                    merge into status to enable UI derivations
 *
 * ROBUSTNESS (webapp freeze 10/06): we monitor the LIFE of the connection via
 * the date of the last received event (including ``ping`` keepalives every 15s).
 * If no event since ``STALE_MS``, we assume the service is lost (backend
 * frozen/down) and expose ``stale=true`` → the App shows a
 * "service lost — retry" banner instead of a mute grey screen. ``reconnect()``
 * forces a new SSE subscription.
 *
 * @returns {{ status: import('../api/types').Status | null,
 *             stale: boolean, reconnect: () => void }}
 */
const STALE_MS = 45000; // 3 keepalive pings (15 s) missed → connection presumed lost

export function useStatus() {
  const [status, setStatus] = useState(null);
  const [stale, setStale] = useState(false);
  const [reconnectKey, setReconnectKey] = useState(0);
  const lastEventAt = useRef(Date.now());

  const reconnect = useCallback(() => {
    lastEventAt.current = Date.now();
    setStale(false);
    setReconnectKey((k) => k + 1); // re-triggers the effect → new subscription
  }, []);

  useEffect(() => {
    lastEventAt.current = Date.now();
    const sub = subscribeStatusEvents((eventType, data) => {
      lastEventAt.current = Date.now(); // any event (ping included) = connection alive
      setStale(false);
      if (eventType === 'status_full') {
        setStatus(data);
      } else if (eventType === 'status_diff') {
        setStatus((s) => ({ ...(s || {}), ...data }));
      } else if (eventType === 'z9_state') {
        setStatus((s) => ({ ...(s || {}), z9_state: data.state, ...data }));
      }
      // 'ping': doesn't touch the status, just serves as a liveness signal above.
    });
    return () => sub.close();
  }, [reconnectKey]);

  // Local heartbeat: marks the connection "stale" if nothing more arrives.
  useEffect(() => {
    const id = setInterval(() => {
      setStale(Date.now() - lastEventAt.current > STALE_MS);
    }, 5000);
    return () => clearInterval(id);
  }, []);

  return { status, stale, reconnect };
}
