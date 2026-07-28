import { useCallback, useEffect, useState } from 'react';

/**
 * Local store reading hook (17.2).
 *
 * Loads the mirror + repo tree via /api/profiles/store. Does not depend
 * on the Z9 — the store is browsable offline (that's the whole point).
 *
 * Exposes:
 *   - store         : { mirrors: [...], repo: { printers, displays, workingspaces }, ... } | null
 *   - z9            : { serials: [...], repo_z9_dir } | null — per-paper personal Z9 space
 *   - loading       : bool
 *   - error         : string | null
 *   - reload()      : refetch (store + personal Z9 space, in parallel)
 *   - syncMirror()  : POST /api/profiles/sync (force optional), then reload
 *   - syncing       : bool
 *   - syncResult    : last sync summary (changed, n_profiles_fetched, ...) | null
 *   - syncError     : error message of the last sync | null
 */
export function useProfilesStore() {
  const [store, setStore] = useState(null);
  const [z9, setZ9] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  const [syncError, setSyncError] = useState(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [rStore, rZ9] = await Promise.all([
        fetch('/api/profiles/store'),
        fetch('/api/profiles/z9'),
      ]);
      if (!rStore.ok) {
        const detail = await rStore.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${rStore.status}`);
      }
      setStore(await rStore.json());
      setZ9(rZ9.ok ? await rZ9.json() : null);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const syncMirror = useCallback(async ({ force = false } = {}) => {
    setSyncing(true);
    setSyncError(null);
    try {
      const r = await fetch(`/api/profiles/sync?force=${force ? 'true' : 'false'}`, {
        method: 'POST',
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        throw new Error(data.detail || `HTTP ${r.status}`);
      }
      setSyncResult(data);
      await reload();
      return data;
    } catch (e) {
      setSyncError(e.message || String(e));
      throw e;
    } finally {
      setSyncing(false);
    }
  }, [reload]);

  return {
    store, z9, loading, error, reload,
    syncMirror, syncing, syncResult, syncError,
  };
}
