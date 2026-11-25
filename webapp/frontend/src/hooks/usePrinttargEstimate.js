import { useEffect, useState } from 'react';
import * as api from '../api/client.js';
import { errorText } from '../lib/errorText.js';

// Paper a printtarg chart WOULD take (POST /api/charts/printtarg-estimate), for a request body
// (null = nothing to estimate). Debounced; only the answer to the current body is shown.
// Used for the OTHER pass of a two-pass profiling (the wizard keeps its own estimate of the
// chart being composed).
export function usePrinttargEstimate(body, t) {
  const key = body ? JSON.stringify(body) : null;
  const [res, setRes] = useState({ key: null });
  useEffect(() => {
    if (!key) return undefined;
    let live = true;
    const timer = setTimeout(() => {
      api.estimatePrinttargChart(JSON.parse(key))
        .then((d) => { if (live) setRes({ key, state: 'ok', data: d }); })
        .catch((e) => { if (live) setRes({ key, state: 'error', code: e?.detail?.code || null, message: errorText(e, t) }); });
    }, 450);
    return () => { live = false; clearTimeout(timer); };
  }, [key, t]);
  if (!key) return null;
  return res.key === key ? res : { state: 'loading', data: null };
}
