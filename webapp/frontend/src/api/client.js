// fetch + SSE wrapper. Switches between mocks and the real backend via VITE_USE_MOCKS.
// In real mode: a mapping layer converts the backend format (Phase 1)
// to the format expected by the components (from the Claude Design bundle).
// Strategy: align the frontend to the backend, do not modify the backend.

import * as mocks from './mocks.js';
import i18n from '../i18n';

// Mocks are OPT-IN: active ONLY if VITE_USE_MOCKS==='true' (explicit dev).
// Default (prod, fresh clone without .env) = REAL backend → the mock cannot leak
// into a production build (the old `!== 'false'` enabled mocks by default, and
// the only file that turned them off — .env.local — is gitignored: fake CN-MOCK
// printer on a pristine machine).
const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === 'true';
const BASE      = import.meta.env.VITE_API_BASE || '';

async function http(path, opts = {}) {
  const r = await fetch(BASE + path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
  });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { /* non-JSON body */ }
    const err = new Error(typeof detail === 'string' ? detail : `HTTP ${r.status} on ${path}`);
    err.status = r.status;
    err.detail = detail;       // structured object (e.g. 409 name_conflict {suggestion, message})
    throw err;
  }
  return r.json();
}

// ─── Backend → frontend mappings ───────────────────────────────────────────
// The Z9+ backend returns 10 channels with long names; the frontend displays
// honest short codes (not the historical DesignJet inkset Y/M/C/K/LC/
// LM/Cl/GE which does not match the Z9+).

const INK_CODE_BY_COLOR = {
  yellow:           'Y',
  magenta:          'M',
  cyan:             'C',
  'photo-black':    'PK',
  'matte-black':    'MK',
  'chromatic-red':  'CR',
  'chromatic-blue': 'CB',
  'chromatic-green':'CG',
  gray:             'GY',
  'post-treatment': 'GE',
};

// 4 distinct values (cf. backend models.PrintPreview.icc_match):
//   match    → identical profiles
//   mismatch → different profiles (red, critical alert)
//   unknown  → comparison impossible on the firmware side (amber, alert)
//   none     → file without an ICC profile (gray, deterministic info)
// Identity mapping — we keep the backend vocabulary as-is on the frontend side.
const ICC_STATUS_MAP = {
  match:    'match',
  mismatch: 'mismatch',
  unknown:  'unknown',
  none:     'none',
};

function mapInks(list) {
  return (list || []).map((ink) => ({
    id:    INK_CODE_BY_COLOR[ink.color] || ink.color.slice(0, 2).toUpperCase(),
    level: (ink.level_pct || 0) / 100,
    state: ink.state,  // 'ok' | 'warning' | 'error'
  }));
}

function mapPaper(lp) {
  return {
    mediaid:   lp.id,           // firmware MEDIAID (used by Papers P4
                                // to gate the Calibrate button)
    kind:      lp.media_source === 'ROLL' ? 'roll' : 'sheet',
    name:      lp.name,
    width_mm:  lp.sheet_width_mm  ?? lp.roll_width_mm,
    height_mm: lp.sheet_height_mm,  // null in ROLL → stays null
    capabilities: [
      ...(lp.gloss_enhancer_supported ? ['gloss_enhancer'] : []),
      ...(lp.max_detail_supported     ? ['max_detail']     : []),
    ],
    // Carry the raw capability flag (NOT only the derived `capabilities` array):
    // the print-screen GE control reads it to disable itself on a paper the
    // firmware reports as not gloss-enhancer-capable (O1).
    gloss_enhancer_supported: lp.gloss_enhancer_supported,
    icc_profile: null,  // filled from preview.paper_icc_name later
  };
}

function mapStatus(raw) {
  // Status P4-fix: the Z9 standby means /api/status returns ready=false
  // + a Z9_UNREACHABLE alert (cf. routes/status.py _unreachable_status).
  // Without detecting it here, mapStatus produced z9_state='busy' → the
  // "Wake the Z9" button (visible iff z9_state==='error') stayed hidden,
  // even when the SSE had initially set 'error' (a following status_full or
  // diff neutralized it). We derive 'error' from the alert flag.
  const unreachable = Array.isArray(raw.alerts)
    && raw.alerts.some((a) => a && a.code === 'Z9_UNREACHABLE');
  return {
    paper:       raw.loaded_paper ? mapPaper(raw.loaded_paper) : null,
    inks:        mapInks(raw.inks),
    current_job: null,  // webapp job tracked locally (jobId useState in App)
    z9_state:    unreachable ? 'error' : (raw.ready ? 'ready' : 'busy'),
    // DEDICATED signal (distinct from reachability). SAFE default: only explicit `true` =
    // configured; absent/undefined = NOT configured → onboarding (the real backend always
    // sends the boolean, status.py; the mock sets it too). Wrongly believing we are
    // configured would hide onboarding (serious); wrongly showing it is benign.
    z9_configured: raw.z9_configured === true,
    z9_activity: raw.z9_activity || null,  // {name, progress_pct}
    model:       raw.model,
    serial:      raw.serial,
    firmware:    raw.firmware,
    state_text:  raw.state_text,
    alerts:      raw.alerts,
  };
}

function mapPreview(raw) {
  const g = raw.geometry;
  const fits = raw.can_print;
  return {
    fits,
    overflow_mm: fits ? undefined : {
      left:   g.overflow_left_mm   ?? 0,
      top:    g.overflow_top_mm    ?? 0,
      right:  g.overflow_right_mm  ?? 0,
      bottom: g.overflow_bottom_mm ?? 0,
    },
    margin_mm: fits ? { x: g.margin_left_mm, y: g.margin_top_mm } : undefined,
    icc_status:        ICC_STATUS_MAP[raw.icc_match] || 'none',
    icc_match_reason:  raw.icc_match_reason || null,
    geometry:          g,  // contains printable_*, image_x/y_mm, overflow_*
    file_icc_name:     raw.file_icc_name,
    file_icc_md5:      raw.file_icc_md5,
    paper_icc_name:    raw.paper_icc_name,
    paper_icc_md5:     raw.paper_icc_md5,
    // Precise backend messages (cf. B13.1: 3 distinct cases per axis —
    // image too large / position < margin / position > max). The
    // frontend must display these messages as-is, not a generic
    // hardcoded text (bug B13.2 fixed here).
    blocking_issues:   raw.blocking_issues || [],
    warnings:          raw.warnings || [],
  };
}

