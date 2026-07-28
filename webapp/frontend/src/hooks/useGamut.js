import { useEffect, useState } from 'react';
import * as api from '../api/client.js';

// Process memory cache: ref_name → mesh. References are costly to load
// but identical across all profiles — shared at the process level.
const _refCache = new Map();


/** Hook: retrieves the effective gamut mesh of a profile for a given intent. */
export function useGamutMesh(source, intent = 'relative') {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!source) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    api.getProfileGamut({ ...source, intent })
      .then((m) => { if (!cancelled) setData(m); })
      .catch((e) => { if (!cancelled) setError(e.message || String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [source?.path, source?.paperMediaid, source?.slot, intent]);

  return { data, loading, error };
}


/** Hook: iso-surface of the gamt tag. */
export function useGamtMesh(source) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!source) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    api.getProfileGamt(source)
      .then((m) => { if (!cancelled) setData(m); })
      .catch((e) => { if (!cancelled) setError(e.message || String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [source?.path, source?.paperMediaid, source?.slot]);

  return { data, loading, error };
}


/** Hook: retrieves the mesh of a reference (process cache, implicit
 * "relative" intent — the intent of a pure reference has no
 * practical impact on its effective boundary). */
export function useReferenceMesh(name) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!name || name === 'none') {
      setData(null);
      return;
    }
    if (_refCache.has(name)) {
      setData(_refCache.get(name));
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.getReferenceGamut(name)
      .then((m) => {
        _refCache.set(name, m);
        if (!cancelled) setData(m);
      })
      .catch((e) => { if (!cancelled) setError(e.message || String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [name]);

  return { data, loading, error };
}


/** Hook: retrieves a CLUT scatter for the LUT popover. */
export function useLutScatter(source, tag) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!source || !tag) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    api.getLutScatter({ ...source, tag })
      .then((s) => { if (!cancelled) setData(s); })
      .catch((e) => { if (!cancelled) setError(e.message || String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [source?.path, source?.paperMediaid, source?.slot, tag]);

  return { data, loading, error };
}
