// Two-pass adaptive profiling — presentation helpers (pure functions only).
// The server composes the Argyll lines, decides the role of every build and checks every
// condition (lib/z9_client/profiling_strategies.py, routes/charts.py). Here the UI only turns
// its form into parameters, and the server's answers into what to show.

export const STRATEGY_TWO_PASS = 'two_pass_adaptive';

/** Next steps of a run (backend lib.z9_client.profiling_strategies.STEPS + first_pass_missing). */
export const STEPS = ['pass1_print', 'pass1_measure', 'intermediate_build', 'pass2_create',
  'pass2_print', 'pass2_measure', 'final_build', 'done', 'first_pass_missing'];

/** Why FWA compensation is refused (backend fwa_gate). */
export const FWA_REASONS = ['no_measurements', 'identity_unknown', 'no_spectra', 'instrument_without_uv',
  'instrument_not_supported', 'condition_unknown', 'condition_not_m0'];

/** Print condition check of a later pass (backend _strategy_print_check). */
export const PRINT_CHECK_CODES = ['paper_differs', 'gloss_enhancer_differs', 'quality_differs',
  'rendermode_differs', 'printer_differs', 'resident_changed', 'clc_changed', 'first_pass_missing',
  'first_pass_print_unknown'];

/** Refusals of a strategy chart creation the UI words (backend _strategy_context & co). */
export const CREATE_ERROR_CODES = ['precondition_build_required', 'precondition_build_unknown',
  'precondition_build_altered', 'precondition_not_intermediate', 'precondition_not_current',
  'precondition_stale', 'strategy_conditions_changed', 'strategy_instrument_locked',
  'strategy_measurement_unknown', 'earlier_pass_frozen', 'strategy_run_mismatch', 'strategy_run_required',
  'targen_failed', 'param_out_of_range', 'invalid_param', 'unknown_param'];

export const NEUTRAL_CHOICES = [0.5, 0.75, 1.0];
export const FINAL_QUALITIES = ['m', 'h', 'u'];
export const SMOOTHING_CHOICES = [2.0, 1.2];

const num = (v, d) => (typeof v === 'number' && Number.isFinite(v) ? v : d);

/** Form state of the two-pass composition, from a definition's (or a run's) parameters. */
export function formFromParams(params) {
  const p1 = params?.pass1 || {};
  const p2 = params?.pass2 || {};
  return {
    pass1Patches: num(p1.chart?.patches, 400), pass1Ofps: p1.chart?.ofps || 'G', pass1Grey: num(p1.chart?.grey_steps, 0),
    smoothing: num(p1.profile?.smoothing, 2.0),
    pass2Patches: num(p2.chart?.patches, 2000), pass2Ofps: p2.chart?.ofps || 'G',
    neutral: num(p2.chart?.neutral_emphasis, 0.75), neutralSteps: num(p2.chart?.neutral_steps, 0),
    finalQuality: p2.profile?.quality || 'h', gamut: p2.profile?.source_gamut || 'AdobeRGB',
    vcIn: p2.profile?.viewcond_in || 'mt', vcOut: p2.profile?.viewcond_out || 'pp',
  };
}

const int = (v) => Math.round(Number(v));
const float = (v) => Number(v);

/** Form state of the one-pass wizard's guided form (not a strategy), from its parameters
 *  (GET /api/profiling-strategies → one_pass.default_params). */
export function oneFormFromParams(params) {
  const c = params?.chart || {};
  const p = params?.profile || {};
  return {
    patches: num(c.patches, 200), ofps: c.ofps || 'G', grey: num(c.grey_steps, 0),
    neutral: num(c.neutral_emphasis, 0.5), neutralSteps: num(c.neutral_steps, 0),
    quality: p.quality || 'h', gamut: p.source_gamut || 'none', vcIn: p.viewcond_in || 'mt', vcOut: p.viewcond_out || 'pp',
  };
}

/** Parameters of the guided one-pass form. -N / -n only with a -c: without one targen cannot
 *  place the neutral axis (the server drops them too). */
export function oneParamsFromForm(form, preconditioned) {
  const chart = { patches: Math.round(Number(form.patches)), ofps: form.ofps, grey_steps: Math.round(Number(form.grey)) };
  if (preconditioned) {
    chart.neutral_emphasis = Number(form.neutral);
    chart.neutral_steps = Math.round(Number(form.neutralSteps));
  }
  return { chart, profile: { quality: form.quality, source_gamut: form.gamut, viewcond_in: form.vcIn, viewcond_out: form.vcOut } };
}

/** The colprof line the profile step of a chart starts from: the final line of a two-pass
 *  profiling, or the line of a guided one-pass chart; null otherwise (the default line). A
 *  preconditioning pass has its own panel. */
export function plannedColprofLine(identity) {
  const st = identity?.strategy;
  if (st) return st.role === 'final' ? (st.lines?.colprof_flags || null) : null;
  return identity?.guided?.lines?.colprof_flags || null;
}

