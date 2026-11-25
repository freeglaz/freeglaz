// Measurement method of a profiling chart: the built-in HP spectrophotometer (honeycomb chart,
// SOL scan, Z9 only) or an external spectrophotometer (printtarg strips read by chartread, any
// printer). Pure functions only: the backend stays the authority (whitelist, printer family
// guards, media); the interface only presents what it is told and never invents a capability.

export const METHOD_INTEGRATED = 'integrated';
export const METHOD_EXTERNAL = 'external';
export const LAYOUT_BY_METHOD = { [METHOD_INTEGRATED]: 'honeycomb', [METHOD_EXTERNAL]: 'printtarg' };
// Tolerance on a roll width, same as the chart format default (_defaultFormatKey).
const ROLL_WIDTH_TOL_MM = 15;
// A loaded sheet matches a sheet format of its width (±SHEET_WIDTH_TOL_MM) and of its height or
// at most SHEET_LENGTH_SHORT_MM shorter: the Z9 measures sheets shorter than their nominal size
// (an A4 reads ~293 mm). Same values as the print route (printtarg_chart.loaded_media_mismatch),
// which checks the sheet actually loaded and refuses a chart on media of another format.
const SHEET_WIDTH_TOL_MM = 3;
const SHEET_LENGTH_SHORT_MM = 10;
// Sources a sheet is identified on (as the print route: printtarg_chart._SHEET_SOURCES).
const SHEET_SOURCES = ['MANUAL_FEED', 'SHEET'];

/** Which methods can be offered for a printer family ('z9' | 'z9pro' | 'designjet' | null).
 *  integrated: proven on the Z9 only. Unknown family (null: status not loaded or printer
 *  unreachable) → offered as before but flagged unverified (the server decides at scan time).
 *  external: offered on every family as soon as the backend lists an instrument; creation is
 *  blocked (not hidden) when printtarg is missing. ``options`` = GET /printtarg-options, null
 *  while loading, false when it could not be read. */
export function methodAvailability(family, options) {
  const integrated = family === 'z9' ? { available: true, note: null }
    : family == null ? { available: true, note: 'family_unknown' }
      : { available: false, note: family === 'z9pro' ? 'z9pro' : 'family_unsupported' };
  let external;
  if (options === false) external = { available: false, note: 'options_unavailable' };
  else if (!options) external = { available: false, note: 'options_loading' };
  else if (!(options.instruments || []).length) external = { available: false, note: 'no_instrument' };
  else external = { available: true, note: options.binaries?.printtarg?.available === false ? 'printtarg_missing' : null };
  return { [METHOD_INTEGRATED]: integrated, [METHOD_EXTERNAL]: external };
}

/** Default method: the built-in scan on a Z9 (today's behaviour, also while the family is not
 *  known yet); the external instrument wherever the built-in scan is not offered. */
export function defaultMethod(family) {
  return family == null || family === 'z9' ? METHOD_INTEGRATED : METHOD_EXTERNAL;
}

/** The method actually used: the user's explicit choice while it stays available (it is never
 *  changed behind their back, going back in the wizard included), otherwise the default, or
 *  the other method when the default is not available. null when nothing can be offered. */
export function resolveMethod(choice, availability, family) {
  const ok = (m) => !!availability?.[m]?.available;
  if (choice && ok(choice)) return choice;
  const d = defaultMethod(family);
  if (ok(d)) return d;
  const other = d === METHOD_INTEGRATED ? METHOD_EXTERNAL : METHOD_INTEGRATED;
  return ok(other) ? other : null;
}

/** The instrument used: the explicit choice if the backend still lists it, else the first one
 *  the backend lists (its own order, no list kept here). */
export function resolveInstrument(choice, options) {
  const list = options?.instruments || [];
  return list.some((i) => i.key === choice) ? choice : (list[0]?.key ?? null);
}

/** Media accepted for an external chart and the matching chart formats, in the BACKEND's order
 *  (its first medium stays the default); with an instrument, only the media the backend has a
 *  preset for with it. No instrument or medium is named here. */
