// API mocks — let the whole app run without a backend.
// Artificial delays to give a realistic feel.

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const uuid  = () => crypto.randomUUID();

// Mutable global state — a single user in dev, no concurrent edits.
let _paperLoaded = true;
let _currentJob  = null;
let _statusSubscribers = new Set();

/** @returns {import('./types').Status} */
function snapshotStatus() {
  // Mocks: we simulate z9_activity = Processing while a job runs,
  // NoActivity otherwise. Lets us test the firmware-driven E_PRINTING
  // state without a real backend.
  return {
    z9_state: _currentJob ? 'busy' : 'ready',
    z9_configured: true,   // the mock simulates a configured printer (no onboarding)
    paper: _paperLoaded ? {
      kind: 'sheet',
      name: 'Canson Baryta Photographique II',
      width_mm: 209,
      height_mm: 292,
      capabilities: ['gloss_enhancer', 'max_detail'],
      icc_profile: 'HP_Z9_Canson_Baryta_GEON',
    } : null,
    inks: [
      { id: 'Y',  level: 0.62 },
      { id: 'M',  level: 0.74 },
      { id: 'C',  level: 0.41 },
      { id: 'K',  level: 0.88 },
      { id: 'LC', level: 0.55 },
      { id: 'LM', level: 0.68 },
      { id: 'Cl', level: 0.33 },
      { id: 'GE', level: 0.81 },
    ],
    current_job: _currentJob,
    z9_activity: _currentJob
      ? { name: 'Processing', progress_pct: null }
      : { name: 'NoActivity', progress_pct: null },
  };
}

function pushStatus() {
  const snap = snapshotStatus();
  for (const cb of _statusSubscribers) cb(snap);
}

export async function getStatus() {
  await sleep(120);
  return snapshotStatus();
}

export function subscribeStatus(onEvent) {
  _statusSubscribers.add(onEvent);
  // Push initial
  setTimeout(() => onEvent(snapshotStatus()), 0);
  return { close: () => _statusSubscribers.delete(onEvent) };
}

// ─── Files ─────────────────────────────────────────────────────────────────
const _files = new Map();

export async function postFile(file) {
  await sleep(450);
  const id = uuid();
  // ICC scenarios driven by a keyword in the file name (4 backend states):
  //   /proof|client/    → mismatch (red)
  //   /noicc|stripped/  → none     (gray, file without ICC)
  //   /offline|down/    → unknown  (amber, simulates Z9 SOAP failure)
  //   otherwise         → match    (green)
  // In reality, the backend reads the ICC bytes and computes an MD5 — here we
  // just hardcode the state to run the frontend in isolation.
  const isProof    = /proof|client/i.test(file.name);
  const noIcc      = /noicc|stripped/i.test(file.name);
  const z9Offline  = /offline|down/i.test(file.name);
  const isOversize = /oversize|huge|large/i.test(file.name);

  let icc_status, icc_profile;
  if (noIcc)           { icc_status = 'none';     icc_profile = null; }
  else if (z9Offline)  { icc_status = 'unknown';  icc_profile = 'HP_Z9_Canson_Baryta_GEON'; }
  else if (isProof)    { icc_status = 'mismatch'; icc_profile = 'sRGB IEC61966-2.1'; }
  else                 { icc_status = 'match';    icc_profile = 'HP_Z9_Canson_Baryta_GEON'; }

  // Dimensions: we attempt the TIFF parse via UTIF.js (dynamic import → the
  // lib is only loaded when a mock upload is performed, never in
  // production with USE_MOCKS=false). Hardcoded fallback 100×150 mm if:
  //   - the file is not a TIFF
  //   - the parse fails (exotic TIFF, weird sub-IFD, etc.)
  //   - the /oversize/ flag is explicitly in the name (keeps the
  //     C_OVERSIZED test path, overrides the real parse)
  // ICC stays a placeholder on the mock side (parsing the mluc desc tag in JS =
  // too complex for a dev helper).
  let parsed = null;
  if (!isOversize && /\.tiff?$/i.test(file.name)) {
    parsed = await _parseTiffMeta(file);
  }

  const info = {
    id,
    filename: file.name,
    width_mm:  isOversize ? 240 : (parsed?.width_mm  ?? 100),
    height_mm: isOversize ? 340 : (parsed?.height_mm ?? 150),
    dpi:                          (parsed?.dpi       ?? 600),
    icc_profile: parsed?.has_icc === false ? null : icc_profile,
    icc_status:  parsed?.has_icc === false ? 'none' : icc_status,
  };
  _files.set(id, info);
  return { file_id: id };
}