function mapFileInfo(raw) {
  return {
    id:          raw.file_id,
    filename:    raw.filename,  // will be overwritten by blob.name in useFileLoader
    width_mm:    raw.width_mm,
    height_mm:   raw.height_mm,
    dpi:         raw.dpi,
    icc_profile: raw.icc_name,
    icc_status:  'none',  // merged after preview in useFileLoader
  };
}

// ─── REST with mappings ────────────────────────────────────────────────────

export const getStatus = () =>
  USE_MOCKS ? mocks.getStatus()
            : http('/api/status').then(mapStatus);

// Backend health — also carries the freeglaz version (shown in Settings).
export const getHealth = () =>
  USE_MOCKS ? Promise.resolve({ ok: true, version: 'dev' })
            : http('/api/health');

export const postFile = async (file) => {
  if (USE_MOCKS) return mocks.postFile(file);
  const form = new FormData();
  form.append('file', file);
  const r = await fetch(BASE + '/api/files', { method: 'POST', body: form });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { /* non-JSON body */ }
    // Structured rejection from the strict upload gate: { code, message }.
    const msg = typeof detail === 'string'
      ? detail
      : (detail?.message || `upload failed: ${r.status}`);
    const err = new Error(msg);
    err.status = r.status;
    err.detail = detail;
    throw err;
  }
  return r.json();
};

export const getFileInfo = (id) =>
  USE_MOCKS ? mocks.getFileInfo(id)
            : http(`/api/files/${id}/info`).then(mapFileInfo);

// URL of the preview PNG generated by the backend. Stable per file_id so cached
// by the browser (200 OK GET). In mocks mode, we return a placeholder SVG
// dataURL so that <img> has something to display.
export function getFilePreviewUrl(fileId) {
  if (USE_MOCKS) return mocks.getFilePreviewUrl(fileId);
  return `${BASE}/api/files/${fileId}/preview`;
}

export const postPrintPreview = (body, options = {}) =>
  USE_MOCKS ? mocks.postPrintPreview(body)
            : http('/api/print/preview', {
                method: 'POST',
                body: JSON.stringify({ file_id: body.file_id, params: body.params || {} }),
                signal: options.signal,
              }).then(mapPreview);

export const postPrint = (body) =>
  USE_MOCKS ? mocks.postPrint(body)
            : http('/api/print', {
                method: 'POST',
                body: JSON.stringify({ file_id: body.file_id, params: body.params || {} }),
              });

// ─── Known printers (IP configuration) ──────────────────────────────────────
// Config (not a machine operation) → no Z9 auth. In mocks: inline stubs.

export const getPrinters = () =>
  USE_MOCKS ? Promise.resolve({ printers: [], active: null })
            : http('/api/printers');

/** Tests an entered IP → {status: ok|unreachable|not_a_designjet, model, serial, …}. */
export const testPrinter = (ip) =>
  USE_MOCKS ? Promise.resolve({ status: 'ok', model: 'HP DesignJet Z9 24in',
                                serial: 'CN-MOCK-0001', firmware: 'MOCK',
                                part: 'W3Z71A', model_support: 'validated' })
            : http('/api/printers/test', { method: 'POST', body: JSON.stringify({ ip }) });

export const addPrinter = (body) =>
  USE_MOCKS ? Promise.resolve({ ...body, active: true })
            : http('/api/printers', { method: 'POST', body: JSON.stringify(body) });

export const updatePrinter = (serial, body) =>
  USE_MOCKS ? Promise.resolve({ serial, ...body })
            : http(`/api/printers/${encodeURIComponent(serial)}`,
                   { method: 'PUT', body: JSON.stringify(body) });

export const activatePrinter = (serial) =>
  USE_MOCKS ? Promise.resolve({ active: { serial } })
            : http(`/api/printers/${encodeURIComponent(serial)}/activate`, { method: 'POST' });

export const deletePrinter = (serial) =>
  USE_MOCKS ? Promise.resolve({ removed: serial })
            : http(`/api/printers/${encodeURIComponent(serial)}`, { method: 'DELETE' });

// Offline demo mode (mock Z9) — hot swap, no printer needed.
export const enableDemo = () =>
  USE_MOCKS ? Promise.resolve({ demo: true })
            : http('/api/printers/demo', { method: 'POST' });

export const disableDemo = () =>
  USE_MOCKS ? Promise.resolve({ demo: false })
            : http('/api/printers/demo/exit', { method: 'POST' });

/**
 * POST /api/print/{jobId}/cancel.
 *
 * 3 backend branches (cf. 11.3) — all translated into a uniform
 * object on the frontend side to simplify the caller:
 *
 *   { ok: true, status: "cancelling" }                  → non-terminal worker
 *   { ok: true, status: "done", already_terminal: true } → worker + Z9 idle
 *   { ok: false, code: "post_send_no_remote_cancel",     → worker DONE, Z9 active
 *     message: "...", z9_activity: "Drying" }
 *   { ok: false, code: "network", message: "..." }      → network error
 *
 * The caller (App.handleCancel) shows the message in a toast
 * for the post_send_no_remote_cancel case (front panel workaround).
 */
