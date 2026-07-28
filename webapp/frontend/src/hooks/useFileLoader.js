import { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { postFile, getFileInfo, postPrintPreview } from '../api/client.js';

/**
 * Hydrates the state `file = { info, preview }` from a file_id ALREADY stored
 * server-side (the end of `load()` without the `postFile`). Shared between:
 *  - `load(blob)` : browser upload then hydrate (drag/file explorer);
 *  - `loadFromId(file_id)` : boot from `freeglaz open` (image already uploaded
 *    by the CLI via the same endpoint).
 *
 * `filename` : the backend stores under "source.<ext>" internally; we keep the
 * display name provided by the caller (blob.name on the drag side, ?name=… on the CLI side).
 */
async function _fetchFileState(file_id, filename) {
  const info = await getFileInfo(file_id);
  const preview = await postPrintPreview({ file_id });
  return {
    info: {
      ...info,
      filename: filename || info.filename,
      // icc_status: not in /api/files/info; we derive it from preview.
      icc_status: preview.icc_status || 'none',
    },
    preview,
  };
}

/**
 * Pipeline upload → fileInfo → preview. Maintains a single file state
 * { info, preview } that feeds the state machine.
 *
 * Also exposes ``updatePreview(backendParams, fileId)`` : called by App.jsx
 * (debounce) when the user modifies the print params (offset,
 * gloss, etc.) → refetch the geometry without re-uploading the file.
 *
 * Anti-race conditions on updatePreview
 * ─────────────────────────────────────
 * Multiple ``updatePreview`` calls can be in flight in parallel if
 * the user chains actions quickly (drag → input → Center).
 * Without protection, responses can arrive out of order and a
 * STALE response can overwrite the correct state (file.preview oscillates,
 * localPos in PaperPreview relies on wrong ix_mm, etc.).
 *
 * Double protection:
 *   1. AbortController: we cancel the previous fetch as soon as a new one
 *      starts (network doesn't serve a response we'll throw away).
 *   2. Request token: if the cancellation arrives too late and a stale
 *      response sneaks in anyway, we compare against the current token and
 *      ignore it.
 */
export function useFileLoader() {
  const { t } = useTranslation();
  const [file, setFile] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  // See docstring above for the anti-race strategy.
  const previewAbortRef = useRef(null);
  const previewIdRef    = useRef(0);

  async function load(blob) {
    setError(null);
    if (!/\.(tif|tiff)$/i.test(blob.name)) {
      setError(t('errors.file_unsupported_format'));
      return;
    }
    setLoading(true);
    try {
      const { file_id } = await postFile(blob);
      setFile(await _fetchFileState(file_id, blob.name));
    } catch (e) {
      // The strict upload gate rejects with { code, message }; map the code to a
      // localized message, falling back to the backend English text then a
      // generic label.
      const code = e.detail?.code;
      const msg = code
        ? t(`errors.reject_${code}`, e.detail?.message || t('errors.file_load'))
        : (e.message || t('errors.file_load'));
      setError(msg);
      setFile(null);
    } finally {
      setLoading(false);
    }
  }

  // Boot from a file_id ALREADY uploaded (case `freeglaz open <file>`: the CLI
  // did the POST /api/files, the front starts directly from hydration —
  // skips the postFile). ADDITIVE path: the Blob path (load) is intact.
  // useCallback: stable identity for the one-shot boot effect in App.jsx.
  const loadFromId = useCallback(async (file_id, filename) => {
    if (!file_id) return;
    setError(null);
    setLoading(true);
    try {
      setFile(await _fetchFileState(file_id, filename));
    } catch (e) {
      setError(e.message || t('errors.file_load'));
      setFile(null);
    } finally {
      setLoading(false);
    }
  }, []);

  // useCallback: stable identity so as not to break the post-send useEffect
  // in App.jsx (B16) that depends on `clear`. Without it, `clear` is recreated on
  // every render of useFileLoader → useEffect cleanup fires → setTimeout
  // 2.5s is cancelled before completing as soon as another state (e.g.
  // status SSE z9_activity at 3s) triggers a re-render. Bug B17 follow-up
  // (P3): the "Print sent ✓" toast stayed displayed indefinitely.
  const clear = useCallback(() => {
    setFile(null);
    setError(null);
  }, []);

  // Stable identity (useCallback): no spurious re-fire of the useEffect
  // that watches updatePreview in App.jsx. fileId is passed as an argument to
  // avoid depending on the `file` closure (which would change on every update).
  const updatePreview = useCallback(async (backendParams, fileId) => {
    if (!fileId) return;

    // 1. Cancel the previous request if in flight — network doesn't serve
    //    a response we'll throw away.
    previewAbortRef.current?.abort();

    // 2. Incremental token: safety net if the cancellation arrives too late
    //    (response already sent server-side). On return we check that
    //    our token is still the most recent.
    const myId = ++previewIdRef.current;
    const controller = new AbortController();
    previewAbortRef.current = controller;

    try {
      const preview = await postPrintPreview(
        { file_id: fileId, params: backendParams },
        { signal: controller.signal },
      );
      // Stale check: a later fetch started and invalidated this one.
      if (myId !== previewIdRef.current) return;
      setFile((prev) => {
        if (!prev || prev.info.id !== fileId) return prev;
        return {
          ...prev,
          info: { ...prev.info, icc_status: preview.icc_status || 'none' },
          preview,
        };
      });
    } catch (e) {
      if (e.name === 'AbortError') return;  // normal cancellation, not an error
      console.warn('updatePreview failed:', e);
    }
  }, []);

  return { file, error, loading, load, loadFromId, clear, updatePreview };
}
