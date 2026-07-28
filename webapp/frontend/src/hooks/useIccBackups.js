import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../api/client.js';

/**
 * Hook that fetches the list of ICC backups for a slot (mediaid +
 * ge_state) and caches it for 30s in memory (avoids spam when hovering
 * the Rollback button).
 *
 * P2.C. ``ge_state`` ∈ ``{"off", "on", "single"}``.
 *
 * @returns {{
 *   count: number,
 *   latest: string|null,    // ISO timestamp of the most recent backup
 *   items: Array<{name, timestamp, size_bytes}>,
 *   loading: boolean,
 *   refresh: () => void,    // force an immediate re-fetch
 * }}
 */
export function useIccBackups(mediaid, geState) {
  const [data, setData] = useState({ count: 0, latest: null, items: [] });
  const [loading, setLoading] = useState(false);
  const lastFetchedAtRef = useRef(0);
  const aliveRef = useRef(true);

  const fetchNow = useCallback(async () => {
    if (!mediaid || !geState) return;
    setLoading(true);
    try {
      const res = await api.getPaperIccBackups(mediaid, geState);
      if (!aliveRef.current) return;
      setData(res);
      lastFetchedAtRef.current = Date.now();
    } catch {
      // best effort — no error displayed (UI just shows "no
      // backup available" silently if the endpoint fails)
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [mediaid, geState]);

  // Initial fetch + on every slot change, with a 30s cache
  useEffect(() => {
    aliveRef.current = true;
    const age = Date.now() - lastFetchedAtRef.current;
    if (age > 30_000) {
      fetchNow();
    }
    return () => { aliveRef.current = false; };
  }, [mediaid, geState, fetchNow]);

  return { ...data, loading, refresh: fetchNow };
}