export const postPrintCancel = async (jobId) => {
  if (USE_MOCKS) return mocks.postPrintCancel(jobId);
  try {
    const r = await fetch(`${BASE}/api/print/${jobId}/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const body = await r.json().catch(() => ({}));
    if (r.status === 202) return { ok: true, ...body };
    if (r.status === 409) {
      // FastAPI HTTPException(detail=dict) → {detail: {...}}
      const detail = body.detail || body;
      return { ok: false, ...detail };
    }
    if (r.status === 404) return { ok: false, code: 'unknown_job' };
    return { ok: false, code: `http_${r.status}`, message: i18n.t('errors.server') };
  } catch (e) {
    return { ok: false, code: 'network', message: e?.message || i18n.t('errors.network_unavailable') };
  }
};

// ─── Papers API ─────────────────────────────────────────────────────────────

export const getPapers = async () => {
  if (USE_MOCKS && typeof mocks.getPapers === 'function') return mocks.getPapers();
  return http('/api/papers');
};

export const togglePaperFavorite = async (mediaid) => {
  if (USE_MOCKS && typeof mocks.togglePaperFavorite === 'function')
    return mocks.togglePaperFavorite(mediaid);
  return http(`/api/papers/${mediaid}/favorite`, { method: 'POST' });
};

export const getPaperNotes = async (mediaid) => {
  if (USE_MOCKS && typeof mocks.getPaperNotes === 'function')
    return mocks.getPaperNotes(mediaid);
  return http(`/api/papers/${mediaid}/notes`);
};

export const putPaperNotes = async (mediaid, notes) => {
  if (USE_MOCKS && typeof mocks.putPaperNotes === 'function')
    return mocks.putPaperNotes(mediaid, notes);
  return http(`/api/papers/${mediaid}/notes`, {
    method: 'PUT',
    body: JSON.stringify({ notes }),
  });
};

// ─── Papers ICC actions ─────────────────────────────────────────────────────

/**
 * URL for the direct download of the ICC binary (for an <a href="..."> tag
 * or window.location). The backend returns ``application/vnd.iccprofile``
 * + Content-Disposition attachment, the browser performs the download.
 */
export const paperIccDownloadUrl = (mediaid, geState) =>
  `${BASE}/api/papers/${mediaid}/icc/${geState}`;

/**
 * Inspects an ICC profile. Either a local file by ``path``,
 * or a firmware profile by ``paper_mediaid`` + ``slot``.
 * @returns {Promise<object>} the ProfileInspection.to_dict() dict enriched
 *   with TRC layers + taxonomy.
 */
export const inspectProfile = async ({ path, paperMediaid, slot }) => {
  const params = new URLSearchParams();
  if (path) params.set('path', path);
  if (paperMediaid) params.set('paper_mediaid', paperMediaid);
  if (slot) params.set('slot', slot);
  const r = await fetch(`${BASE}/api/profiles/inspect?${params}`);
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // Structured rejection {code, message}: surface it via err.detail so the UI
    // can localize the code (errorText); plain-string details pass through.
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};

/**
 * profile-compare: structural comparison of 2+ ICC profiles.
 * @param {Object} args
 * @param {string[]} args.paths       — absolute paths (≥2, ≤8)
 * @param {(string|null)[]} [args.ti3Paths] — aligned with `paths`, null/"" items
 *        allowed to skip the profcheck of a profile
 * @param {boolean} [args.withCurvature] — adds curvature [EXPERIMENTAL]
 * @returns {Promise<Object>} dict (cf. profile_compare.compare_profiles)
 */
export const compareProfiles = async ({ paths, ti3Paths, withCurvature }) => {
  const body = {
    paths,
    with_curvature: !!withCurvature,
  };
  if (ti3Paths !== undefined) body.ti3_paths = ti3Paths;
  const r = await fetch(`${BASE}/api/profiles/compare`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // Structured rejection {code, message}: surface it via err.detail so the UI
    // can localize the code (errorText); plain-string details pass through.
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};

/**
 * Effective gamut of an ICC profile (device_surface_grid, variable intent).
 */
export const getProfileGamut = async ({ path, paperMediaid, slot, intent }) => {
  const p = new URLSearchParams();
  if (path) p.set('path', path);
  if (paperMediaid) p.set('paper_mediaid', paperMediaid);
  if (slot) p.set('slot', slot);
  if (intent) p.set('intent', intent);
  const r = await fetch(`${BASE}/api/profiles/gamut?${p}`);
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // Structured rejection {code, message}: surface it via err.detail so the UI
    // can localize the code (errorText); plain-string details pass through.
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};

/**
 * Iso-surface of the gamt tag (boundary declared by the profiler).
 * Returns 404 if the profile has no gamt tag.
 */
export const getProfileGamt = async ({ path, paperMediaid, slot }) => {
  const p = new URLSearchParams();
  if (path) p.set('path', path);
  if (paperMediaid) p.set('paper_mediaid', paperMediaid);
  if (slot) p.set('slot', slot);
  const r = await fetch(`${BASE}/api/profiles/gamt?${p}`);
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // Structured rejection {code, message}: surface it via err.detail so the UI
    // can localize the code (errorText); plain-string details pass through.
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};

export const getReferenceGamut = async (name, { intent } = {}) => {
  const p = new URLSearchParams();
  if (intent) p.set('intent', intent);
  const r = await fetch(`${BASE}/api/gamut/reference/${encodeURIComponent(name)}?${p}`);
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // Structured rejection {code, message}: surface it via err.detail so the UI
    // can localize the code (errorText); plain-string details pass through.
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};

export const getLutScatter = async ({ path, paperMediaid, slot, tag, intent, resolution }) => {
  const p = new URLSearchParams();
  if (path) p.set('path', path);
  if (paperMediaid) p.set('paper_mediaid', paperMediaid);
  if (slot) p.set('slot', slot);
  if (tag) p.set('tag', tag);
  if (intent) p.set('intent', intent);
  if (resolution) p.set('resolution', String(resolution));
  const r = await fetch(`${BASE}/api/profiles/lut_scatter?${p}`);
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // Structured rejection {code, message}: surface it via err.detail so the UI
    // can localize the code (errorText); plain-string details pass through.
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};


/**
 * Direct download URL of the .icc of a repo/z9 profile (<a href> tag).
 * The backend returns application/vnd.iccprofile + attachment.
 */
export const z9ProfileExportUrl = ({ serial, mediaId, filename }) => {
  const p = new URLSearchParams();
  p.set('serial', serial);
  p.set('media_id', mediaId);
  p.set('filename', filename);
  return `${BASE}/api/profiles/z9/export?${p}`;
};

/**
 * Tree of the personal Z9 space (serial → paper → refined profiles).
 * @returns {Promise<{serials: object[], repo_z9_dir: string}>}
 */
export const getZ9Profiles = async () => {
  const r = await fetch(`${BASE}/api/profiles/z9`);
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // Structured rejection {code, message}: surface it via err.detail so the UI
    // can localize the code (errorText); plain-string details pass through.
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};

/** Self-consistency check (mechanism a) of ANY profile in the store,
 * by absolute path: mirror (= synchronized installed profiles), repo z9, etc.
 * profcheck -k on the auto-sourced creation ti3 + white Lab test. Local, NO Z9. */
export const checkProfile = async ({ path }) => {
  const r = await fetch(`${BASE}/api/profiles/check`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // Structured rejection {code, message}: surface it via err.detail so the UI
    // can localize the code (errorText); plain-string details pass through.
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};

/**
 * Deletes a profile from the personal Z9 space (.icc + sidecar).
 */
export const deleteZ9Profile = async ({ serial, mediaId, filename }) => {
  const p = new URLSearchParams();
  p.set('serial', serial);
  p.set('media_id', mediaId);
  p.set('filename', filename);
  const r = await fetch(`${BASE}/api/profiles/z9?${p}`, { method: 'DELETE' });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // Structured rejection {code, message}: surface it via err.detail so the UI
    // can localize the code (errorText); plain-string details pass through.
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};

/**
 * GET/PUT app-level settings.
 */
export const getSettings = async () => {
  const r = await fetch(`${BASE}/api/settings`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
};

// Argyll settings — dedicated (persisted to ~/.freeglazrc.toml [argyll], NOT
// settings.json). Shape: { status: ArgyllStatus, root: string|null, env_override: bool }.
export const getArgyllSettings = async () => {
  const r = await fetch(`${BASE}/api/settings/argyll`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
};

export const updateArgyllRoot = async (root) => {
  const r = await fetch(`${BASE}/api/settings/argyll`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ root }),
  });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};

export const updateSettings = async (patch) => {
  const r = await fetch(`${BASE}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // Structured rejection {code, message}: surface it via err.detail so the UI
    // can localize the code (errorText); plain-string details pass through.
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};

/**
 * Uploads an ICC file to replace the profile of a slot. The backend
 * makes an automatic backup of the current profile before overwriting.
 * @returns {Promise<{outcome, icc_name, backup_created}>}
 */
export const replacePaperIcc = async (mediaid, geState, file) => {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch(`${BASE}/api/papers/${mediaid}/icc/${geState}`, {
    method: 'PUT',
    body: fd,
  });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // Structured rejection {code, message}: surface it via err.detail so the UI
    // can localize the code (errorText); plain-string details pass through.
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};

/**
 * Deletes the custom profile of a slot to re-expose the factory one.
 * Auto backup before deletion.
 */
export const restoreFactoryIcc = async (mediaid, geState) => {
  const r = await fetch(`${BASE}/api/papers/${mediaid}/icc/${geState}`, {
    method: 'DELETE',
  });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // Structured rejection {code, message}: surface it via err.detail so the UI
    // can localize the code (errorText); plain-string details pass through.
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || `HTTP ${r.status}`)
      : (detail || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.detail = detail;
    throw err;
  }
  return r.json();
};

/**
 * Lists the local backups for a slot.
 * @returns {Promise<{count, latest, items: [{name, timestamp, size_bytes}]}>}
 */
export const getPaperIccBackups = async (mediaid, geState) => {
  return http(`/api/papers/${mediaid}/icc/${geState}/backups`);
};

/**
 * Restores the most recent backup and consumes it.
 */
export const rollbackPaperIcc = async (mediaid, geState) => {
  return http(`/api/papers/${mediaid}/icc/${geState}/rollback`, { method: 'POST' });
};


// ─── Papers lifecycle ────────────────────────────────────────────────────────

/**
 * Creates a custom paper by cloning a donor.
 *
 * @param {{ name: string, donor_id: string, force?: boolean }} body
 * @returns {Promise<{paper, mediaid, donor_id, donor_name}>}
 * @throws Error with ``.code === 'name_conflict'`` and ``.existing_mediaid``
 *   on 409, otherwise a standard Error.
 */
export const createPaper = async (body) => {
  const r = await fetch(`${BASE}/api/papers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    if (r.status === 409 && typeof detail === 'object' && detail?.error === 'name_conflict') {
      const err = new Error(detail.message || i18n.t('errors.paper_name_conflict'));
      err.code = 'name_conflict';
      err.existing_mediaid = detail.existing_mediaid;
      throw err;
    }
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${r.status}`);
  }
  return r.json();
};