export function externalFormats(options, formats, instrument = null) {
  let keys = options?.media || [];
  const inst = instrument ? (options?.instruments || []).find((i) => i.key === instrument) : null;
  if (inst) keys = keys.filter((k) => (inst.presets || []).some((p) => p.media_key === k));
  return keys.map((k) => (formats || []).find((f) => f.key === k)).filter(Boolean);
}

/** Can the LOADED media take an external chart? {status, key?, ...}:
 *  ok (key = the matching chart format) | unknown (nothing known about the loaded paper) |
 *  sheet_loaded (a sheet of no accepted format) | sheet_unidentified (a sheet whose source or
 *  dimensions are not reported: no sheet format can be confirmed) | roll_source_unsupported (a
 *  source freeglaz does not print on, e.g. ROLL2) | roll_width (a roll of another width). `expected` = the
 *  accepted roll widths, `accepted` = the names of every accepted format (for the messages).
 *  Informative before creation, blocks printing. */
export function externalMediaCheck(loadedPaper, options, formats, instrument = null) {
  const fmts = externalFormats(options, formats, instrument);
  const rolls = fmts.filter((f) => f.is_roll);
  const sheets = fmts.filter((f) => !f.is_roll);
  const expected = rolls.map((f) => f.width_mm);
  const base = { expected, accepted: fmts.map((f) => f.name || f.key) };
  if (!loadedPaper) return { status: 'unknown', ...base };
  const src = (loadedPaper.media_source || '').toUpperCase();
  if (src && src !== 'ROLL' && src.startsWith('ROLL')) return { status: 'roll_source_unsupported', source: src, ...base };
  const w = loadedPaper.width_mm;
  if (loadedPaper.kind !== 'roll') {
    const h = loadedPaper.height_mm;
    const dims = typeof w === 'number' && typeof h === 'number' && w > 0 && h > 0;
    if (!SHEET_SOURCES.includes(src) || !dims) return { status: 'sheet_unidentified', ...base };
    const hit = sheets.find((f) => f.height_mm
      && Math.abs(f.width_mm - w) <= SHEET_WIDTH_TOL_MM
      && h <= f.height_mm + SHEET_WIDTH_TOL_MM && f.height_mm - h <= SHEET_LENGTH_SHORT_MM);
    return hit ? { status: 'ok', key: hit.key, width_mm: w, ...base }
      : { status: 'sheet_loaded', sheets: sheets.map((f) => f.key), ...base };
  }
  if (typeof w !== 'number' || !(w > 0)) return { status: 'unknown', ...base };
  if (!rolls.length) return { status: 'roll_width', width_mm: w, ...base };
  const hit = rolls.find((f) => Math.abs(f.width_mm - w) <= ROLL_WIDTH_TOL_MM);
  return hit ? { status: 'ok', key: hit.key, width_mm: w, ...base }
    : { status: 'roll_width', width_mm: w, ...base };
}

/** Statuses of externalMediaCheck / externalPrintBlock that block printing an external chart. */
export const MEDIA_BLOCKING = ['sheet_loaded', 'sheet_unidentified', 'roll_width', 'roll_source_unsupported',
  'media_mismatch'];

/** What blocks printing a CREATED external chart on the loaded media, or null: a blocking status
 *  of `check` (externalMediaCheck for the chart's instrument), or `media_mismatch` when the loaded
 *  media is accepted but is not the format the chart was laid out for (an A4 chart and a roll
 *  loaded since, for example). A chart laid out for a SHEET also needs the sheet identified: when
 *  nothing is known about the loaded paper, `sheet_unidentified`. The print route refuses the same
 *  cases (chart_media_mismatch); for a roll chart an unknown paper stays the route's to check. */
export function externalPrintBlock(result, check, formats = []) {
  if (!result || result.layout !== 'printtarg' || !check) return null;
  const sheetChart = result.media_source ? result.media_source === 'sheet'
    : (formats || []).some((f) => f.key === result.media_key && !f.is_roll);
  if (MEDIA_BLOCKING.includes(check.status)) return check;
  if (check.status === 'ok' && check.key && result.media_key && check.key !== result.media_key) {
    const name = (k) => (formats || []).find((f) => f.key === k)?.name || k;
    return { ...check, status: 'media_mismatch', chart: name(result.media_key), loaded: name(check.key) };
  }
  if (sheetChart && !(check.status === 'ok' && check.key === result.media_key)) {
    return { ...check, status: 'sheet_unidentified' };
  }
  return null;
}

