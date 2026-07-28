/**
 * Turn a caught error into a user-facing string, localized when the backend
 * tagged it with a machine code.
 *
 * Backend convention: a structured rejection is `HTTPException(status, detail=
 * {code, message})`. The api/client `http()` wrapper exposes it as `err.detail`
 * (object) with `err.message` as a string fallback. When a `code` is present we
 * translate `errors.<code>` (FR/EN catalogs), falling back to the backend
 * `message`, then to `err.message`. Plain-string details and code-less errors
 * pass through unchanged — so this never hides an untranslated error, it only
 * localizes the ones the backend opted in.
 *
 * @param {any} err - the caught error (expects optional `.detail` / `.message`)
 * @param {(key: string, opts?: object) => string} t - i18next translate
 * @returns {string}
 */
export function errorText(err, t) {
  const detail = err?.detail;
  if (detail && typeof detail === 'object' && detail.code) {
    return t(`errors.${detail.code}`, { defaultValue: detail.message || err?.message || '' });
  }
  if (typeof detail === 'string' && detail) return detail;
  return err?.message || String(err ?? '');
}