/**
 * Deletes a custom paper (cleanup of orphaned ICC backups included
 * on the backend side).
 * @returns {Promise<{mediaid, name, outcome}>}
 */
export const deletePaper = async (mediaid) => {
  const r = await fetch(`${BASE}/api/papers/${mediaid}`, { method: 'DELETE' });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${r.status}`);
  }
  return r.json();
};


// ─── Papers calibration CLC ──────────────────────────────────────────────────

export const getCurrentCalibration = async () => {
  return http('/api/papers/calibrate/current');
};

export const startCalibration = async (mediaid) => {
  const r = await fetch(`${BASE}/api/papers/${mediaid}/calibrate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    // 409 → returns the full payload so the UI can display the
    // "already running on X" detail
    if (r.status === 409 && typeof detail === 'object') {
      const err = new Error(detail.message || i18n.t('errors.calibration_in_progress'));
      err.code = 'conflict';
      err.current = detail.current;
      throw err;
    }
    throw new Error(detail || `HTTP ${r.status}`);
  }
  return r.json();
};

/**
 * SSE subscription to the progress of a calibration in progress.
 *
 * Emits ``onEvent({ type, data })`` for each event received. Possible
 * types: ``snapshot``, ``calibration_started``, ``progress``,
 * ``calibration_finished``, ``ping``.
 *
 * @returns {{ close: () => void }} for cleanup.
 */
