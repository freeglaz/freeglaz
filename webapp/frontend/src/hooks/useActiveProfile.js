import { useEffect, useRef, useState } from 'react';
import * as api from '../api/client.js';

/**
 * Polling hook for the global "Profiling in progress" badge (P5.B6).
 *
 * Poll GET /api/papers/profile/current every 5s. If a profiling job
 * is active (state in starting|running), exposes the snapshot. Otherwise null.
 *
 * Mirror of useCurrentCalibration (P4.C).
 */
const POLL_INTERVAL_MS = 5000;

export function useActiveProfile() {
  const [job, setJob] = useState(null);
  const timerRef = useRef(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    const tick = async () => {
      try {
        const res = await api.getActiveProfile();
        if (!aliveRef.current) return;
        const j = res?.job;
        if (j && (j.state === 'starting' || j.state === 'running')) {
          setJob(j);
        } else {
          setJob(null);
        }
      } catch {
        // silent: we'll retry on the next tick
      }
      if (aliveRef.current) {
        timerRef.current = setTimeout(tick, POLL_INTERVAL_MS);
      }
    };
    tick();
    return () => {
      aliveRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return job;
}
