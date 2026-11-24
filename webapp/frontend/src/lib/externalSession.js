/**
 * Pure logic of the external measurement interface (chartread-fgK sessions). Everything here
 * is derived from the backend snapshot (GET/SSE /api/charts/{id}/external-measure): the
 * interface never computes progress from the buttons it pressed, and only offers what the
 * snapshot's `actions` allow. There is no action that starts a reading: the instrument's
 * own button does.
 *
 * Snapshot shape (backend): {seq, state, state_seq, running, actions, prompt, pass,
 * acquired_not_saved, error, session: {passes_saved, passes_total, patches_saved,
 * useful_patches, passes, pass_labels, durability, durability_warning, suspended,
 * last_stop, identity, …}, context: {model, filter_options, engine, presence, chart},
 * notices, last_session}.
 */

export const STEPS = ['prepare', 'calibrate', 'read', 'publish'];

const CALIBRATE_STATES = new Set(['starting', 'calibration', 'calibrating', 'calibrated']);
const READ_STATES = new Set(['ready', 'saving', 'saved', 'warning', 'finishing', 'stopping']);

/** Which of the four steps a snapshot belongs to. */
export function stepOf(snap) {
  if (!snap) return 'prepare';
  const s = snap.state;
  if (s === 'complete' || s === 'published') return 'publish';
  if (snap.running) {
    if (CALIBRATE_STATES.has(s)) return 'calibrate';
    if (READ_STATES.has(s)) return 'read';
    return 'calibrate';
  }
  if (s === 'idle' && (snap.session?.passes_saved || 0) > 0) return 'read';   // a session to resume
  return 'prepare';
}

/** Body of a command: the state and seq it was decided on. */
export function commandBody(snap, extra = {}) {
  return { expect_state: snap?.state ?? null, seq: snap?.state_seq ?? null, ...extra };
}

/** Keep the most recent snapshot (the backend's seq only grows). */
export function newer(current, incoming) {
  if (!incoming) return current;
  if (!current) return incoming;
  return (incoming.seq ?? 0) >= (current.seq ?? 0) ? incoming : current;
}

export const isStale = (err) => err?.status === 409 && err?.detail?.code === 'stale_command';

/** Is this action allowed now? (only the backend's word counts) */
export const allowed = (snap, action) => !!snap && Array.isArray(snap.actions) && snap.actions.includes(action);

/** Counts actually saved (validated by the backend), never inferred. */
export function progress(snap) {
  const s = snap?.session || snap?.last_session || null;
  const passesTotal = s?.passes_total ?? snap?.context?.chart?.n_strips ?? 0;
  const patchesTotal = s?.useful_patches ?? snap?.context?.chart?.patch_count ?? 0;
  const passesSaved = s?.passes_saved ?? 0;
  const patchesSaved = s?.patches_saved ?? 0;
  return {
    passesSaved, passesTotal, patchesSaved, patchesTotal,
    pct: patchesTotal ? Math.round((1000 * patchesSaved) / patchesTotal) / 10 : 0,
    complete: !!s?.complete,
  };
}

/** Durability of the last checkpoint: full | reduced | not_durable | recovered | correction |
 *  refused (a refusal leaves the previous file and suspends) — tone for the badge. */
export function durability(snap) {
  const s = snap?.session || null;
  if (!s) return null;
  const susp = s.suspended?.reason;
  if (susp === 'checkpoint_refused') return { kind: 'refused', tone: 'danger' };
  if (susp === 'checkpoint_invalid' || susp === 'session_write_failed') return { kind: 'invalid', tone: 'danger' };
  const d = s.durability;
  if (!d) return null;
  const lasting = !!s.durability_warning;      // reduced durability seen earlier in the session
  if (d === 'not_durable') return { kind: 'not_durable', tone: 'danger', lasting };
  if (d === 'reduced') return { kind: 'reduced', tone: 'warn', lasting };
  if (d === 'recovered') return { kind: 'recovered', tone: 'info', lasting };
  if (d === 'correction') return { kind: 'correction', tone: 'info', lasting };
  return { kind: 'full', tone: 'success', lasting };
}