export const subscribeCalibrationEvents = (mediaid, onEvent) => {
  const url = `${BASE}/api/papers/${mediaid}/calibrate/events`;
  const es = new EventSource(url);

  const types = ['snapshot', 'calibration_started', 'progress', 'calibration_finished', 'ping'];
  const handlers = {};
  for (const t of types) {
    handlers[t] = (e) => {
      let data;
      try { data = e.data ? JSON.parse(e.data) : null; } catch { data = e.data; }
      onEvent?.({ type: t, data });
    };
    es.addEventListener(t, handlers[t]);
  }
  es.onerror = () => {
    // The browser reconnects on its own; we have no dedicated polling
    // fallback for P4 (the CLC is a long but one-off event, not a
    // critical continuous stream like status).
  };
  return {
    close: () => {
      for (const t of types) es.removeEventListener(t, handlers[t]);
      es.close();
    },
  };
};


// ─── Papers profiling ICC ─────────────────────────────────────────────────────

export const getActiveProfile = async () => {
  return http('/api/papers/profile/current');
};

export const startProfile = async (mediaid, body) => {
  const r = await fetch(`${BASE}/api/papers/${mediaid}/profile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = `HTTP ${r.status}`; }
    if (r.status === 409 && typeof detail === 'object') {
      const err = new Error(detail.message || i18n.t('errors.profiling_in_progress'));
      err.code = detail.code || 'profile_busy';
      err.current = detail.current;
      throw err;
    }
    const msg = typeof detail === 'object' ? detail.message : detail;
    throw new Error(msg || `HTTP ${r.status}`);
  }
  return r.json();
};

export const subscribeProfileEvents = (mediaid, onEvent) => {
  const url = `${BASE}/api/papers/${mediaid}/profile/events`;
  const es = new EventSource(url);

  const types = ['snapshot', 'profile_started', 'progress', 'profile_finished', 'ping'];
  const handlers = {};
  for (const t of types) {
    handlers[t] = (e) => {
      let data;
      try { data = e.data ? JSON.parse(e.data) : null; } catch { data = e.data; }
      onEvent?.({ type: t, data });
    };
    es.addEventListener(t, handlers[t]);
  }
  es.onerror = () => {};
  return {
    close: () => {
      for (const t of types) es.removeEventListener(t, handlers[t]);
      es.close();
    },
  };
};


// ─── Mechanical properties ────────────────────────────────────────────────────

export const getMechanicalProperties = async (mediaid) => {
  return http(`/api/papers/${mediaid}/mechanical-properties`);
};

export const setMechanicalProperties = async (mediaid, body) => {
  const r = await fetch(`${BASE}/api/papers/${mediaid}/mechanical-properties`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${r.status}`); }
  return r.json();
};