/** Parameters sent with a pass: the first pass freezes every pass; a later pass sends its own
 *  only (the server refuses changes to an earlier pass). */
export function paramsFromForm(form, passId) {
  const pass2 = {
    chart: { patches: int(form.pass2Patches), ofps: form.pass2Ofps, neutral_emphasis: float(form.neutral),
             neutral_steps: int(form.neutralSteps) },
    profile: { quality: form.finalQuality, source_gamut: form.gamut, viewcond_in: form.vcIn, viewcond_out: form.vcOut },
  };
  if (passId === 'pass2') return { pass2 };
  return {
    pass1: { chart: { patches: int(form.pass1Patches), ofps: form.pass1Ofps, grey_steps: int(form.pass1Grey) },
             profile: { smoothing: float(form.smoothing) } },
    pass2,
  };
}

/** Patch count of the pass being composed. */
export function currentPatches(form, passId) {
  return int(passId === 'pass2' ? form.pass2Patches : form.pass1Patches);
}

/** The create request of a strategy pass (no targen line, no -c: the server's). */
export function strategyCreateRequest({ passId, form, run = null, buildId = null }) {
  return { id: STRATEGY_TWO_PASS, pass_id: passId, params: paramsFromForm(form, passId),
           run: passId === 'pass1' ? null : run, precondition_build_id: passId === 'pass1' ? null : buildId };
}

const isFwaTok = (t) => t === '-f' || /^-f\S/.test(t);

/** colprof line with or without FWA compensation (-f). */
export function withFwa(flags, on) {
  const toks = (flags || '').trim().split(/\s+/).filter(Boolean).filter((t) => !isFwaTok(t));
  if (on) toks.push('-f');
  return toks.join(' ');
}
export function hasFwa(flags) {
  return (flags || '').trim().split(/\s+/).some(isFwaTok);
}

/** Where a chart stands in its run, for a badge. null for a one-pass chart. */
export function strategyBadge(strategy) {
  if (!strategy || !strategy.pass) return null;
  const known = strategy.id === STRATEGY_TWO_PASS;
  const index = known ? ({ pass1: 1, pass2: 2 }[strategy.pass] ?? null) : null;
  return { id: strategy.id, pass: strategy.pass, role: strategy.role || null, index, total: known ? 2 : null,
           run: strategy.run || null };
}

/** What the resume card offers for a run's next step. */
export function stepAction(step) {
  if (step === 'pass2_create') return 'create_pass2';
  if (step === 'pass1_print') return 'print_first';     // created, never printed: print it now
  if (step === 'pass2_print') return 'print_last';
  if (step === 'pass1_measure' || step === 'intermediate_build') return 'open_first';
  return 'open_last';
}

/** Runs still waiting for something (resume card), most recent first (the server's order). */
export function activeRuns(runs) {
  return (runs || []).filter((r) => r && r.next_step && r.next_step !== 'done' && r.next_step !== 'first_pass_missing');
}

/** Sum of the paper of the two passes, or null when one is not known. */
export function paperTotal(...mms) {
  if (!mms.length || mms.some((v) => typeof v !== 'number' || !Number.isFinite(v))) return null;
  return mms.reduce((a, b) => a + b, 0);
}

/** State of the run's intermediate profile for the requested colprof line:
 *  none | stale | other_settings (built with another line) | ready. */
export function intermediateState(first, requestedFlags = null) {
  const i = first?.intermediate;
  if (!i?.built) return 'none';
  if (i.state === 'stale' || i.state === 'missing_input') return 'stale';
  const built = Array.isArray(i.colprof_flags) ? i.colprof_flags.join(' ') : (i.colprof_flags || '');
  if (requestedFlags && built && built !== requestedFlags.trim()) return 'other_settings';
  return 'ready';
}

/** The sources of a final build for a variant (the build panel then previews it). */
export function variantSources(variant, run) {
  return variant === 'pass1_pass2' && run
    ? { extra_chart_ids: [run], nameSuffix: '_p1p2' }
    : { extra_chart_ids: [], nameSuffix: '' };
}

/** A failed creation that offers the quasi-random fallback (never applied by itself). */
export function offersQuasiRandomFallback(err) {
  const d = err?.detail;
  return !!(d && typeof d === 'object' && d.code === 'targen_failed' && d.fallback === 'Q');
}

/** Print refused or to confirm for a later pass: {kind: 'blocked'|'confirm', codes, detail} | null. */
export function printCheck(err) {
  const d = err?.detail;
  if (err?.status !== 409 || !d || typeof d !== 'object') return null;
  if (d.code === 'strategy_print_conditions_changed') {
    return { kind: 'blocked', codes: (d.differences || []).map((x) => x.code), detail: d };
  }
  if (d.code === 'strategy_print_warnings') {
    return { kind: 'confirm', codes: (d.warnings || []).map((x) => x.code), detail: d };
  }
  return null;
}

/** The newest later chart of a run (the one the next steps are about). */
export function lastLater(run) {
  const later = run?.later || [];
  return later.length ? later[later.length - 1] : null;
}
