/**
 * PROTECTED localStorage access (webview-safe).
 *
 * `localStorage` is NOT reliable in some contexts — notably the native
 * pywebview/WebKitGTK webview (Linux), where `localStorage.getItem/setItem` can THROW
 * (and does not always exist). A direct access DURING render (e.g. an initializer
 * `useState(() => localStorage.getItem(...))`) then propagates the exception and unmounts
 * the React tree → blank screen / crashed page. This trap has already bitten twice
 * (startup theme, then the Logs page).
 *
 * → All front-end localStorage access goes through these helpers. Safeguard: a direct
 * `localStorage.*` access outside of this file is forbidden (cf. ESLint/test).
 */

function _ls() {
  // `typeof` first: in some contexts `localStorage` isn't even defined
  // (referencing an absent global throws a ReferenceError).
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null;
  } catch {
    return null;
  }
}

/** Protected read. Returns `fallback` if unavailable or if the value is absent. */
export function safeLocalGet(key, fallback = null) {
  try {
    const ls = _ls();
    if (!ls) return fallback;
    const v = ls.getItem(key);
    return v ?? fallback;
  } catch {
    return fallback;
  }
}

/** Protected write (silent no-op if localStorage is unavailable/throws). */
export function safeLocalSet(key, value) {
  try {
    _ls()?.setItem(key, value);
  } catch {
    /* localStorage unavailable — no-op */
  }
}

/** Protected removal (silent no-op). */
export function safeLocalRemove(key) {
  try {
    _ls()?.removeItem(key);
  } catch {
    /* no-op */
  }
}