/** Strip map: every pass label with its status (saved | current | acquired | pending). */
export function stripMap(snap) {
  const s = snap?.session || snap?.last_session || null;
  const labels = s?.pass_labels || [];
  const saved = new Set(s?.passes || []);
  const current = snap?.running ? (snap?.prompt?.pass ?? snap?.pass ?? null) : null;
  const acquired = snap?.acquired_not_saved ?? null;
  return labels.map((label) => ({
    label,
    status: acquired === label && !saved.has(label) ? 'acquired'
      : current === label ? 'current' : saved.has(label) ? 'saved' : 'pending',
    saved: saved.has(label),
  }));
}

// Problems shown as a banner. Every message says how many strips stay saved.
const RUN_ERRORS = ['instrument_lost', 'output_flood', 'engine_spinning', 'engine_timeout', 'counts_diverge',
  'engine_not_checkpointing', 'instrument_not_spectral', 'instrument_unit_changed', 'start_failed', 'fatal',
  'driver_failure'];
const SUSPENSIONS = ['checkpoint_refused', 'checkpoint_not_durable', 'checkpoint_invalid', 'session_write_failed',
  'instrument_unit_changed'];

/** The problem to show, if any: {code, tone, passes, patches}. */
export function problem(snap) {
  if (!snap) return null;
  const s = snap.session || null;
  const p = progress(snap);
  const out = (code, tone = 'danger') => ({ code, tone, passes: p.passesSaved, patches: p.patchesSaved });
  if (snap.running && snap.error?.kind) {
    const k = snap.error.kind;
    return out(RUN_ERRORS.includes(k) ? k : 'engine_error');
  }
  const susp = s?.suspended?.reason;
  if (susp) return out(SUSPENSIONS.includes(susp) ? susp : 'suspended');
  if (!snap.running && s) {
    // The last run first: a run closed by the start-up reconciliation ("interrupted") wrote no
    // last_stop, which then belongs to an earlier run.
    if (s.last_run_end === 'interrupted' || s.last_stop?.reason === 'backend_stop') return out('interrupted', 'warn');
    const stop = s.last_stop;
    const err = stop?.error?.kind;
    if (err) return out(RUN_ERRORS.includes(err) ? err : 'engine_error');
    if (stop?.reason === 'exited') return out('exited', 'warn');
  }
  return null;
}

/** The key prompt to answer, as the interface presents it. */
export function promptView(snap) {
  const p = snap?.prompt;
  if (!p) return null;
  if (p.kind === 'calibration_prompt' || p.kind === 'calibration_retry') {
    return { type: 'calibration', calibration: p.calibration || 'other', instruction: p.instruction || '',
             retry: p.kind === 'calibration_retry' };
  }
  if (p.kind === 'ready') return { type: 'ready', pass: p.pass, readBefore: !!p.read_before, allRead: !!p.all_read };
  return { type: 'warning', kind: p.kind, data: p };
}

/** Condition choices for a model with a selectable filter (i1Pro 3 Plus): none by default. */
export function conditionChoices(snap) {
  return (snap?.context?.filter_options || []).map((o) => ({ option: o.option, condition: o.condition }));
}

/** Can the session be created? (engine there; a condition chosen when the model needs one) */
export function canCreate(snap, filterOption) {
  if (!allowed(snap, 'create')) return false;
  if (!snap?.context?.engine?.ok) return false;
  return !snap.context.filter_required || !!filterOption;
}

/** Final checks before publishing (all from the backend's validated state). */
export function publishChecks(snap) {
  const s = snap?.session || null;
  const id = s?.identity || null;
  const p = progress(snap);
  return [
    { key: 'complete', ok: p.complete && p.passesSaved === p.passesTotal, passes: p.passesSaved, total: p.passesTotal },
    { key: 'spectral', ok: !!id && (id.n_bands || 0) >= 2, n: id?.n_bands || 0,
      start: id?.bands?.[0] ?? null, end: id?.bands?.[1] ?? null },
    { key: 'instrument', ok: !!id?.target_instrument, value: id?.target_instrument || null },
    { key: 'calstd', ok: true, value: id?.devcalstd || null },
    { key: 'provenance', ok: s?.engine_origin === 'approved', origin: s?.engine_origin || null },
  ];
}