/**
 * Reads dimensions / DPI / ICC presence of a TIFF via UTIF.js.
 * Dynamic import: the UTIF lib only enters the bundle for
 * builds that actually run this path (= dev with VITE_USE_MOCKS).
 * In prod with a real backend, UTIF is never referenced → tree-shaken.
 *
 * Returns ``null`` if parsing fails (exotic TIFF, corrupted file,
 * etc.). The caller then does a hardcoded fallback.
 */
async function _parseTiffMeta(file) {
  try {
    const UTIF = (await import('utif')).default;
    const ab = await file.arrayBuffer();
    const ifds = UTIF.decode(ab);
    if (!ifds || ifds.length === 0) return null;
    const ifd = ifds[0];
    // UTIF exposes `width`/`height` directly, plus the raw TIFF tags
    // as tNNN[]. RATIONAL (XRes/YRes) = [num, den].
    const w_px = ifd.width  ?? (ifd.t256 ? ifd.t256[0] : null);
    const h_px = ifd.height ?? (ifd.t257 ? ifd.t257[0] : null);
    if (!w_px || !h_px) return null;
    const xres = _readRational(ifd.t282) ?? 300;
    const yres = _readRational(ifd.t283) ?? 300;
    return {
      width_mm:  (w_px / xres) * 25.4,
      height_mm: (h_px / yres) * 25.4,
      dpi:       Math.round((xres + yres) / 2),
      has_icc:   Boolean(ifd.t34675 && ifd.t34675.length > 0),
    };
  } catch (e) {
    console.warn('[mocks.postFile] TIFF parse failed via UTIF, fallback hardcoded:', e);
    return null;
  }
}

function _readRational(arr) {
  if (!arr || !arr.length) return null;
  // UTIF returns either [num, den] (standard rational), or [value]
  // already resolved depending on the version. We handle both.
  if (arr.length >= 2 && arr[1]) return arr[0] / arr[1];
  return arr[0];
}

export async function getFileInfo(id) {
  await sleep(80);
  const info = _files.get(id);
  if (!info) throw new Error('file not found');
  return info;
}

// Placeholder SVG when VITE_USE_MOCKS=true. Roughly reproduces the
// blue-gray-landscape gradient of the original rectangle from the Claude Design
// bundle, to keep a visually meaningful preview without a backend.
const _PLACEHOLDER_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300" preserveAspectRatio="none">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#8c9aa8"/>
      <stop offset="35%" stop-color="#5a6470"/>
      <stop offset="70%" stop-color="#3c4550"/>
      <stop offset="100%" stop-color="#6b5a48"/>
    </linearGradient>
  </defs>
  <rect width="400" height="300" fill="url(#g)"/>
  <rect x="0" y="65%" width="400" height="18%" fill="rgba(220,180,120,0.45)"/>
  <rect x="0" y="83%" width="400" height="17%" fill="rgba(20,25,30,0.6)"/>
