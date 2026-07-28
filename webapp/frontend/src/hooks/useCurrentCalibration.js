import { useEffect, useRef, useState } from 'react';
import * as api from '../api/client.js';

/**
 * Lightweight hook for the global "CLC in progress" badge in the status bar.
 *
 * Poll ``GET /api/papers/calibrate/current`` every 5s. If a
 * calibration is active (state in starting|running), exposes a job
 * snapshot. Otherwise ``null``. P4.C.
 *
 * No SSE here — the badge is deliberately passive, it's the
 * detail panel that opens the SSE for real-time progress.
 */
const POLL_INTERVAL_MS = 5000;

export function useCurrentCalibration() {
  const [job, setJob] = useState(null);
  const timerRef = useRef(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    const tick = async () => {
      try {
        const res = await api.getCurrentCalibration();
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