export const restoreDefaultPreset = async (mediaid) => {
  const r = await fetch(`${BASE}/api/papers/${mediaid}/restore-default-preset`, { method: 'POST' });
  if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${r.status}`); }
  return r.json();
};


// ─── Z9 firmware logs ──────────────────────────────────────────────────────────

export const getRecentLogs = async ({ severity, since, event_code } = {}) => {
  const params = new URLSearchParams();
  if (severity) params.set('severity', severity);
  if (since) params.set('since', since);
  if (event_code) params.set('event_code', event_code);
  const qs = params.toString();
  return http(`/api/logs/recent${qs ? '?' + qs : ''}`);
};


// ─── Wake Z9 ─────────────────────────────────────────────────────────────────

/**
 * Wakes the Z9 from freeglaz. Blocks up to 30 s on the backend side.
 * Returns:
 *  - {ok: true, status: "awake", elapsed_seconds}
 *  - {ok: true, status: "timeout", elapsed_seconds, detail}
 *  - {ok: true, status: "unreachable", detail}
 *  - {ok: false, code: "z9_not_configured"} if Z9_HOST missing (503)
 *  - {ok: false, code: "network", message} on frontend network error
 */
export const wakeZ9 = async () => {
  try {
    const r = await fetch(`${BASE}/api/wake`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    if (r.status === 503) return { ok: false, code: 'z9_not_configured' };
    const body = await r.json().catch(() => ({}));
    return { ok: true, ...body };
  } catch (e) {
    return { ok: false, code: 'network', message: e?.message || i18n.t('errors.network_unavailable') };
  }
};

// ─── Job Queue API ──────────────────────────────────────────────────────────
//
// The JobQueuePanel consumes these endpoints with 3 s polling.
// Backend: /api/jobs returns {queue_status, number_of_jobs,
// modification_number, timestamp, jobs:[{uuid, name, user, status,
// completion_status, preview_uri, preview_source, can_reprint, ...}],
// _meta:{consecutive_failures, last_success_at, subscriber_started}}.

export const getJobs = async () => {
  if (USE_MOCKS && typeof mocks.getJobs === 'function') return mocks.getJobs();
  return http('/api/jobs');
};

export const pauseQueue = async () => {
  if (USE_MOCKS && typeof mocks.pauseQueue === 'function') return mocks.pauseQueue();
  return http('/api/jobs/queue/pause', { method: 'POST' });
};

export const resumeQueue = async () => {
  if (USE_MOCKS && typeof mocks.resumeQueue === 'function') return mocks.resumeQueue();
  return http('/api/jobs/queue/resume', { method: 'POST' });
};

/**
 * Empties the Z9 queue (Patch 3, 25/05/2026). Reproduces the
 * "Clear queue" button of the HP EWS. All jobs (Pending, Paused,
 * Active, Processing) move to Deleted on the firmware side.
 *
 * Returns:
 *  - {ok:true, removed_count:N, queue_uuid:"..."} on success
 *  - {ok:false, code:"auth_required"} if Z9_ADMIN_PWD missing (401)
 *  - {ok:false, code:"z9_error", detail} on other firmware error (502)
 *  - {ok:false, code:"network", message} on frontend network error
 */
export const clearQueue = async () => {
  if (USE_MOCKS && typeof mocks.clearQueue === 'function') return mocks.clearQueue();
  try {
    const r = await fetch(`${BASE}/api/jobs/queue`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
    });
    const body = await r.json().catch(() => ({}));
    if (r.status === 200) return { ok: true, ...body };
    if (r.status === 401) return { ok: false, code: 'auth_required', detail: body.detail };
    if (r.status === 502) return { ok: false, code: 'z9_error', detail: body.detail };
    if (r.status === 503) return { ok: false, code: 'z9_not_configured' };
    return { ok: false, code: `http_${r.status}`, detail: body.detail };
  } catch (e) {
    return { ok: false, code: 'network', message: e?.message || i18n.t('errors.network_unavailable') };
  }
};

export const cancelJob = async (jobUuid) => {
  if (USE_MOCKS && typeof mocks.cancelJob === 'function') return mocks.cancelJob(jobUuid);
  return http(`/api/jobs/${jobUuid}/cancel`, { method: 'POST' });
};

export const removeJob = async (jobUuid) => {
  if (USE_MOCKS && typeof mocks.removeJob === 'function') return mocks.removeJob(jobUuid);
  return http(`/api/jobs/${jobUuid}/remove`, { method: 'POST' });
};

/**
 * Reprints a job. Returns:
 *  - {ok:true, original_uuid, new_uuid}                           if all OK
 *  - {ok:true, original_uuid, new_uuid:null, warning}            if reprint
 *    submitted but the new job not yet visible on the queue side
 *  - {ok:false, code:"non_reprintable", detail}                  if 422
 *  - {ok:false, code:"z9_error", detail}                         if 502
 *  - {ok:false, code:"reprint_failed", stage, detail,
 *     firmware_succeeded, new_uuid}                              if 500
 *    (the frontend can show a contextual message via stage)
 *  - {ok:false, code:"network", message}                         on network error
 */
export const reprintJob = async (jobUuid) => {
  if (USE_MOCKS && typeof mocks.reprintJob === 'function') return mocks.reprintJob(jobUuid);
  try {
    const r = await fetch(`${BASE}/api/jobs/${jobUuid}/reprint`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const body = await r.json().catch(() => ({}));
    if (r.status === 200) return { ok: true, ...body };
    if (r.status === 422) {
      return { ok: false, code: 'non_reprintable', detail: body.detail };
    }
    if (r.status === 502) {
      return { ok: false, code: 'z9_error', detail: body.detail };
    }
    if (r.status === 500 && body.error === 'reprint_failed') {
      return { ok: false, code: 'reprint_failed', ...body };
    }
    return { ok: false, code: `http_${r.status}`, detail: body.detail };
  } catch (e) {
    return { ok: false, code: 'network', message: e?.message || i18n.t('errors.network_unavailable') };
  }
};

/**
 * URL of a job thumbnail preview. The backend resolves local-first
 * (freeglaz thumbnail in webapp/data/job_previews/) then falls back to
 * the Z9 firmware. The caller can just set this as the src of an <img>.
 */
export function getJobPreviewUrl(jobUuid) {
  return `${BASE}/api/jobs/${jobUuid}/preview`;
}

// ─── SSE / polling ─────────────────────────────────────────────────────────

// Polling fallback for the status stream: `subscribeStatusEvents` (below) is
// the primary path over SSE `/api/status/events`; this polls `/api/status`
// every 5 s and is used when the SSE is unavailable (repeated failures / no
// EventSource). Singleton pattern:
// - A single polling chain exists across the whole app, regardless of the
//   number of subscribers.
// - Multiple subscribers (StrictMode dev double-mount, HMR, etc.) all
//   receive the events from the same fetch — no request duplication.
// - When the last subscriber unsubscribes, the chain stops at the next
//   tick.
const _statusSubscribers = new Set();
let _statusPollerStarted = false;
const STATUS_POLL_INTERVAL_MS = 5000;

async function _statusPollerLoop() {
  while (_statusSubscribers.size > 0) {
    try {
      const raw = await http('/api/status');
      const mapped = mapStatus(raw);
      for (const cb of _statusSubscribers) cb(mapped);
    } catch (e) {
      console.warn('status polling error:', e);
    }
    if (_statusSubscribers.size === 0) break;
    await new Promise((r) => setTimeout(r, STATUS_POLL_INTERVAL_MS));
  }
  _statusPollerStarted = false;
}

export function subscribeStatus(onEvent) {
  if (USE_MOCKS) return mocks.subscribeStatus(onEvent);
  _statusSubscribers.add(onEvent);
  if (!_statusPollerStarted) {
    _statusPollerStarted = true;
    _statusPollerLoop();
  }
  return {
    close: () => { _statusSubscribers.delete(onEvent); },
  };
}

/**
 * Subscribes to the SSE stream /api/status/events. Signature:
 *   onUpdate(eventType, data)
 * where eventType ∈ {"status_full", "status_diff", "z9_state"}.
 *
 * Fallback: if EventSource fails 3 consecutive times, switches to
 * subscribeStatus (5 s polling) which calls onUpdate('status_full', status)
 * on each tick. The user does not lose visibility, only freshness. The
 * fallback is definitive for this subscriber (no SSE re-test) — on the
 * next app reload, SSE is retried.
 *
 * Mocks mode: no SSE, we simulate via mocks.subscribeStatus by
 * wrapping into (status_full, status).
 */
export function subscribeStatusEvents(onUpdate) {
  if (USE_MOCKS) {
    return mocks.subscribeStatus((status) => onUpdate('status_full', status));
  }

  let es = null;
  let fallbackSub = null;
  let consecutiveErrors = 0;
  let closed = false;

  function makeHandler(eventType) {
    return (e) => {
      consecutiveErrors = 0;
      try {
        const raw = e.data ? JSON.parse(e.data) : {};
        // The SSE payload arrives in the raw Pydantic Status format (loaded_paper,
        // inks as a list) — like GET /api/status. We pass it through mapStatus
        // to produce the internal frontend format (paper, inks as dict),
        // strictly identical to what the existing polling path does.
        // z9_state passes as-is: it is a state-machine hint, not a
        // full Status (e.g. {state: "error", reason: "..."}).
        const data = (eventType === 'status_full' || eventType === 'status_diff')
          ? mapStatus(raw)
          : raw;
        onUpdate(eventType, data);
      } catch (err) {
        console.warn(`SSE parse error on ${eventType}:`, err);
      }
    };
  }

  function activateFallback(reason) {
    if (closed || fallbackSub) return;
    console.warn(`SSE → fallback polling (${reason})`);
    if (es) { es.close(); es = null; }
    fallbackSub = subscribeStatus((status) => onUpdate('status_full', status));
  }

  function connect() {
    es = new EventSource(BASE + '/api/status/events');
    es.addEventListener('status_full', makeHandler('status_full'));
    es.addEventListener('status_diff', makeHandler('status_diff'));
    es.addEventListener('z9_state',    makeHandler('z9_state'));
    // 'ping' (15 s keepalive): no useful payload, but we relay it as a
    // CONNECTION LIVENESS SIGNAL → useStatus uses it to avoid wrongly
    // crying "service lost" during an idle period without a status change.
    es.addEventListener('ping', () => { consecutiveErrors = 0; onUpdate('ping', null); });

    es.onerror = () => {
      // PERMANENT failure (e.g. 503 at handshake when z9=None): EventSource goes
      // readyState=CLOSED and does NOT reconnect → onerror no longer fires, the
      // counter would stay stuck at 1 (<3) and the fallback would never arm.
      // → we switch RIGHT AWAY to polling (activateFallback is idempotent).
      if (es && es.readyState === EventSource.CLOSED) {
        activateFallback('EventSource CLOSED (permanent failure, e.g. 503)');
        return;
      }
      // Otherwise (transient drop after a 200 — EventSource really retries):
      // we count the failures and switch beyond the threshold.
      consecutiveErrors++;
      if (consecutiveErrors >= 3) {
        activateFallback('3 consecutive errors');
      }
    };
  }

  connect();

  return {
    close: () => {
      closed = true;
      if (es) es.close();
      if (fallbackSub) fallbackSub.close();
    },
  };
}

// Maps backend `stage` (cf. backend models.JobStage) → frontend `state`
// (used by App.jsx, usePrintJob, etc.). The frontend's historical
// nomenclature (inherited from the Claude Design bundle typedefs) speaks of
// 'completed'/'failed', the Pydantic backend uses 'done'/'error'.
const WORKER_STAGE_TO_STATE = {
  preparing: 'preparing',
  preflight: 'preflight',
  rendering: 'rendering',
  sending:   'sending',
  done:      'completed',
  error:     'failed',
  cancelled: 'cancelled',
};

function _normalizeWorkerEvent(payload) {
  return {
    state:    WORKER_STAGE_TO_STATE[payload.stage] || payload.stage,
    progress: (payload.progress ?? 0) / 100,  // backend 0-100 → frontend 0-1
    message:  payload.message,
    data:     payload.data,
  };
}

export function subscribePrintJob(jobId, onEvent) {
  if (USE_MOCKS) return mocks.subscribePrintJob(jobId, onEvent);
  const es = new EventSource(BASE + `/api/print/${jobId}/events`);
  // Bug B16.1: the backend emits ALL events with an event-name
  // ('stage' for intermediate stages, 'done'/'error'/'cancelled'
  // for terminal ones — cf. printing.py _EVENT_NAME_BY_STAGE). But
  // `es.onmessage` catches ONLY events WITHOUT an event-name. Consequence:
  // no event ever reached the frontend, progress stayed at 0 and
  // state at 'idle', never resolved to 'completed' → post-send UI reset
  // (B16) never triggered. Fix: addEventListener for each named
  // type + shape normalization (stage → state, progress 0-100 → 0-1).
  const handler = (e) => {
    try { onEvent(_normalizeWorkerEvent(JSON.parse(e.data))); } catch {}
  };
  es.addEventListener('stage',     handler);
  es.addEventListener('done',      handler);
  es.addEventListener('error',     handler);
  es.addEventListener('cancelled', handler);
  return { close: () => es.close() };
}

// ─── FREE chart (creation configurator) ─────────────────────────────────────
// Real endpoints (no mock: the configurator talks to the Z9 backend).
export async function getChartFormats() {
  return http('/api/charts/formats');
}
export async function getTargenHelp() {
  return http('/api/charts/targen-help');
}
export async function getTargenPresets() {
  return http('/api/charts/targen-presets');
}
export async function saveTargenPreset(body) {
  return http('/api/charts/targen-presets', { method: 'POST', body: JSON.stringify(body) });
}
// colprof (profile step) — symmetric to targen; reuses strategies.py on the backend.
export async function getChartColprofHelp() {
  return http('/api/charts/colprof-help');
}
export async function getChartColprofPresets() {
  return http('/api/charts/colprof-presets');
}
export async function saveChartColprofPreset(body) {
  return http('/api/charts/colprof-presets', { method: 'POST', body: JSON.stringify(body) });
}
export async function getPreconditionProfiles({ paper, printGe }) {
  const qs = new URLSearchParams({ paper, print_ge: printGe || 'OFF' });
  return http(`/api/charts/precondition-profiles?${qs}`);
}
export async function createChart(body) {
  return http('/api/charts', { method: 'POST', body: JSON.stringify(body) });
}
export async function getCharts({ paper, printed, scanned, purpose } = {}) {
  const qs = new URLSearchParams();
  if (paper) qs.set('paper', paper);
  if (printed != null) qs.set('printed', printed ? 'true' : 'false');
  if (scanned != null) qs.set('scanned', scanned ? 'true' : 'false');
  if (purpose) qs.set('purpose', purpose);
  const s = qs.toString();
  return http(`/api/charts${s ? `?${s}` : ''}`);
}
export function chartPreviewUrl(chartId) {
  return `${BASE}/api/charts/${encodeURIComponent(chartId)}/preview`;
}
export async function printChart(chartId, body) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/print`,
              { method: 'POST', body: JSON.stringify(body || {}) });
}
/** Launches the SOL scan in the BACKGROUND (non-blocking) → {ok, session_id, job:{state:'running'}}.
 *  Then poll getScanStatus. Throws an Error "HTTP 409" (scan already running) or
 *  "HTTP 429" (SOL cooldown <30 s) which the caller must distinguish. */