</svg>`;
const _PLACEHOLDER_DATAURL = `data:image/svg+xml;utf8,${encodeURIComponent(_PLACEHOLDER_SVG)}`;

export function getFilePreviewUrl(_fileId) {
  return _PLACEHOLDER_DATAURL;
}

// ─── Preview / Print ───────────────────────────────────────────────────────
// Simulated mechanical margins (MANUAL_FEED bottom 17.4 mm like the real Z9
// to reproduce the asymmetric behavior on the mocks side too).
const MOCK_MARGINS = { left: 5, top: 5, right: 5, bottom: 17.4 };

export async function postPrintPreview({ file_id, params = {} }) {
  await sleep(60);
  const info = _files.get(file_id);
  if (!info) throw new Error('file not found');
  const paper = snapshotStatus().paper;
  if (!paper) throw new Error('no paper');

  const sheet_w = paper.width_mm;
  // Economic ROLL (no defined sheet height): we mirror the backend
  // which returns sheet_h = image_h + 10mm + vertical offset (the effective
  // top must be included, otherwise MediaBox < PAPERLENGTH PJL). Avoids an
  // "infinite sheet" 9999mm visually absurd on the preview side.
  const sheet_h = paper.height_mm
    || (paper.kind === 'roll' ? info.height_mm + 10 + (params.offset_y_mm ?? 0) : 9999);
  const printable_x = MOCK_MARGINS.left;
  const printable_y = MOCK_MARGINS.top;
  const printable_w = Math.max(0, sheet_w - MOCK_MARGINS.left - MOCK_MARGINS.right);
  const printable_h = Math.max(0, sheet_h - MOCK_MARGINS.top  - MOCK_MARGINS.bottom);

  // User offsets (drag image / Center button). The shape sent by
  // useFileLoader is { file_id, params: { offset_x_mm, offset_y_mm, ... } } —
  // not top-level. Bug B8: we silently ignored them, so the drag
  // and the Center click had no visible effect on the mocks side.
  const offset_x_mm = params.offset_x_mm ?? 0;
  const offset_y_mm = params.offset_y_mm ?? 0;

  // Orientation: 90/270 transposes the footprint (consistent with oriented_dims
  // on the backend side). Mock symmetric to the real pipeline.
  const orientation = params.orientation ?? 0;
  const oriented = orientation === 90 || orientation === 270;
  const eff_w = oriented ? info.height_mm : info.width_mm;
  const eff_h = oriented ? info.width_mm : info.height_mm;

  // Image centered on the sheet (consistent with MANUAL_FEED on the backend
  // side) + user offset. Symmetric to webapp/backend/services/print_geometry.py.
  const image_x = (sheet_w - eff_w) / 2 + offset_x_mm;
  const image_y = (sheet_h - eff_h) / 2 + offset_y_mm;
  const img_right  = image_x + eff_w;
  const img_bottom = image_y + eff_h;

  const overflow_left   = Math.max(0, printable_x - image_x);
  const overflow_top    = Math.max(0, printable_y - image_y);
  const overflow_right  = Math.max(0, img_right   - (printable_x + printable_w));
  const overflow_bottom = Math.max(0, img_bottom  - (printable_y + printable_h));
  const fits = (overflow_left + overflow_top + overflow_right + overflow_bottom) === 0;

  const geometry = {
    sheet_width_mm: sheet_w, sheet_height_mm: sheet_h,
    image_width_mm: eff_w, image_height_mm: eff_h,
    image_x_mm: image_x, image_y_mm: image_y,
    // Auto anchors (mock = centered MANUAL_FEED): values before user offset.
    auto_x_mm: (sheet_w - eff_w) / 2,
    auto_y_mm: (sheet_h - eff_h) / 2,
    margin_left_mm:   image_x,
    margin_top_mm:    image_y,
    margin_right_mm:  sheet_w - image_x - eff_w,
    margin_bottom_mm: sheet_h - image_y - eff_h,
    media_source: 'MANUAL_FEED',
    centered_x: offset_x_mm === 0,
    centered_y: offset_y_mm === 0,
    printable_x_mm: printable_x, printable_y_mm: printable_y,
    printable_w_mm: printable_w, printable_h_mm: printable_h,
    overflow_left_mm: overflow_left, overflow_top_mm: overflow_top,
    overflow_right_mm: overflow_right, overflow_bottom_mm: overflow_bottom,
  };

  // Human-readable reason depending on the state (consistent with print_geometry.icc_match_status).
  const icc_match_reason =
    info.icc_status === 'match'    ? 'Profils byte-identiques (MD5)' :
    info.icc_status === 'mismatch' ? 'Bytes ICC différents (MD5)' :
    info.icc_status === 'unknown'  ? 'Profil ICC du papier non résolu (Z9 indisponible ?)' :
    info.icc_status === 'none'     ? 'Fichier sans profil ICC embarqué' :
                                     null;

  // Geometric diagnostic B13.1: 3 distinct cases per axis (image
  // too large / position < margin / position > max). Reproduced in
  // the mock to stay consistent with the real backend, otherwise the
  // oversize scenarios in mocks would show a bland error.
  const blocking_issues = [];
  const too_wide = info.width_mm  > printable_w + 0.01;
  const too_tall = info.height_mm > printable_h + 0.01;
  if (too_wide) {
    blocking_issues.push(
      `Image trop large pour la zone imprimable : ${info.width_mm.toFixed(0)} mm > ${printable_w.toFixed(0)} mm (largeur utile = feuille − marges mécaniques)`
    );
  } else {
    if (image_x < printable_x - 0.01) {
      blocking_issues.push(
        `Position X (${image_x.toFixed(1)} mm) inférieure à la marge gauche (${printable_x.toFixed(0)} mm)`
      );
    }
    const max_x = printable_x + printable_w - info.width_mm;
    if (image_x > max_x + 0.01) {
      blocking_issues.push(
        `Image déborde à droite : Position X max = ${max_x.toFixed(1)} mm (actuelle ${image_x.toFixed(1)} mm)`
      );
    }
  }
  if (too_tall) {
    blocking_issues.push(
      `Image trop haute pour la zone imprimable : ${info.height_mm.toFixed(0)} mm > ${printable_h.toFixed(0)} mm (hauteur utile = feuille − marges mécaniques)`
    );
  } else {
    if (image_y < printable_y - 0.01) {
      blocking_issues.push(
        `Position Y (${image_y.toFixed(1)} mm) inférieure à la marge haute (${printable_y.toFixed(0)} mm)`
      );
    }
    const max_y = printable_y + printable_h - info.height_mm;
    if (image_y > max_y + 0.01) {
      blocking_issues.push(
        `Image déborde en bas : Position Y max = ${max_y.toFixed(1)} mm (actuelle ${image_y.toFixed(1)} mm)`
      );
    }
  }

  return {
    fits,
    overflow_mm: fits ? undefined : {
      left: overflow_left, top: overflow_top,
      right: overflow_right, bottom: overflow_bottom,
    },
    margin_mm: fits ? { x: image_x, y: image_y } : undefined,
    icc_status:       info.icc_status,
    icc_match_reason,
    geometry,
    blocking_issues,
    warnings: [],
    file_icc_name:    info.icc_profile,
    file_icc_md5:     info.icc_profile ? 'mockfile' + (info.id || '').slice(0, 24) : null,
    paper_icc_name:   paper.icc_profile,
    paper_icc_md5:    info.icc_status === 'unknown' ? null : 'mockpaper' + paper.name.slice(0, 23),
  };
}

export async function postPrint({ file_id, params }) {
  await sleep(200);
  const info = _files.get(file_id);
  const job_id = uuid();
  _currentJob = {
    id: job_id,
    progress: 0,
    state: 'sending',
    filename: info?.filename || 'untitled',
  };
  pushStatus();
  return { job_id };
}

export async function postPrintCancel(jobId) {
  await sleep(80);
  // Dev helper: window.__z9.simulate409 = true → cancel mocked as if the
  // Z9 were still printing after the PRN send. Lets us test the B6 toast without
  // depending on the real backend.
  if (typeof window !== 'undefined' && window.__z9?.simulate409) {
    return {
      ok: false,
      code: 'post_send_no_remote_cancel',
      message: "L'impression est en cours sur la Z9. Annulez via le panneau front de l'imprimante. (Cancel à distance non implémenté, cf. bug B6.)",
      z9_activity: 'Drying',
    };
  }
  if (_currentJob?.id === jobId) {
    _currentJob = null;
    pushStatus();
  }
  return { ok: true };
}

export function subscribePrintJob(jobId, onEvent) {
  // Simulates 0 → 100 % over 12 seconds
  let pct = 0;
  const tick = setInterval(() => {
    if (!_currentJob || _currentJob.id !== jobId) {
      clearInterval(tick);
      onEvent({ state: 'cancelled', progress: pct / 100 });
      return;
    }
    pct = Math.min(100, pct + 5);
    _currentJob.progress = pct / 100;
    _currentJob.state    = pct < 100 ? (pct < 20 ? 'sending' : 'printing') : 'completed';
    pushStatus();
    onEvent({ state: _currentJob.state, progress: _currentJob.progress, job_id: jobId });
    if (pct >= 100) {
      clearInterval(tick);
      setTimeout(() => { _currentJob = null; pushStatus(); }, 800);
    }
  }, 600);
  return { close: () => clearInterval(tick) };
}

// ─── Convert (DeviceLink, JALON 1 socle) ────────────────────────────────────
// Simulated detection: uploaded mock files carry an embedded profile so the UI
// can exercise the "source space + TRC" panel. Toggle window.__z9.noSourceIcc
// to test the "image without ICC → cannot convert" path.
export async function getConvertSourceInfo(fileId) {
  await sleep(80);
  if (!_files.get(fileId)) throw new Error('file not found');
  if (typeof window !== 'undefined' && window.__z9?.noSourceIcc) {
    return { has_profile: false };
  }
  return {
    has_profile: true,
    color_space: 'RGB ',
    pcs: 'Lab ',
    trc: {
      family: { auto: 'gamma_2_2', override: null, effective: 'gamma_2_2' },
      per_channel: {},
      consistent_across_channels: true,
      primary_family_label: 'Gamma 2.2',
    },
  };
}

// Mock conversion: returns a tiny placeholder blob + a filename, mirroring the
// { blob, filename } shape the real client resolves to.
export async function postConvert(body) {
  await sleep(300);
  if (!_files.get(body.file_id)) throw new Error('file not found');
  const blob = new Blob([_PLACEHOLDER_SVG], { type: 'image/tiff' });
  const ia = body.image_aware ? '_ia' : '';
  return { blob, filename: `converted_${body.intent || 'r'}${ia}.tif` };
}

// ─── DEV helpers ──────────────────────────────────────────────────────────
// Exposed on window to tinker from the console during implementation.
if (typeof window !== 'undefined') {
  window.__z9 = {
    unloadPaper: () => { _paperLoaded = false; pushStatus(); },
    loadPaper:   () => { _paperLoaded = true;  pushStatus(); },
    cancelJob:   () => { _currentJob = null;   pushStatus(); },
  };
}
