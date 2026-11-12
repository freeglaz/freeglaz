import { useEffect, useState } from 'react';
import * as api from '../api/client.js';
import { previewKey, previewRequest } from '../lib/buildPreview.js';

// Preview of a build (POST /profile/preview, apply_all): recomputed (debounced) whenever the
// request or the chart state changes; results are kept WITH their key, so a stale preview is
// never shown as current. Never builds.
export function useBuildPreview(chartId, body, { enabled = true, stateStamp = '', nonce = 0 } = {}) {
  const key = enabled && chartId ? `${previewKey(chartId, body, stateStamp)}#${nonce}` : null;
  const [res, setRes] = useState({ key: null, data: null, error: null });
  useEffect(() => {
    if (!key) return undefined;
    let alive = true;
    const timer = setTimeout(() => {
      api.previewProfile(chartId, previewRequest(body))
        .then((d) => { if (alive) setRes({ key, data: d, error: null }); })
        .catch((e) => { if (alive) setRes({ key, data: null, error: { status: e?.status, message: e?.message } }); });
    }, 250);
    return () => { alive = false; clearTimeout(timer); };
  }, [key]);   // eslint-disable-line react-hooks/exhaustive-deps -- body/chartId are in the key
  const mine = res.key === key;
  const status = !key ? 'idle' : !mine ? 'loading' : res.error ? 'error' : 'ready';
  return { status, key, data: mine ? res.data : null, error: mine ? res.error : null };
}

/** THE build call of the interface: always policy apply_all with the previewed fingerprint
 *  (no silent fallback to legacy). Rejects with the backend error (409 inputs_changed…). */
export function buildWithPreview(chartId, body, preview, onPhase) {
  if (!preview?.inputs_fingerprint) return Promise.reject(Object.assign(new Error('preview required'), { status: 0 }));
  return api.buildProfileAndWait(chartId, { ...body, policy: 'apply_all', expected_fingerprint: preview.inputs_fingerprint }, onPhase);
}