export async function scanChart(chartId, body) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/scan`,
              { method: 'POST', body: JSON.stringify(body || {}) });
}
/** Background scan progress (poll) →
 *  {job:{state,phase,percent,ti3,n_patches,bands,error}, session, resume_confirmation_required, cooldown_remaining_s}. */
export async function getScanStatus(chartId) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/scan/status`, { method: 'GET' });
}
/** Abandons this chart's active scan session (releases the "only one active" lock). */
export async function abandonScan(chartId) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/scan/abandon`, { method: 'POST' });
}
/** Mini ΔE2000 inter-scan report for the session (≥2 scans kept) → safeguard before averaging.
 *  → {n_scans, n_patches, summary:{mean,median,p95,max,worst_patch_id,isolated_outlier}, patches}. */
export async function getScanDelta(chartId) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/scan/delta`, { method: 'GET' });
}
/** Aggregated detail of a chart (Measurements tab) → {identity, scans, profile} (READ-ONLY). */
export async function getChartDetail(chartId) {
  return http(`/api/charts/${encodeURIComponent(chartId)}`, { method: 'GET' });
}
/** Additional sources eligible for the multi-source build (same paper+GE, authoritative):
 *  { paper_media_id, gloss_enhancer, extra_charts:[{chart_id, n_scans, …}], profiles:[{path, label}] }. */