// ─── instrument presence while the wizard is open ───────────────────────────
// A change of presence alone publishes no SSE event (the backend lists the USB devices, passively,
// when it builds a snapshot: context()). So while the wizard is open on a chart without a live
// run, it reads the snapshot again every PRESENCE_POLL_MS. During a run the engine itself watches
// the instrument (instrument_lost); after a run, the watch resumes.

export const PRESENCE_POLL_MS = 2000;
// What the backend reports (checking: it has no verdict yet, a USB listing is running).
const PRESENCE = new Set(['checking', 'present', 'absent', 'other', 'several', 'error']);

/** Is the presence worth watching now? (no live run, and a session to create or to start) */
export function watchesPresence(snap) {
  return !!snap && !snap.running && (allowed(snap, 'create') || allowed(snap, 'start'));
}

/**
 * Presence as the wizard shows it: checking | present | absent | other | several | error |
 * unreachable. `check` is the watch's last answer since it started ({ok}), or null while the
 * first one is awaited: until then the presence carried by an older snapshot is not shown as
 * current. A read that failed is "unreachable" (freeglaz did not answer: nothing is known of
 * the instrument, which is not an absence); "error" is the backend's own verdict that the USB
 * devices could not be listed, or a status the interface does not know. Neither is a presence.
 */
export function presenceState(snap, check) {
  if (!snap || !check) return { status: 'checking', familyOnly: false };
  if (!check.ok) return { status: 'unreachable', familyOnly: false };
  const p = snap.context?.presence;
  if (!p || !PRESENCE.has(p.status)) return { status: 'error', familyOnly: false };
  return { status: p.status, familyOnly: p.status === 'present' && !!p.family_only };
}

/**
 * Why "Start" is not offered, or null: only a confirmed presence of the chart's instrument
 * offers it. chartread-fgK opens the first instrument ArgyllCMS finds, so the backend refuses
 * every other case (absent, another instrument, several, detection error); the button waits
 * and comes back by itself once the instrument is detected. Checking: an answer is on its way.
 */
export function startBlock(presence) {
  return presence.status === 'present' ? null : presence.status;
}

/**
 * The presence watch: check() now, then again PRESENCE_POLL_MS after each answer. Never two
 * checks at once (the next one is only scheduled once the previous answered). onResult(ok)
 * after each answer; stop() ends it, and an answer arriving after stop() is dropped.
 * `check` resolves to something truthy when the snapshot was read.
 */
export function createPresenceWatch({ check, onResult, intervalMs = PRESENCE_POLL_MS, timers = globalThis }) {
  let stopped = false;
  let timer = null;
  const tick = async () => {
    timer = null;
    let ok;
    try { ok = !!(await check()); } catch { ok = false; }
    if (stopped) return;
    onResult(ok);
    timer = timers.setTimeout(tick, intervalMs);
  };
  return {
    start() { if (!stopped) tick(); },
    stop() {
      stopped = true;
      if (timer !== null) timers.clearTimeout(timer);
      timer = null;
    },
  };
}

/**
 * Snapshot reads (GET) applied in the order they were asked: the answer to an earlier read that
 * arrives after a later one was applied is dropped. (Snapshots of an unchanged state share their
 * seq, so newer() alone would let a late answer put back an older presence.) shared() joins the
 * read in flight instead of starting a second one.
 */
export function createSnapshotReader(fetchSnap, onSnap) {
  let issued = 0;
  let applied = 0;
  let pending = null;
  const read = () => {
    const id = ++issued;
    // `pending` is cleared before the caller hears the answer: a shared() right after it starts
    // a new read instead of joining the finished one (a failure would otherwise be handed back).
    const p = Promise.resolve().then(fetchSnap).then(
      (s) => {
        if (pending === p) pending = null;
        if (id > applied) { applied = id; onSnap(s); }
        return s;
      },
      (e) => {
        if (pending === p) pending = null;
        throw e;
      });
    pending = p;
    return p;
  };
  return { read, shared: () => pending || read() };
}

/** Readable recent events (newest last), from the snapshot's notices. */
export function recentEvents(snap, limit = 5) {
  const keep = new Set(['checkpoint', 'suspended', 'error', 'run_ended', 'corrected', 'published',
    'calibration_prompt', 'misread', 'wrong_strip', 'unexpected', 'needcal', 'sensor_position']);
  return (snap?.notices || []).filter((n) => keep.has(n.kind)).slice(-limit);
}