/** How a chart is measured, from a library item or a chart detail identity. Charts without
 *  `layout` are historical honeycomb charts (built-in scan). */
export function chartMeasurement(chart) {
  const layout = chart?.layout || 'honeycomb';
  const method = chart?.measurement_method || chart?.measurement?.method
    || (layout === 'honeycomb' ? 'sol' : layout === 'printtarg' ? 'external' : null);
  const instrument = chart?.instrument ?? (chart?.measurement?.instruments || [])[0] ?? null;
  return { layout, method, instrument, external: method === 'external', integrated: method === 'sol' };
}

/** Display name of an instrument key (UI text from the catalogs; an unknown key shows as is). */
export function instrumentLabel(t, key) {
  return key ? t(`measurement.instrument.${key}`, { defaultValue: key }) : t('measurement.instrument_unknown');
}

/** Badge of a chart: {kind: 'integrated' | 'external' | 'unknown', instrument}. */
export function chartBadge(chart) {
  const m = chartMeasurement(chart);
  return { kind: m.integrated ? 'integrated' : m.external ? 'external' : 'unknown', instrument: m.instrument };
}

/** printtarg -a / -h as the backend offers them for this instrument, or null. */
export function layoutOptionsFor(options, instrument) {
  const inst = (options?.instruments || []).find((i) => i.key === instrument);
  return inst?.layout_options || null;
}

/** The user's choice made valid for the instrument: a size the backend offers (else its
 *  default), high density only where offered, and never both (high density at full size). */
export function resolveLayoutChoice(choice, layoutOptions) {
  const scales = layoutOptions?.patch_scales || [1];
  const def = layoutOptions?.default_patch_scale ?? scales[0] ?? 1;
  const highDensity = !!(choice?.highDensity && layoutOptions?.high_density);
  const wanted = choice?.patchScale ?? def;
  const patchScale = highDensity ? def : (scales.some((s) => Math.abs(s - wanted) < 1e-9) ? wanted : def);
  return { patchScale, highDensity };
}

/** Nominal patch geometry (printtarg's) of a choice, from the backend's table, or null. */
export function layoutGeometry(layoutOptions, choice) {
  const g = layoutOptions?.geometry;
  if (!g) return null;
  if (choice?.highDensity) return g.high_density || null;
  const key = String(Number(choice?.patchScale ?? layoutOptions.default_patch_scale ?? 1));
  return g.standard?.[key] || null;
}

/** The estimate request for the current composition, or the reason there is none. */
export function estimateRequest({ instrument, mediaKey, srcMode, fCount, ti1Text, choice }) {
  if (!instrument || !mediaKey) return { skip: 'incomplete' };
  const base = { instrument, media_key: mediaKey, patch_scale: choice.patchScale, high_density: choice.highDensity };
  if (srcMode === 'ti1') return (ti1Text || '').trim() ? { body: { ...base, ti1_text: ti1Text } } : { skip: 'incomplete' };
  return fCount ? { body: { ...base, n_patches: fCount } } : { skip: 'needs_count' };
}

/** What the preview of a created printtarg chart shows — every number is the generator's. */
export function printtargSummary(result) {
  if (!result || result.layout !== 'printtarg') return null;
  const comp = result.composition || {};
  return {
    instrument: result.instrument,
    nPatches: result.n_patches,
    nStrips: result.total_strips,
    patchesPerStrip: result.steps_in_pass,
    nBlocks: result.n_pages,
    rollLayout: result.roll_layout,
    widthMm: result.tiff_w_mm,
    lengthMm: result.tiff_h_mm,
    rollLengthMm: result.roll_length_mm ?? null,
    // a sheet is not cut from a roll: the preview and the confirmation say "sheet"
    onSheet: result.media_source === 'sheet',
    mediaKey: result.media_key || null,
    stripsCrossTheRoll: comp.strips_cross_the_roll ?? (result.roll_layout === 'transverse'),
    stripOrder: comp.strip_order || null,
    patch1Side: comp.patch_1_side || null,
    iccName: result.tag?.icc_name || null,
    ge: result.tag?.gloss_enhancer || null,
    warnings: result.warnings || [],
    generatorWarnings: result.generator_warnings || [],
    // printtarg -a / -h and the patches found on the pages (charts made before: defaults)
    patchScale: result.patch_scale ?? 1,
    highDensity: !!result.high_density,
    patchGeometry: result.patch_geometry || null,
  };
}

