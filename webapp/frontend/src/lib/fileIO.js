// Desktop-aware file I/O — single path for every export/import.
//
// #22: in the desktop window (pywebview / WebKitGTK / WKWebView), letting the
// webview navigate to a `blob:` or backend attachment URL replaces the page with
// the file content and FREEZES the app. So in desktop we route exports through the
// native SAVE dialog exposed by the Python side (`window.pywebview.api.save_file`,
// cf. webapp/desktop.py). In a browser, `window.pywebview` is absent → standard
// `<a download>`. NEVER navigate the webview to a file (the whole point of #22).

function _isDesktop() {
  return typeof window !== 'undefined' && !!window.pywebview?.api?.save_file;
}

// content: string | Uint8Array | ArrayBuffer → base64 (chunked to avoid arg limits).
function _toBase64(content) {
  let bytes;
  if (typeof content === 'string') bytes = new TextEncoder().encode(content);
  else if (content instanceof ArrayBuffer) bytes = new Uint8Array(content);
  else bytes = content;
  let bin = '';
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}

/**
 * Save `content` to a file the user picks.
 * Desktop → native SAVE dialog (Python writes the bytes, no navigation).
 * Browser → standard download via a `<a download>` blob link.
 * @returns {Promise<boolean>} true if saved, false if cancelled.
 */
export async function saveFile(name, content, mime = 'application/octet-stream') {
  if (_isDesktop()) {
    const path = await window.pywebview.api.save_file(name, _toBase64(content));
    return !!path;
  }
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return true;
}

/**
 * Save a BACKEND-served file (ICC, profile…) without ever navigating the webview to
 * its URL. Fetches the bytes, then routes through saveFile (desktop dialog / browser
 * download). Replaces the old `<a href={backendUrl}>` pattern (which froze desktop).
 * @returns {Promise<boolean>} true if saved.
 */
export async function saveFromUrl(url, name, mime) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`download failed: ${res.status}`);
  const buf = await res.arrayBuffer();
  // Use the EXACT filename the backend serves (Content-Disposition) — an export
  // must keep the file's real name (e.g. the ICC desc-based name), never a
  // reconstructed one. `name` is only a fallback if the header is absent.
  const served = _filenameFromContentDisposition(res.headers.get('content-disposition'));
  return saveFile(served || name, new Uint8Array(buf),
    mime || res.headers.get('content-type') || 'application/octet-stream');
}

// Extract the filename from a Content-Disposition header. RFC 5987 `filename*=`
// (encoded) wins over plain `filename="…"`.
function _filenameFromContentDisposition(cd) {
  if (!cd) return null;
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(cd);
  if (star) {
    try { return decodeURIComponent(star[1].trim().replace(/^["']|["']$/g, '')); }
    catch { /* fall through to the plain form */ }
  }
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  return plain ? plain[1].trim() : null;
}

/**
 * Open a file via the native desktop dialog (when available).
 * @returns {Promise<{name:string, bytes:Uint8Array, text:()=>string}|null>} null in
 * a browser (the caller keeps its `<input type=file>`, which WebKitGTK supports).
 */
export async function openFile() {
  if (!(_isDesktop() && window.pywebview.api.open_file)) return null;
  const r = await window.pywebview.api.open_file();
  if (!r) return null;
  const bin = atob(r.content_b64 || '');
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return { name: r.name, bytes, text: () => new TextDecoder().decode(bytes) };
}

export const isDesktop = _isDesktop;