export async function getBuildSources(chartId) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/build-sources`, { method: 'GET' });
}
/** QC patch × scan matrix (≥2 included) → {n_scans, scans, n_patches, summary,
 *  patches:[{id, rgb, readings:[{ti3, lab, rejected, outlier}], disagreement, n_valid}]}. READ-ONLY. */
export async function getScanQC(chartId) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/scan/qc`, { method: 'GET' });
}
/** Measurements of ONE scan (≥1) → {ti3, n_patches, patches:[{id, rgb, lab}]} (view + CSV export). */
export async function getScanPatches(chartId, ti3) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/scans/${encodeURIComponent(ti3)}/patches`,
              { method: 'GET' });
}
/** PERMANENTLY deletes a measurement set (ti3 + cgats) — destructive, irreversible (vs
 *  "exclude" reversible). UI confirmation. Local, no Z9. */
export async function deleteScan(chartId, ti3) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/scans/${encodeURIComponent(ti3)}`,
              { method: 'DELETE' });
}
/** Rejects/re-includes ONE reading (patch × scan) — the QC scalpel. Anchored to SAMPLE_ID.
 *  Software MUTATION, no Z9. The "≥1 valid reading/patch" guard returns 409. */
export async function setReadingRejected(chartId, ti3, sampleId, rejected) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/scans/${encodeURIComponent(ti3)}`
              + `/readings/${encodeURIComponent(sampleId)}/reject`,
              { method: 'POST', body: JSON.stringify({ rejected }) });
}
/** Sets the ROLE of a scan (software MUTATION, no Z9): 'included' (feeds the profile) |
 *  'excluded' (out of the profile, but ti3 KEPT/displayed). Role = durable flag, ti3 never deleted. */
export async function setScanRole(chartId, ti3, role) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/scans/${encodeURIComponent(ti3)}/role`,
              { method: 'POST', body: JSON.stringify({ role }) });
}
/** Starts the profile build as a BACKGROUND JOB → {chart_id, job} (in sync mode: flat result).
 *  Track via getProfileStatus. Local, no Z9. */
export async function profileChart(chartId, body) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/profile`,
              { method: 'POST', body: JSON.stringify(body || {}) });
}
/** State of the CURRENT/last profile build for this chart → {state, phase, result|error}. */
export async function getProfileStatus(chartId) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/profile/status`, { method: 'GET' });
}
/** POLLS the build status until done|error (WITHOUT restarting). onPhase(phase) = live feedback.
 *  Resolves with the result (unchanged shape), null if no build remains (idle). Used for re-attaching. */
export async function pollProfileBuild(chartId, onPhase) {
  for (;;) {
    const st = await getProfileStatus(chartId);
    if (onPhase && st.phase) onPhase(st.phase);
    if (st.state === 'done') return st.result;
    if (st.state === 'error') throw new Error(st.error || i18n.t('errors.profile_build_failed'));
    if (st.state === 'idle') return null;
    await new Promise((r) => setTimeout(r, 1500));
  }
}
/** Launches the build (background job) THEN polls until done|error. Resolves with the result (same shape
 *  as before the move to a job). Shared by wizard + Measurements (a single build path). */
export async function buildProfileAndWait(chartId, body, onPhase) {
  const started = await profileChart(chartId, body);          // starts the job
  const job = started?.job;
  if (job?.state === 'done') return job.result;               // sync mode: already done
  if (job?.state === 'error') throw new Error(job.error || i18n.t('errors.profile_build_failed'));
  return pollProfileBuild(chartId, onPhase);                  // async: track the job
}
// profcheck (b) Mechanism 1 — validate a profile against an INDEPENDENT chart already scanned.
// Terminal forward A2B (profcheck) instead of colprof. Local, no Z9 action.
export async function validateChart(chartId, body) {
  return http(`/api/charts/${encodeURIComponent(chartId)}/validate`,
              { method: 'POST', body: JSON.stringify(body || {}) });
}
// Disk management (manual deletion; never automatic).
export async function deleteChart(chartId) {
  return http(`/api/charts/${encodeURIComponent(chartId)}`, { method: 'DELETE' });
}
export async function lightenChart(chartId) {       // deletes the TIFF chart, keeps the ti3
  return http(`/api/charts/${encodeURIComponent(chartId)}/lighten`, { method: 'POST' });
}