// ── structured refusals of the measurement identity (backend codes) ────────────
export const IDENTITY_CODES = ['measurement_identity_incompatible', 'measurement_identity_insufficient',
  'measurement_record_invalid', 'scans_not_comparable'];
const FIELD_ORDER = ['method', 'instrument', 'condition', 'spectral_bands', 'calibration_standard'];

/** Backend error → presentation of an identity refusal, or null if it is not one.
 *  {code, conflicts: [{a, b, items: [{code, field, a, b}]}], remedies, recordError, message}. */
export function identityRefusal(err) {
  const d = err?.detail && typeof err.detail === 'object' ? err.detail : (err && typeof err === 'object' && err.code ? err : null);
  if (!d || !IDENTITY_CODES.includes(d.code)) return null;
  const conflicts = (d.decision?.conflicts || []).map((c) => ({
    a: c.a, b: c.b, status: c.status,
    items: [...(c.differences || [])]
      .sort((x, y) => FIELD_ORDER.indexOf(x.field) - FIELD_ORDER.indexOf(y.field))
      .map((x) => ({ code: x.code, field: x.field, a: describeValue(x.field, x.a), b: describeValue(x.field, x.b) })),
  }));
  return { code: d.code, conflicts, remedies: d.remedies || [], recordError: d.record_error || null,
           message: d.message || err?.message || '', scan: d.scan || null };
}

/** Readable value of an identity field (spectral grids are summarized). */
export function describeValue(field, v) {
  if (v == null) return null;
  if (field === 'spectral_bands' && typeof v === 'object') {
    return v.n_bands ? `${v.n_bands} × ${v.step_nm ?? '?'} nm (${v.start_nm}–${v.end_nm})` : null;
  }
  return String(v);
}

/** Short identity of one scan for a list: null for a historical SOL scan (nothing new to say),
 *  {instrument, condition, missing, error} otherwise. */
export function scanIdentityLine(identity) {
  if (!identity) return null;
  if (identity.error) return { error: identity.error.code || 'unreadable' };
  if (identity.historical) return null;
  return { method: identity.method, instrument: identity.instrument, condition: identity.condition,
           missing: identity.missing || [], hasRecord: !!identity.has_record };
}

// ── validation and multi-source notices ───────────────────────────────────────
/** Validation report → identity notice: null when nothing to say, else {status, warnings,
 *  validation, buildInstruments}. A different instrument is a warning, never an error. */
export function validationIdentityNotice(report) {
  const mi = report?.measurement_identity;
  if (!mi || mi.error) return null;
  const status = mi.comparison?.status;
  const warnings = (mi.warnings || []).map((w) => w.code);
  if (status === 'same' && !warnings.length) return null;
  const buildInstruments = [...new Set((mi.build?.scans || [])
    .map((s) => s.identity?.instrument).filter(Boolean))];
  return { status, warnings, validationInstrument: mi.validation?.identity?.instrument ?? null,
           validationCondition: mi.validation?.identity?.condition ?? null, buildInstruments,
           differences: mi.comparison?.differences || [] };
}

/** Number of source profiles in a preview whose measurements have no traceable instrument. */
export function untrackedSourceProfiles(preview) {
  return ((preview?.sources?.source_profiles) || []).filter((p) => p.identity_tracked === false).length;
}

/** Combination decision of a preview → null when nothing to show (single scan, all compatible). */
export function previewCompatibility(preview) {
  const c = preview?.measurement_compatibility;
  if (!c || !c.combined) return null;
  return { status: c.status, nMembers: (c.members || []).length };
}
