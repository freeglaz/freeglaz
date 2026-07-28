import { useEffect, useState } from 'react';
import { subscribePrintJob } from '../api/client.js';

/**
 * Subscribes to a job's SSE stream. `jobId` null → no-op (idle).
 * @param {string|null} jobId
 */
export function usePrintJob(jobId) {
  const [progress, setProgress] = useState(0);
  const [state,    setState]    = useState('idle');
  const [error,    setError]    = useState(null);
  useEffect(() => {
    // Reset on every jobId change (incl. a new job after a failed one).
    setProgress(0); setState('idle'); setError(null);
    if (!jobId) return;
    const sub = subscribePrintJob(jobId, (ev) => {
      setProgress(ev.progress ?? 0);
      setState(ev.state ?? 'unknown');
      // On failure, keep the backend reason (message, else the error code)
      // so the UI can show WHY the print failed instead of resetting silently.
      if (ev.state === 'failed') {
        setError(ev.message || ev.data?.code || null);
      }
    });
    return () => sub.close();
  }, [jobId]);
  return { progress, state, error };
}
