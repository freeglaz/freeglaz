import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import {
  X, Grid3x3, AlertTriangle, Info, CheckCircle2, Upload, Save, HelpCircle,
  Printer, ScanLine, FlaskConical, ShieldCheck, Trash2, Archive, GitCompare,
} from 'lucide-react';
import * as api from '../../api/client.js';
import Segmented from '../ui/Segmented.jsx';
import ConfirmModal from '../ui/ConfirmModal.jsx';
import ProfileBuildPanel from './ProfileBuildPanel.jsx';
import { geLabel } from '../../lib/geLabel.js';
import { safeLocalGet } from '../../lib/safeLocalStorage.js';
import { useLoadedPaper } from '../../hooks/useLoadedPaper.js';
import { usePrinterFamily } from '../../hooks/usePrinterFamily.js';
import ProfcheckReport from '../Profiles/ProfcheckReport.jsx';
import AllPatchesModal from '../Profiles/AllPatchesModal.jsx';
import ScanDeltaCard from './ScanDeltaCard.jsx';   // extracted (shared wizard + Measurements tab)
import ChartCard from './ChartCard.jsx';           // extracted chart-card (shared)
import ScanKeepList from './ScanKeepList.jsx';     // keep/exclude toggle (shared)
import MeasurementsModal from './MeasurementsModal.jsx';   // single-scan view + intrinsic QC (shared with Measurements)
import MeasureQcSummary from './MeasureQcSummary.jsx';     // QC summary (shared with MeasurementsModal)
import { qcPanelState } from '../../lib/measureQc.js';
import BuildConfirmModal from './BuildConfirmModal.jsx';
import { useBuildPreview, buildWithPreview } from '../../hooks/useBuildPreview.js';
import { buildRequestBody, chartStateStamp, classifyBuildError, summarizePreview } from '../../lib/buildPreview.js';
import { errorText } from '../../lib/errorText.js';
import {
  METHOD_EXTERNAL, METHOD_INTEGRATED, chartMeasurement, estimateRequest, externalFormats, externalMediaCheck,
  externalPrintBlock, instrumentLabel, layoutGeometry, layoutOptionsFor,
  methodAvailability, printtargSummary, resolveInstrument, resolveLayoutChoice, resolveMethod,
  validationIdentityNotice,
} from '../../lib/measurementMethod.js';
import { openInLibrary } from '../../lib/libraryLink.js';
import MeasurementBadge from './MeasurementBadge.jsx';
import ExternalMeasurePanel from '../ExternalMeasure/ExternalMeasurePanel.jsx';
import TwoPassCompose from './TwoPassCompose.jsx';
import StrategyRunPanel from './StrategyRunPanel.jsx';
import StrategyBadge from './StrategyBadge.jsx';
import OnePassCompose from './OnePassCompose.jsx';
import { usePrinttargEstimate } from '../../hooks/usePrinttargEstimate.js';
import {
  STRATEGY_TWO_PASS, activeRuns, currentPatches, formFromParams, offersQuasiRandomFallback,
  paperTotal, paramsFromForm, printCheck, stepAction, strategyCreateRequest,
  oneFormFromParams, oneParamsFromForm, plannedColprofLine,
} from '../../lib/profilingStrategy.js';

/**
 * ArgyllProfileWizard — custom profiling (Argyll) as an ACTION ON THE SLOT, mirroring
 * the HP wizard. Driven by the slot's {paper, ge, mode} — NO paper/slot choice.
 *
 * mode='print' : compose (targen/.ti1) → "Create and print" (inseparable,
 *   confirmation before the physical act) → "printed" screen → bridge "move to scan".
 * mode='scan'  : filtered library (this paper, printed) → choose → scan
 *   (native SOL measurement) → profile (colprof). GUIDED PATH throughout.
 *
 * Measurement is OFFLINE (Path A: print → dry → reload → scan) — so there
 * is NO "auto" mode on the Argyll side (unlike HP).
 *
 * Measurement method (chosen at composition, kept for the whole creation): the built-in HP
 * spectrophotometer (honeycomb chart, SOL scan, Z9 only) or an external spectrophotometer
 * (printtarg strips, any printer; instruments listed by GET /api/charts/printtarg-options).
 * An external chart is printed by the same pipeline and never reaches the SOL scan: after
 * printing, and whenever one is picked for measurement, the wizard shows that the external
 * measurement is not driven by freeglaz yet.
 *
 * Plumbing (resident tag, slot, FULLPAGE, raw values) HIDDEN in prod;
 * revealed behind the DEV FLAG (localStorage freeglaz_dev='1') for diagnostics.
 */

const DEV = safeLocalGet('freeglaz_dev') === '1';

function _extractF(line) {
  const m = (line || '').match(/-f\s*(\d+)/);
  return m ? parseInt(m[1], 10) : null;
}
// SHORT form ('ON'/'OFF') for the wizard's inline contexts ("GE {…}", "GE={…}");
// derives from the single presentation helper (source of truth for the FULLPAGE→ON mapping).
function _geLabel(g) { return geLabel(g) === 'GE ON' ? 'ON' : 'OFF'; }

// Default chart-format key from the LOADED media (status.paper) + the available
// formats (getChartFormats → each carries physical `width_mm` + `is_roll`).
// Roll: pick the WIDEST roll format that fits the loaded roll width (chart ≤ roll →
// 17"/24"/44" all matched by real width, no hardcoded roll24); if the loaded roll is
// narrower than every known roll format, fall back to the narrowest roll format; if
// the roll width is unknown, a roll format (not a fixed one). Sheet/none → sheet default.
function _defaultFormatKey(loadedPaper, formats) {
  if (!formats?.length) return null;
  const rolls = formats.filter((f) => f.is_roll);
  if (loadedPaper?.kind === 'roll' && rolls.length) {
    const w = loadedPaper.width_mm;
    if (typeof w === 'number' && w > 0) {
      const fits = rolls.filter((f) => f.width_mm <= w + 15).sort((a, b) => b.width_mm - a.width_mm);
      if (fits.length) return fits[0].key;                                   // widest that fits
      return [...rolls].sort((a, b) => a.width_mm - b.width_mm)[0].key;      // narrower than any → smallest
    }
    return rolls[0].key;                                                     // width unknown → a roll (not hardcoded)
  }
  return formats.some((f) => f.key === 'a3') ? 'a3'
    : (formats.find((f) => !f.is_roll)?.key || formats[0].key);             // sheet default
}

function NSelect({ value, onChange, options, disabled = false }) {
  return (
    <select value={value} disabled={disabled}
            onChange={(e) => onChange(e.target.value)}
            className="text-xs2 bg-sunken text-text-strong rounded px-2 py-1 max-w-[260px] disabled:opacity-40 cursor-pointer">
      {(options || []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

export default function ArgyllProfileWizard({ open, onClose, paper, ge, mode: modeProp = 'print', onDone, profile, initialChart = null,
                                              strategyContinuation = null, printChartId = null }) {
  const { t } = useTranslation();
  // A scan-mode wizard switches to composition for pass 2 of a two-pass profiling ("Prepare
  // pass 2" on a scanned first pass): the effective mode, reset at each opening.
  const [modeOverride, setModeOverride] = useState(null);
  const mode = modeOverride || modeProp;
  const loadedPaper = useLoadedPaper();   // live status.paper → default chart format (roll if a roll is loaded)
  // Built-in spectro scan: Z9 only (the Z9 Pro firmware crashes on it). The backend
  // refuses it anyway (409 scan_unsupported_model); here we only hide the action
  // once the family is KNOWN — unknown (null) leaves the decision to the backend.
  const printerFamily = usePrinterFamily();
  const scanUnsupported = printerFamily != null && printerFamily !== 'z9';
  const mediaid = paper?.mediaid || paper?.media_id || '';
  const paperName = paper?.name || mediaid;
  const geStr = ge === true || ge === 'FULLPAGE' ? 'FULLPAGE' : 'OFF';
  // mode='validate' (profcheck b): compose an INDEPENDENT chart → print → scan
  // → profcheck terminal (forward A2B) instead of colprof. `profile` = profile to validate.
  const isValidate = mode === 'validate';

  // unified phases: compose (print) | select/scanready/scanned/profiled (scan)
  // initialChart: DIRECT opening on a chart (from Measurements) →
  // skips `select`, goes straight to `scanready`, at any stage.
  const [phase, setPhase] = useState(
    (mode === 'scan' && initialChart) ? 'scanready'
      : (mode === 'scan' || isValidate) ? 'select' : 'compose');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  // ─── compose (print) ───
  const [formats, setFormats] = useState([]);
  const [mediaKey, setMediaKey] = useState('a3');
  // guided = the one-pass guided form (same layout as two passes); targen = free line; ti1 = a file
  const [srcMode, setSrcMode] = useState(mode === 'print' ? 'guided' : 'targen');
  const [flags, setFlags] = useState('-G -f 200');
  const [ti1Text, setTi1Text] = useState('');
  const [ti1Name, setTi1Name] = useState('');
  const [preconds, setPreconds] = useState([]);
  // KISS: default = 'zero' (Start from scratch, NO preconditioning) — no silent
  // inheritance of the resident as -c. The user picks 'affiner' explicitly for a
  // passe-2 refinement (and sees which profile is used, below).
  const [cChoice, setCChoice] = useState('zero');           // zero | affiner | custom
  const [cCustom, setCCustom] = useState('');               // id path:… if custom
  const [cFreeglaz, setCFreeglaz] = useState('');           // id path:… of a profile built by freeglaz
  const [presets, setPresets] = useState([]);
  const [presetKey, setPresetKey] = useState('');
  const [help, setHelp] = useState(null);
  const [showHelp, setShowHelp] = useState(false);
  const [confirmPrint, setConfirmPrint] = useState(false);
  const [result, setResult] = useState(null);               // created chart (preview before printing)
  // Measurement method: explicit user choice (null = the family's default) and instrument.
  // Instruments, presets and media come from the backend (no whitelist kept here).
  const [ptOptions, setPtOptions] = useState(null);          // printtarg options | null loading | false error
  const [methodChoice, setMethodChoice] = useState(null);    // integrated | external | null
  const [instrumentChoice, setInstrumentChoice] = useState(null);
  // printtarg -a / -h chosen for an external chart (made valid for the instrument below)
  const [layoutChoice, setLayoutChoice] = useState({ patchScale: null, highDensity: false });
  const [estimateRes, setEstimateRes] = useState(null);   // last paper estimate answer {key, …}
  // ─── profiling strategy (print mode): one pass = the wizard as before; two passes = the
  // two-pass adaptive profiling (lib/profilingStrategy.js; lines and checks are the server's)
  const [strategyMode, setStrategyMode] = useState(strategyContinuation ? 'two' : 'one');   // one | two
  const [stratDefs, setStratDefs] = useState(null);          // GET /api/profiling-strategies
  const [twoForm, setTwoForm] = useState(() => formFromParams(null));
  const [oneForm, setOneForm] = useState(() => oneFormFromParams(null));   // one pass, guided form
  const [oneComposedRes, setOneComposedRes] = useState({ key: null, data: null, error: null });
  const [continuation, setContinuation] = useState(strategyContinuation);   // {run, buildId} → pass 2
  const [stratRunRes, setStratRunRes] = useState({ run: null, data: null });   // the run continued (pass 2)
  const [composed, setComposed] = useState(null);            // lines the server will run
  const [composeErr, setComposeErr] = useState(null);
  const [runsRes, setRunsRes] = useState({ key: null, runs: [] });   // resume card
  const [fallbackOffer, setFallbackOffer] = useState(false); // targen failed: -Q offered, never applied alone
  const [printConfirm, setPrintConfirm] = useState(null);    // later pass: warnings to confirm before printing
  const [presetSources, setPresetSources] = useState(null);  // final pass: variant → build panel sources
  const [finalLineFor, setFinalLineFor] = useState(null);    // chart whose final line was prefilled

  // ─── scan ───
  const [charts, setCharts] = useState(null);
  const [selected, setSelected] = useState(mode === 'scan' && initialChart ? initialChart : null);  // chart object/{chart_id}
  const [scanInfo, setScanInfo] = useState(null);
  const [profInfo, setProfInfo] = useState(null);
  const [printInfo, setPrintInfo] = useState(null);
  // ─── colprof (profile step — symmetric to the targen line) ───
  const [colprofFlags, setColprofFlags] = useState('');     // pre-filled at the settled default
  const [colprofPresets, setColprofPresets] = useState([]);
  const [colprofHelp, setColprofHelp] = useState(null);
  const [showColprofHelp, setShowColprofHelp] = useState(false);
  const [colprofPresetKey, setColprofPresetKey] = useState('');
  const [chartAction, setChartAction] = useState(null);        // { type:'delete'|'lighten', chart }
  const [selectNonce, setSelectNonce] = useState(0);           // forces the list refetch
  // Multi-source sources selected in the panel (current chart implicit).
  const [extraSources, setExtraSources] = useState({ extra_chart_ids: [], source_profiles: [] });
  const [conflict, setConflict] = useState(null);              // collision (409) → 3-choice pop-up
  const [nameNotice, setNameNotice] = useState(null);          // ASCII/length only (BELOW the field)
  // ─── NON-BLOCKING roll scan (phase 2): poll the background job + UX guards ───
  const [scanProg, setScanProg] = useState(null);     // {state, phase, percent} of the background job
  const [cooldownSec, setCooldownSec] = useState(0);  // SOL cooldown countdown (>=30 s, mirrors 429)
  // Scan count = DURABLE DISK TRUTH (status.n_scans = measurements/*.ti3), not the
  // session: drives the "chart moved?" guard + badge + button label → works even with a session
  // ended/absent (the ti3 stays on disk). This closes the resumption hole (abandoned session).
  const [nScans, setNScans] = useState(0);
  const [profileBase, setProfileBase] = useState('average');  // average | last (default average)
  const [chartMovedAsk, setChartMovedAsk] = useState(false);  // "chart moved?" modal (blocking)
  const [sessionBlockedBy, setSessionBlockedBy] = useState(null);  // chart_id of an active session on ANOTHER chart (409) → offer to finish it
  const [clcWarn, setClcWarn] = useState(null);               // clc status to warn about (never|stale|pending) | null
  const scanPollRef = useRef(null);
  const cooldownRef = useRef(null);
  const formatDefaultedRef = useRef(false);
  const profileInFlight = useRef(false);   // format default applied once per open (see effect)
  const [scanDelta, setScanDelta] = useState(null);   // mini inter-scan ΔE report (scanned screen)
  const [chartDetail, setChartDetail] = useState(null);  // scans+kept (keep/exclude toggle)
  const [concordanceDelta, setConcordanceDelta] = useState(null);  // report opened from the chart view
  const [deltaAllOpen, setDeltaAllOpen] = useState(false);
  // Intrinsic measurement QC of the scan JUST acquired (advisory, separate from the acquisition:
  // its failure never turns a successful scan into a failed one).
  const [qcScan, setQcScan] = useState({ key: null, loading: false, report: null, error: null });
  const [measuresOpen, setMeasuresOpen] = useState(false);
  // Phase 4D — the same build contract as Measurements: preview (apply_all) → confirmation → build
  // with the preview's fingerprint. The preview follows settings, sources, roles and exclusions.
  const [previewNonce, setPreviewNonce] = useState(0);
  const [confirmKey, setConfirmKey] = useState(null);
  const nKeptBuild = (chartDetail?.scans || []).filter((s) => s.kept).length;
  const buildBody = useMemo(() => buildRequestBody({ nKept: nKeptBuild, profileBase, colprofFlags, extraSources }),
                            [nKeptBuild, profileBase, colprofFlags, extraSources]);
  const stateStamp = useMemo(() => chartStateStamp(chartDetail), [chartDetail]);
  const preview = useBuildPreview(selected?.chart_id, buildBody, {
    enabled: phase === 'scanned' && !isValidate && !!chartDetail && chartDetail?.identity?.strategy?.role !== 'preconditioning',
    stateStamp, nonce: previewNonce });

  // reset on opening
  useEffect(() => {
    if (!open) return;
    // DIRECT opening on a chart ("Open in the scan flow"
    // from Measurements) → we skip the filtered list, straight to scanready, at any
    // stage (bypasses the list's printed:true filter, without touching it).
    // ADDITIVE path: without initialChart, behavior UNCHANGED (select + list).
    const directScan = modeProp === 'scan' && !!initialChart;
    // DIRECT opening on an existing chart to print it (again): its summary loads, then the
    // creation's own preview → confirmation → print (Measurements, or a pass never printed).
    const directPrint = modeProp === 'print' && !!printChartId;
    setPhase(directScan ? 'scanready' : directPrint ? 'loading' : (modeProp === 'scan' || isValidate) ? 'select' : 'compose');
    setError(null); setBusy(false); setSelected(directScan ? initialChart : null); setResult(null);
    setScanInfo(null); setProfInfo(null); setPrintInfo(null); setConfirmPrint(false);
    setScanProg(null); setCooldownSec(0); setNScans(0);
    setQcScan({ key: null, loading: false, report: null, error: null }); setMeasuresOpen(false);
    setProfileBase('average'); setChartMovedAsk(false);
    setCChoice('zero');                 // reset precond default (Start from scratch) each open
    setStrategyMode(strategyContinuation ? 'two' : 'one'); setContinuation(strategyContinuation);
    setComposed(null); setComposeErr(null); setFallbackOffer(false); setPrintConfirm(null);
    setPresetSources(null); setModeOverride(null);
    setMethodChoice(null); setInstrumentChoice(null);   // family default at each open, then the user's
    formatDefaultedRef.current = false; // re-arm the format default (applied once formats load)
    setClcWarn(null);
    setConcordanceDelta(null); setDeltaAllOpen(false);
    if (scanPollRef.current) clearInterval(scanPollRef.current);
    if (cooldownRef.current) clearInterval(cooldownRef.current);
    // Validation: INDEPENDENT distribution by default = quasi-random device (-q),
    // != the deterministic OFPS of creation + moderate count (we verify, we don't build).
    // Editable. The non-overlap vs creation is cross-checked in the report.
    if (modeProp === 'validate') setFlags('-q -f 64');
    // one pass: the guided form first (a validation chart keeps its own free line)
    setSrcMode(modeProp === 'print' ? 'guided' : 'targen');
    // eslint-disable-next-line react-hooks/exhaustive-deps -- isValidate follows modeProp
  }, [open, modeProp, initialChart, strategyContinuation, printChartId]);

  // Existing chart to print (see the reset above): the same result object as a creation.
  useEffect(() => {
    if (!open || modeProp !== 'print' || !printChartId) return undefined;
    let cancelled = false;
    api.getChartPrintSummary(printChartId)
      .then((summary) => { if (!cancelled) { setResult(summary); setPhase('preview'); } })
      .catch((e) => { if (!cancelled) setError(errorText(e, t)); });
    return () => { cancelled = true; };
  }, [open, modeProp, printChartId, t]);

  // Default chart format = the loaded media's REAL width (roll 17"→44" derived from
  // status.paper.width_mm, matched against the roll formats' physical width), never a
  // hardcoded roll24. Applied ONCE per open (guard), as soon as formats are loaded.
  // Sheet (or no roll) → sheet default. Always user-editable afterwards.
  useEffect(() => {
    if (!open || formatDefaultedRef.current || !formats.length) return;
    formatDefaultedRef.current = true;
    const key = _defaultFormatKey(loadedPaper, formats);
    if (key) setMediaKey(key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, formats, loadedPaper?.kind, loadedPaper?.width_mm]);

  // Cleanup of timers on unmount (never an orphan poll).
  useEffect(() => () => {
    if (scanPollRef.current) clearInterval(scanPollRef.current);
    if (cooldownRef.current) clearInterval(cooldownRef.current);
  }, []);

  // compose data (print mode: composes a chart)
  useEffect(() => {
    if (!open || mode === 'scan') return;
    api.getChartFormats().then((d) => setFormats(d.formats || [])).catch(() => {});
    api.getTargenPresets().then((d) => setPresets(d.presets || [])).catch(() => {});
    api.getPrinttargOptions().then(setPtOptions).catch(() => setPtOptions(false));
  }, [open, mode]);

  // Profiling strategies (print mode only; a validation chart is never a strategy pass).
  useEffect(() => {
    if (!open || mode !== 'print') return;
    api.getProfilingStrategies().then((d) => {
      setStratDefs(d);
      const def = (d.strategies || []).find((x) => x.id === STRATEGY_TWO_PASS);
      if (def && !strategyContinuation) setTwoForm(formFromParams(def.default_params));
      setOneForm(oneFormFromParams(d.one_pass?.default_params));
    }).catch(() => setStratDefs(false));
  }, [open, mode, strategyContinuation]);

  // Pass 2: the run continued (its frozen parameters, its first chart's instrument).
  const stratRun = continuation?.run && stratRunRes.run === continuation.run ? stratRunRes.data : null;
  useEffect(() => {
    if (!open || !continuation?.run) return undefined;
    let alive = true;
    api.getStrategyRun(continuation.run).then((r) => {
      if (!alive) return;
      setStratRunRes({ run: continuation.run, data: r });
      const params = r.first?.params || r.later?.[r.later.length - 1]?.params;
      if (params) setTwoForm(formFromParams(params));
      // the instrument is inherited and locked: the method follows the first chart's layout
      if (r.first) {
        setMethodChoice(r.first.layout === 'printtarg' ? METHOD_EXTERNAL : METHOD_INTEGRATED);
        if (r.first.instrument) setInstrumentChoice(r.first.instrument);
      }
    }).catch((e) => { if (alive) setError(errorText(e, t)); });
    return () => { alive = false; };
  }, [open, continuation?.run, t]);

  // Lines the server WILL run for the two-pass form (debounced; never rebuilt client-side).
  const twoPassActive = strategyMode === 'two' && mode === 'print';
  const passId = continuation ? 'pass2' : 'pass1';
  // One pass: the guided form (same layout and controls as two passes) unless this build has no
  // [one_pass] values; then the free line, as before. A validation chart keeps its free line.
  const guidedAvailable = mode === 'print' && stratDefs !== false && (stratDefs === null || !!stratDefs.one_pass);
  const effSrcMode = srcMode === 'guided' && !guidedAvailable ? 'targen' : srcMode;
  const guidedActive = mode === 'print' && !twoPassActive && effSrcMode === 'guided';
  const composeKey = twoPassActive && phase === 'compose'
    ? JSON.stringify({ params: paramsFromForm(twoForm, passId), run: continuation?.run || null }) : null;
  useEffect(() => {
    if (!composeKey) return undefined;
    let alive = true;
    const timer = setTimeout(() => {
      const { params, run } = JSON.parse(composeKey);
      api.composeStrategy({ strategy: STRATEGY_TWO_PASS, params, run })
        .then((d) => { if (alive) { setComposed(d.passes || null); setComposeErr(null); } })
        .catch((e) => { if (alive) { setComposed(null); setComposeErr({ code: e?.detail?.code || null, message: errorText(e, t) }); } });
    }, 300);
    return () => { alive = false; clearTimeout(timer); };
  }, [composeKey, t]);

  // Resume card: runs of this paper still waiting for something (derived from the charts).
  const runsKey = open && mode === 'print' && !continuation && phase === 'compose' && mediaid ? `${mediaid}|${geStr}` : null;
  const runsInProgress = runsKey && runsRes.key === runsKey ? runsRes.runs : [];
  useEffect(() => {
    if (!runsKey) return undefined;
    let alive = true;
    api.getStrategyRuns({ paper: mediaid, ge: geStr })
      .then((d) => { if (alive) setRunsRes({ key: runsKey, runs: activeRuns(d.runs) }); })
      .catch(() => { if (alive) setRunsRes({ key: runsKey, runs: [] }); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mediaid/geStr are in runsKey
  }, [runsKey]);

  // colprof: presets + opinionated default line (pre-filled, overridable). Loaded
  // on opening (both modes profile in the end; print chains into the scan).
  useEffect(() => {
    if (!open) return;
    api.getChartColprofPresets()
      .then((d) => { setColprofPresets(d.presets || []); setColprofFlags(d.default_flags || '-v -qh'); })
      .catch(() => setColprofFlags('-v -qh'));
  }, [open]);

  // -c menu (live residents + customs) for this paper/GE
  useEffect(() => {
    if (!open || mode === 'scan' || !mediaid) return;
    api.getPreconditionProfiles({ paper: mediaid, printGe: geStr })
      .then((d) => {
        const list = d.profiles || [];
        setPreconds(list);
        const hasResident = list.some((p) => p.id === `resident:${geStr}`);
        setCChoice(hasResident ? 'affiner' : 'zero');
        const firstCustom = list.find((p) => p.kind === 'custom');
        setCCustom(firstCustom ? firstCustom.id : '');
        const firstBuilt = list.find((p) => p.kind === 'freeglaz');   // most recent first (server)
        setCFreeglaz(firstBuilt ? firstBuilt.id : '');
      })
      .catch(() => setPreconds([]));
  }, [open, mode, mediaid, geStr]);

  // filtered library (scan mode)
  useEffect(() => {
    if (!open || phase !== 'select') return;
    if (mode !== 'scan' && !isValidate) return;
    setCharts(null);
    // scan: PRINTED charts (to scan). validate: validation charts already
    // SCANNED (to validate directly — resume WITHOUT reprinting).
    const q = isValidate
      ? { paper: mediaid, scanned: true, purpose: 'validation' }
      : { paper: mediaid, printed: true };
    api.getCharts(q)
      .then((d) => setCharts(d.charts || []))
      .catch((e) => { setError(e.message); setCharts([]); });
  }, [open, mode, isValidate, phase, mediaid, selectNonce]);

  // Entering "ready to scan": detects scans already on DISK for THIS chart (multi-scan
  // resumption) → reattaches the poll if a scan is running, arms the residual cooldown, and populates
  // nScans from the disk (→ triggers the "chart not moved" confirmation even if the
  // session was ended — the ti3 stays on disk).
  useEffect(() => {
    if (phase !== 'scanready' || !selected?.chart_id) return;
    if (chartMeasurement(selected).external) return;     // no SOL session for an external chart
    api.getScanStatus(selected.chart_id).then((st) => {
      setNScans(st.n_scans || 0);
      if ((st.cooldown_remaining_s || 0) > 0) _startCooldown(st.cooldown_remaining_s);
      if (st.job?.state === 'running' && st.job.chart_id === selected.chart_id) {
        setScanProg({ state: 'running', phase: st.job.phase, percent: st.job.percent });
        setPhase('scanning'); _pollScan(selected.chart_id);
      }
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, selected?.chart_id]);

  // "scanned" screen: detail (scans+kept) + LAZY ΔE concordance (>=2 KEPT).
  // Recomputed after keep/exclude toggle (safety net before averaging).
  const _loadScanned = useCallback(() => {
    if (!selected?.chart_id) { setChartDetail(null); setScanDelta(null); return; }
    api.getChartDetail(selected.chart_id).then((dd) => {
      setChartDetail(dd);
      const nKept = (dd.scans || []).filter((s) => s.kept).length;
      if (nKept >= 2) api.getScanDelta(selected.chart_id).then(setScanDelta).catch(() => setScanDelta(null));
      else setScanDelta(null);
    }).catch(() => {});
  }, [selected?.chart_id]);

  useEffect(() => {
    if (phase !== 'scanned' || isValidate) { setScanDelta(null); setChartDetail(null); return; }
    _loadScanned();
  }, [phase, isValidate, nScans, _loadScanned]);

  const onSetRoleWizard = (ti3, role) => {
    if (!selected?.chart_id) return;
    setError(null);
    api.setScanRole(selected.chart_id, ti3, role)
      .then(_loadScanned)
      .catch((e) => setError(e?.message || t('scan.toggle_failed')));
  };

  const availability = useMemo(() => methodAvailability(printerFamily, ptOptions), [printerFamily, ptOptions]);
  const method = resolveMethod(methodChoice, availability, printerFamily);
  const external = method === METHOD_EXTERNAL;
  const instrument = resolveInstrument(instrumentChoice, ptOptions || null);
  const extFormats = useMemo(() => externalFormats(ptOptions || null, formats, instrument),
    [ptOptions, formats, instrument]);
  const mediaCheck = externalMediaCheck(loadedPaper, ptOptions || null, formats, instrument);
  // External charts: the format matching the loaded paper among those the backend has a preset
  // for with this instrument, else its first one (the roll: nothing is chosen from an unknown
  // paper); the honeycomb choice (mediaKey) is kept untouched for a switch back.
  const effMediaKey = external ? (mediaCheck.key || extFormats[0]?.key || (ptOptions?.media || [])[0] || null)
    : mediaKey;
  const fmt = useMemo(() => formats.find((f) => f.key === effMediaKey), [formats, effMediaKey]);
  // honeycomb capacity only: the printtarg generator lays out any count on the roll
  const maxPatches = external ? null : (fmt?.max_patches ?? null);
  const fCount = twoPassActive ? currentPatches(twoForm, passId)
    : guidedActive ? (Math.round(Number(oneForm.patches)) || null)
      : (effSrcMode === 'targen' ? _extractF(flags) : null);
  const fOver = fCount != null && maxPatches != null && fCount > maxPatches;
  const layoutOpts = external ? layoutOptionsFor(ptOptions || null, instrument) : null;
  const layout = resolveLayoutChoice(layoutChoice, layoutOpts);
  // Paper estimate BEFORE generating the TIFF: the backend runs printtarg's layout probes with
  // the chart's own preset and options (debounced; only the answer to the current request shows).
  const estimateReq = external && phase === 'compose'
    ? estimateRequest({ instrument, mediaKey: effMediaKey, srcMode: (twoPassActive || guidedActive) ? 'targen' : effSrcMode, fCount, ti1Text, choice: layout })
    : { skip: 'off' };
  // two passes: the paper of the OTHER pass too (pass 2 planned with the same settings at pass 1)
  const otherEstimateReq = external && phase === 'compose' && twoPassActive && passId === 'pass1'
    ? estimateRequest({ instrument, mediaKey: effMediaKey, srcMode: 'targen', fCount: Number(twoForm.pass2Patches) || null, ti1Text: '', choice: layout })
    : { skip: 'off' };
  const otherEstimate = usePrinttargEstimate(otherEstimateReq.body || null, t);
  const estimateKey = estimateReq.body ? JSON.stringify(estimateReq.body) : null;
  useEffect(() => {
    if (!estimateKey) return undefined;
    let live = true;
    const timer = setTimeout(() => {
      setEstimateRes((e) => ({ key: estimateKey, state: 'loading', data: e?.data || null }));
      api.estimatePrinttargChart(JSON.parse(estimateKey))
        .then((d) => { if (live) setEstimateRes({ key: estimateKey, state: 'ok', data: d }); })
        .catch((e) => {
          if (live) setEstimateRes({ key: estimateKey, state: 'error', code: e?.detail?.code || null, message: errorText(e, t) });
        });
    }, 450);
    return () => { live = false; clearTimeout(timer); };
  }, [estimateKey, t]);
  const estimate = estimateReq.skip === 'needs_count' ? { state: 'needs_count' }
    : !estimateKey ? null
      : estimateRes?.key === estimateKey ? estimateRes
        : { state: 'loading', data: estimateRes?.data || null };
  const residentCurrent = preconds.find((p) => p.id === `resident:${geStr}`);
  const customs = preconds.filter((p) => p.kind === 'custom');
  const builtProfiles = preconds.filter((p) => p.kind === 'freeglaz');   // built by freeglaz, same paper + GE

  function resolveCProfile() {
    if (cChoice === 'zero') return 'none';
    if (cChoice === 'custom') return cCustom || 'none';
    if (cChoice === 'freeglaz') return cFreeglaz || 'none';
    return residentCurrent ? residentCurrent.id : 'none';   // affiner
  }

  // One pass, guided form: the lines the server WILL run (debounced; never rebuilt client-side).
  // -N / -n only apply with a -c from the menu (resident, built by freeglaz, custom).
  const onePre = resolveCProfile() !== 'none';
  const oneKey = guidedActive && phase === 'compose'
    ? JSON.stringify({ params: oneParamsFromForm(oneForm, onePre), preconditioned: onePre }) : null;
  useEffect(() => {
    if (!oneKey) return undefined;
    let alive = true;
    const timer = setTimeout(() => {
      api.composeOnePass(JSON.parse(oneKey))
        .then((d) => { if (alive) setOneComposedRes({ key: oneKey, data: d, error: null }); })
        .catch((e) => {
          if (alive) setOneComposedRes({ key: oneKey, data: null, error: { code: e?.detail?.code || null, message: errorText(e, t) } });
        });
    }, 300);
    return () => { alive = false; clearTimeout(timer); };
  }, [oneKey, t]);
  const oneComposed = oneKey && oneComposedRes.key === oneKey ? oneComposedRes.data : null;
  const oneComposeErr = oneKey && oneComposedRes.key === oneKey ? oneComposedRes.error : null;
  const cLabel = !onePre ? null
    : cChoice === 'custom' ? (customs.find((p) => p.id === cCustom)?.name || cCustom)
      : cChoice === 'freeglaz' ? (builtProfiles.find((p) => p.id === cFreeglaz)?.name || cFreeglaz)
        : (residentCurrent?.name || residentCurrent?.id || null);

  function pickFormat(k) {
    setMediaKey(k);   // cols derived from the format (step-not-fixed model) — no more user choice
  }

  async function onTi1File(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setTi1Name(file.name);
    setTi1Text(await file.text());
  }

  async function loadHelp() {
    setShowHelp((v) => !v);
    if (help == null) {
      try { const d = await api.getTargenHelp(); setHelp(d.help); }
      catch { setHelp(t('chart_create.help_unavailable')); }
    }
  }

  async function savePreset() {
    if (!presetKey.trim() || !flags.trim()) return;
    try {
      const d = await api.saveTargenPreset({ key: presetKey.trim(), flags: `-d2 ${flags}`.trim(), description: '' });
      setPresets(d.presets || []); setPresetKey('');
    } catch (e) { setError(e.message); }
  }

  // ─── Create then (after preview) print ───
  // Create + print stay a single session on the same paper; we just insert
  // a validation LOOK (preview) before the physical act. Cancelling at the preview
  // records nothing in the library (library = printed only) → no phantom chart.
  const canCreate = mediaid && !fOver && !busy && method != null
    && !(external && estimate?.state === 'error' && estimate.code === 'too_many_strips_for_sheet')
    && (!external || (instrument && effMediaKey && availability[METHOD_EXTERNAL].note !== 'printtarg_missing'))
    && (twoPassActive ? (!!fCount && !composeErr && (!continuation || !!stratRun?.first))
      : guidedActive ? (!!fCount && !oneComposeErr)
        : (effSrcMode === 'ti1' ? ti1Text.trim() : flags.trim()));

  // Two passes, pass 2: opened from "Prepare pass 2" (Measurements, the scanned screen or the
  // resume card) with the run and the exact intermediate version to precondition with.
  function startPass2({ run, buildId }) {
    if (mode !== 'print') setModeOverride('print');
    setContinuation({ run, buildId }); setStrategyMode('two');
    setResult(null); setSelected(null); setError(null); setFallbackOffer(false);
    formatDefaultedRef.current = false;
    setPhase('compose');
  }

  // A chart already created (resume card: a pass never printed): its preview, then printing.
  async function printExisting(chartId) {
    if (mode !== 'print') setModeOverride('print');
    setResult(null); setSelected(null); setError(null); setFallbackOffer(false); setPhase('loading');
    try {
      const summary = await api.getChartPrintSummary(chartId);
      setResult(summary); setPhase('preview');
    } catch (e) { setError(errorText(e, t)); }
  }

  async function doCreate(formOverride = null, oneOverride = null) {
    setBusy(true); setError(null); setFallbackOffer(false);
    if (guidedActive) {
      const cProfile = resolveCProfile();
      try {
        const body = {
          media_key: effMediaKey, paper_mediaid: mediaid, paper_name: paperName, gloss_enhancer: geStr,
          purpose: 'profiling', c_profile: cProfile,
          guided: { params: oneParamsFromForm(oneOverride || oneForm, cProfile !== 'none') },
        };
        if (external) {
          body.layout = 'printtarg'; body.instrument = instrument;
          body.patch_scale = layout.patchScale; body.high_density = layout.highDensity;
        }
        const created = await api.createChart(body);
        setResult(created); setPhase('preview');
      } catch (e) {
        setFallbackOffer(offersQuasiRandomFallback(e));
        setError(e?.detail?.code ? t(`strategy.error.${e.detail.code}`, { defaultValue: errorText(e, t) }) : errorText(e, t));
        setPhase('compose');
      } finally { setBusy(false); }
      return;
    }
    if (twoPassActive) {
      const form = formOverride || twoForm;
      try {
        const body = {
          media_key: effMediaKey, paper_mediaid: mediaid, paper_name: paperName, gloss_enhancer: geStr,
          purpose: 'profiling',
          strategy: strategyCreateRequest({ passId, form, run: continuation?.run, buildId: continuation?.buildId }),
        };
        if (external) {
          body.layout = 'printtarg'; body.instrument = instrument;
          body.patch_scale = layout.patchScale; body.high_density = layout.highDensity;
        }
        const created = await api.createChart(body);
        setResult(created); setPhase('preview');
      } catch (e) {
        setFallbackOffer(offersQuasiRandomFallback(e));
        setError(e?.detail?.code ? t(`strategy.error.${e.detail.code}`, { defaultValue: errorText(e, t) }) : errorText(e, t));
        setPhase('compose');
      } finally { setBusy(false); }
      return;
    }
    try {
      const body = {
        media_key: effMediaKey,   // cols derived on the backend (step-not-fixed model), no longer sent
        paper_mediaid: mediaid, paper_name: paperName,
        gloss_enhancer: geStr,
        // validation: NEVER -c (independence); purpose='validation' on the server
        c_profile: isValidate ? 'none' : resolveCProfile(),
        purpose: isValidate ? 'validation' : 'profiling',
      };
      if (external) {                                 // honeycomb: body unchanged
        body.layout = 'printtarg'; body.instrument = instrument;
        body.patch_scale = layout.patchScale; body.high_density = layout.highDensity;
      }
      if (effSrcMode === 'ti1') body.ti1_text = ti1Text;
      else body.targen_flags = `-d2 ${flags}`.trim();
      const created = await api.createChart(body);
      setResult(created); setPhase('preview');
    } catch (e) { setError(errorText(e, t)); setPhase('compose'); }
    finally { setBusy(false); }
  }

  async function doPrint(skipClc = false, confirmWarnings = false) {
    // CLC safety net BEFORE printing the chart: calibration acts on what gets printed.
    // Printing under a non-fresh CLC = wasting paper/ink/scan on an already-drifted chart.
    // BEST-EFFORT (if reading clc is impossible → we don't block printing). skipClc=true =
    // "print anyway" (from the modal → no setState race).
    if (!skipClc) {
      try {
        const papers = (await api.getPapers()).papers || [];
        const st = papers.find((p) => (p.mediaid || p.media_id) === mediaid)?.clc?.status;
        if (st === 'never' || st === 'stale' || st === 'pending') {
          setConfirmPrint(false); setClcWarn(st); return;
        }
      } catch { /* reading clc impossible → we don't block printing */ }
    }
    setConfirmPrint(false); setBusy(true); setError(null); setPhase('printing');
    try {
      const info = await api.printChart(result.chart_id, { quality: 'HIGH', confirm_condition_warnings: confirmWarnings });
      setSelected(result); setPrintInfo(info); setPhase('printed');
    } catch (e) {
      // later pass of a two-pass profiling: conditions checked BEFORE any paper is used
      const pc = printCheck(e);
      if (pc?.kind === 'confirm') setPrintConfirm(pc);
      else if (pc?.kind === 'blocked') {
        setError(t('strategy.print.blocked', { list: pc.codes.map((c) => t(`strategy.print.code.${c}`, { defaultValue: c })).join(' · ') }));
      } else setError(errorText(e, t));
      setPhase('preview');
    }
    finally { setBusy(false); }
  }

  // Cooldown UI = READABLE mirror of the server's HARD guard (>=30 s between SOL ops). The UI
  // does not bypass the guard, it makes it visible (scan button disabled during it).
  function _startCooldown(sec) {
    setCooldownSec(Math.ceil(sec || 0));
    if (cooldownRef.current) clearInterval(cooldownRef.current);
    cooldownRef.current = setInterval(() => {
      setCooldownSec((s) => { if (s <= 1) { clearInterval(cooldownRef.current); return 0; } return s - 1; });
    }, 1000);
  }

  // Explicit QC of the NEW scan (cached server-side → Measurements shows it without recomputing).
  function _runScanQc(chartId, ti3) {
    if (!chartId || !ti3) return;
    const key = `${chartId}/${ti3}`;
    setQcScan({ key, loading: true, report: null, error: null });
    api.getScanMeasureQC(chartId, ti3)
      .then((r) => setQcScan((cur) => (cur.key === key ? { key, loading: false, report: r.report, error: null } : cur)))
      .catch((e) => setQcScan((cur) => (cur.key === key ? { key, loading: false, report: null, error: e } : cur)));
  }

  // Poll the background job until done/error (never block the UI; NO kill on the UI side).
  function _pollScan(chartId) {
    if (scanPollRef.current) clearInterval(scanPollRef.current);
    scanPollRef.current = setInterval(async () => {
      try {
        const st = await api.getScanStatus(chartId);
        setNScans(st.n_scans || 0);
        const job = st.job || {};
        setScanProg({ state: job.state, phase: job.phase, percent: job.percent });
        if (job.state === 'done') {
          clearInterval(scanPollRef.current);
          setScanInfo({ ti3: job.ti3, n_patches: job.n_patches, bands: job.bands });
          setPhase('scanned');
          _runScanQc(chartId, job.ti3);                 // advisory, after the scan is recorded
        } else if (job.state === 'error') {
          clearInterval(scanPollRef.current);
          // EXPECTED failure path = CALM message (not anxiety-inducing red). Retry COOLDOWN-GATED.
          const cancelled = /OP_CANCELLED|registration|cancel/i.test(job.error || '');
          setError(cancelled
            ? t('scan.roll.retry_registration')
            : (job.error || t('scan.roll.scan_failed')));
          _startCooldown(st.cooldown_remaining_s || 30);
          setPhase('scanready');
        }
      } catch { /* transient — we re-poll */ }
    }, 3000);
  }

  // ─── Scan (NON-BLOCKING physical act: drives the Z9 measurement in the background) ───
  async function doScan() {
    // A scan is a MACHINE op → ALWAYS confirm chart placement first (first scan
    // included, not only resumes): recall the chart name + load instructions.
    if (!chartMovedAsk) { setChartMovedAsk(true); return; }
    setChartMovedAsk(false);
    setBusy(true); setError(null);
    setScanProg({ state: 'starting', phase: 'starting', percent: 0 });
    setPhase('scanning');
    try {
      await api.scanChart(selected.chart_id, {});   // immediate return (job launched)
      _pollScan(selected.chart_id);
    } catch (e) {
      const msg = e?.message || '';
      if (e?.status === 409 && e?.detail?.code === 'scan_unsupported_model') {
        setError(t('scan.unsupported_model'));
      } else if (e?.status === 409 && e?.detail?.code === 'session_active_other_chart') {
        // Inter-chart lock → offer a DIRECT "finish the blocking session" action
        // (modal below) instead of a dead-end text refusal.
        setSessionBlockedBy(e.detail.chart_id);
      } else if (e?.status === 429) {               // SOL cooldown (server hard guard)
        const st = await api.getScanStatus(selected.chart_id).catch(() => null);
        _startCooldown(st?.cooldown_remaining_s || 30);
        setError(t('scan.roll.cooldown_sol'));
      } else if (e?.status === 409) {               // scan already in progress
        setError(t('scan.roll.already_running'));
      } else {
        setError(msg || t('scan.roll.scan_failed'));
      }
      setPhase('scanready');
    } finally { setBusy(false); }
  }

  // Opens a chart's ΔE concordance report (>=2 scans) FROM the chart view —
  // READ-ONLY, NO scan triggered (consults the persistent ti3 via the endpoint).
  async function openConcordance(chart) {
    setError(null);
    try {
      const r = await api.getScanDelta(chart.chart_id);
      setConcordanceDelta({ ...r, _chartId: chart.chart_id });
    } catch (e) {
      setError(e?.detail?.code === 'scans_not_comparable' ? errorText(e, t)   // other instrument / condition
        : e?.status === 409 ? t('scan.roll.less_than_two_scans')
          : (e?.message || t('scan.roll.concordance_unavailable')));
    }
  }

  // ─── Profile (local, colprof) ───
  // onConflict: null (collision → pop-up) | 'replace' | 'keep_both' (replay from the pop-up).
  async function doProfile(onConflict = null, confirmed = false) {
    if (profileInFlight.current) return;                // synchronous double-click guard (phase 4E)
    if (preview.status !== 'ready' || preview.data?.would_be_refused) return;
    if (typeof onConflict !== 'string' && !confirmed && summarizePreview(preview.data).needsConfirmation) {
      setConfirmKey(preview.key); return;               // confirmation bound to THIS preview
    }
    setConfirmKey(null);
    const previewed = preview.data;
    profileInFlight.current = true;
    setBusy(true); setError(null); setPhase('profiling');
    try {
      const body = { ...buildBody };
      // Defensive type guard (the button is already wired arrow, but we align
      // with buildProfile to harden against a future onClick={handler} rewiring).
      if (typeof onConflict === 'string') body.on_conflict = onConflict;  // replay after pop-up choice
      setConflict(null); setNameNotice(null);
      // Build = background job (apply_all + previewed fingerprint): starts + polls until done|error.
      const info = await buildWithPreview(selected.chart_id, body, previewed);
      setProfInfo(info); setPhase('profiled');
      onDone?.(info);
    } catch (e) {
      const c = classifyBuildError(e);
      if (c.kind === 'name_conflict') setConflict(e.detail);   // collision → 3-choice pop-up
      else if (c.kind === 'identity_refused') {
        setError(errorText(e, t)); setPreviewNonce((n) => n + 1);           // the preview explains what to exclude
      } else if (c.kind === 'inputs_changed' || c.kind === 'coverage_refused') {
        setError(t(`build_preview.${c.kind}`)); setPreviewNonce((n) => n + 1);   // re-preview, re-confirm
      } else if (c.kind === 'name_length') setNameNotice(t('mesures.profile_name_maxlen'));
      else if (c.kind === 'name_invalid') setNameNotice(e.message);
      else setError(errorText(e, t));
      setPhase('scanned');
    }
    finally { profileInFlight.current = false; setBusy(false); }
  }

  // ─── Validate (profcheck b) — local, forward A2B terminal ───
  async function doValidate() {
    setBusy(true); setError(null); setPhase('profiling');
    try {
      const info = await api.validateChart(selected.chart_id,
        { profile_path: profile?.absolute_path || profile?.path,
          // authentic profile identity (= slot's paper/GE) for the safety net
          media_id: mediaid, gloss_enhancer: geStr });
      setProfInfo(info); setPhase('validated');
      onDone?.(info);
    } catch (e) { setError(e.message); setPhase('scanned'); }
    finally { setBusy(false); }
  }

  // ─── Disk management: delete / lighten a chart (manual + confirmed) ───
  async function doConfirmChartAction() {
    if (!chartAction) return;
    const { type, chart } = chartAction;
    setBusy(true); setError(null);
    try {
      if (type === 'delete') await api.deleteChart(chart.chart_id);
      else await api.lightenChart(chart.chart_id);
      setChartAction(null);
      setSelectNonce((n) => n + 1);     // refetch the list
    } catch (e) { setError(e.message); setChartAction(null); }
    finally { setBusy(false); }
  }

  async function loadColprofHelp() {
    setShowColprofHelp((v) => !v);
    if (colprofHelp == null) {
      try { const d = await api.getChartColprofHelp(); setColprofHelp(d.help); }
      catch { setColprofHelp(t('chart_create.help_unavailable')); }
    }
  }

  async function saveColprofPreset() {
    if (!colprofPresetKey.trim() || !colprofFlags.trim()) return;
    try {
      const d = await api.saveChartColprofPreset(
        { key: colprofPresetKey.trim(), flags: colprofFlags.trim(), description: '' });
      setColprofPresets(d.presets || []); setColprofPresetKey('');
    } catch (e) { setError(e.message); }
  }

  function handleClose() {
    // Release this chart's scan session on leaving the wizard: the common "one
    // chart = one scan, then done" case must NOT leave an `awaiting_next` lock
    // that blocks the next chart. Fire-and-forget; SKIPPED while a scan is running
    // (the job resolves the session: success → awaiting_next, error → close).
    // abandonScan on a chart with no active session is a harmless 404 (caught).
    // Deliberate multi-scan stays open as long as the wizard stays open.
    const target = selected || initialChart;
    const cid = target?.chart_id;
    if (cid && phase !== 'scanning' && !chartMeasurement(target).external) api.abandonScan(cid).catch(() => {});
    onClose?.();
  }

  if (!open) return null;

  // Source of the ΔE report: the card/table serves the scanned screen (scanDelta) OR the opening
  // from the chart view (concordanceDelta). Mutually exclusive (different phases).
  const activeDelta = concordanceDelta || scanDelta;
  // The selected chart is measured outside freeglaz (printtarg): never the SOL screens.
  const selectedExternal = chartMeasurement(selected).external;
  const resultExternal = result?.layout === 'printtarg';
  // Printing an external chart needs loaded media of the format it was laid out for (checked
  // for the chart's own instrument); the print route refuses the same cases.
  const printBlockedBy = resultExternal
    ? externalPrintBlock(result, externalMediaCheck(loadedPaper, ptOptions || null, formats, result.instrument), formats)
    : null;
  const activeDeltaChartId = concordanceDelta?._chartId || selected?.chart_id;
  // Number of KEPT scans (the toggle excludes) → drives the base selector + the average.
  const nKeptScanned = chartDetail ? chartDetail.scans.filter((s) => s.kept).length : nScans;
  // ─── two-pass profiling (print mode): selector + resume card, and the composition of a pass ───
  function resumeRun(r) {
    const action = stepAction(r.next_step);
    if (action === 'create_pass2' && r.first?.intermediate?.build_id) {
      startPass2({ run: r.run, buildId: r.first.intermediate.build_id }); return;
    }
    const last = r.later?.length ? r.later[r.later.length - 1].chart_id : null;
    if (action === 'print_first' || action === 'print_last') {
      const toPrint = action === 'print_first' ? r.first?.chart_id : last;
      if (toPrint) { printExisting(toPrint); return; }
    }
    const target = action === 'open_first' ? (r.first?.chart_id || last) : (last || r.first?.chart_id);
    handleClose();
    if (target) openInLibrary(target);
  }
  const twoPassAvailable = mode === 'print' && !!stratDefs && (stratDefs.strategies || []).some((x) => x.id === STRATEGY_TWO_PASS);
  const strategyNode = twoPassAvailable ? (
    <StrategySelector t={t} value={strategyMode} locked={!!continuation} runs={runsInProgress} onResume={resumeRun}
                      onChange={(v) => { setStrategyMode(v); setError(null); setFallbackOffer(false); }}/>
  ) : null;
  const curRoll = estimate?.state === 'ok' ? estimate.data?.roll_length_mm : null;
  const otherRoll = passId === 'pass1' ? (otherEstimate?.state === 'ok' ? otherEstimate.data?.roll_length_mm : null)
    : (stratRun?.first?.roll_length_mm ?? null);
  const totalRoll = paperTotal(curRoll, otherRoll);
  const twoPassNode = twoPassActive ? (
    <TwoPassCompose t={t} passId={passId} form={twoForm} setForm={setTwoForm} choices={stratDefs?.choices}
      composed={composed} composeError={composeErr} run={stratRun} maxPatches={maxPatches} dev={DEV}
      paperNode={external && estimate ? <PaperEstimate t={t} estimate={estimate}/> : null}
      otherPaperNode={!external ? null : passId === 'pass1'
        ? (otherEstimate ? <PaperEstimate t={t} estimate={otherEstimate}/> : null)
        : (stratRun?.first ? <FirstPassPaper t={t} first={stratRun.first}/> : null)}
      totalNode={external && totalRoll != null ? (
        <p className="text-tiny font-semibold text-text-strong" data-strategy-total-roll={Math.round(totalRoll)}>
          {t('strategy.paper.total', { m: (totalRoll / 1000).toLocaleString(undefined, { maximumFractionDigits: 2 }) })}
        </p>) : null}/>
  ) : null;
  const startOptions = [
    ...(residentCurrent ? [{ value: 'affiner', label: t('chart_create.start_refine') }] : []),
    { value: 'zero', label: t('chart_create.start_zero') },
    ...(builtProfiles.length ? [{ value: 'freeglaz', label: t('chart_create.start_built') }] : []),
    ...(customs.length ? [{ value: 'custom', label: t('chart_create.start_custom') }] : []),
  ];
  const startHint = cChoice === 'affiner' && residentCurrent
    ? `${t('chart_create.start_refine_desc')} ${t('chart_create.precond_with', { name: residentCurrent.name })}`
    : t({ zero: 'chart_create.start_zero_desc', freeglaz: 'chart_create.start_built_desc', custom: 'chart_create.start_custom_desc' }[cChoice]
        || 'chart_create.start_refine_desc');
  const onePassNode = guidedActive ? (
    <OnePassCompose t={t} form={oneForm} setForm={setOneForm} choices={stratDefs?.choices}
      composed={oneComposed} composeError={oneComposeErr} maxPatches={maxPatches}
      preconditioned={onePre} cLabel={cLabel} startHint={startHint}
      startNode={(
        <span className="flex items-center gap-2 flex-wrap" data-field="start" data-start={cChoice}>
          <NSelect value={cChoice} onChange={setCChoice} options={startOptions}/>
          {cChoice === 'freeglaz' && (
            <NSelect value={cFreeglaz} onChange={setCFreeglaz}
                     options={builtProfiles.map((p) => ({ value: p.id,
                       label: [p.name, p.chart_id, (p.built_at || '').slice(0, 10)].filter(Boolean).join(' · ') }))}/>
          )}
          {cChoice === 'custom' && (
            <NSelect value={cCustom} onChange={setCCustom} options={customs.map((p) => ({ value: p.id, label: p.name }))}/>
          )}
        </span>
      )}
      paperNode={external && estimate ? <PaperEstimate t={t} estimate={estimate}/> : null}
      onEditLine={(line) => { setFlags(line); setSrcMode('targen'); }}/>
  ) : null;
  // the chart of the scanned screen belongs to a two-pass profiling (honeycomb charts)
  const scannedStrategy = chartDetail?.identity?.strategy || null;
  const scannedIsFirstPass = scannedStrategy?.id === STRATEGY_TWO_PASS && scannedStrategy.pass === 'pass1';
  const scannedFinalLine = plannedColprofLine(chartDetail?.identity);   // two-pass final or guided one pass
  if (scannedFinalLine && finalLineFor !== chartDetail.identity.chart_id) {
    setFinalLineFor(chartDetail.identity.chart_id);          // the strategy's final line, still editable
    setColprofFlags(scannedFinalLine);
  }

  const kicker = isValidate ? t('chart_create.kicker_validate')
               : mode === 'scan' ? t('chart_create.kicker_scan') : t('chart_create.kicker');
  const title = isValidate ? t('chart_create.title_validate')
              : mode === 'scan' ? t('chart_create.title_scan') : t('chart_create.title_argyll');

  return createPortal(
    <div className="fixed inset-0 z-[100] bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4"
         onClick={(e) => { if (e.target === e.currentTarget) handleClose(); }}>
      <div className="bg-bg border border-border-soft rounded-[14px] shadow-2xl w-[min(960px,95vw)] max-h-[92vh] flex flex-col overflow-hidden">
        <div className="px-6 pt-5 pb-4 flex items-start gap-3 border-b border-border-soft">
          {isValidate ? <ShieldCheck size={18} className="text-accent mt-0.5"/>
            : mode === 'scan' ? <ScanLine size={18} className="text-accent mt-0.5"/>
                           : <Grid3x3 size={18} className="text-accent mt-0.5"/>}
          <div className="flex-1 min-w-0">
            <div className="text-[10px] font-bold tracking-[0.10em] uppercase text-accent font-mono">{kicker}</div>
            <h2 className="text-[17px] font-semibold text-text-strong">{title}</h2>
            <p className="text-tiny text-text-faint mt-0.5">
              {paperName}{DEV && <span className="font-mono"> · {mediaid} · GE={geStr}</span>}
              {!DEV && <> · {t('chart_create.gloss')} {_geLabel(geStr)}</>}
            </p>
          </div>
          <button type="button" onClick={handleClose} aria-label={t('common.close')}
                  className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-strong hover:bg-sunken">
            <X size={16}/>
          </button>
        </div>

        <div className="flex-1 min-h-0 px-6 py-5 overflow-y-auto space-y-5 text-sm text-text-strong">
          {error && (
            <div className="flex items-start gap-2 text-xs2 text-danger bg-danger/10 rounded-md px-3 py-2">
              <AlertTriangle size={14} className="mt-0.5"/>
              <span className="flex-1">{error}
                {fallbackOffer && phase === 'compose' && (
                  <span className="block mt-1.5 text-text-strong">
                    {t('strategy.fallback.explain')}{' '}
                    <button type="button" data-action="retry-quasi-random" disabled={busy}
                            onClick={() => {
                              if (guidedActive) {
                                const next = { ...oneForm, ofps: 'Q' };
                                setOneForm(next); doCreate(null, next); return;
                              }
                              const key = passId === 'pass2' ? 'pass2Ofps' : 'pass1Ofps';
                              const next = { ...twoForm, [key]: 'Q' };
                              setTwoForm(next); doCreate(next);
                            }}
                            className="underline font-semibold text-accent disabled:opacity-50">{t('strategy.fallback.retry')}</button>
                  </span>
                )}
              </span>
            </div>
          )}

          {phase === 'compose' && (
            <ComposeView
              t={t} formats={formats} mediaKey={mediaKey} pickFormat={pickFormat}
              maxPatches={maxPatches}
              fmt={fmt} srcMode={effSrcMode} setSrcMode={setSrcMode} flags={flags} setFlags={setFlags}
              fOver={fOver} fCount={fCount} ti1Text={ti1Text} setTi1Text={setTi1Text}
              ti1Name={ti1Name} onTi1File={onTi1File} presets={presets} presetKey={presetKey}
              setPresetKey={setPresetKey} savePreset={savePreset} loadHelp={loadHelp}
              showHelp={showHelp} help={help} geStr={geStr}
              cChoice={cChoice} setCChoice={setCChoice} cCustom={cCustom} setCCustom={setCCustom}
              residentCurrent={residentCurrent} customs={customs} dev={DEV}
              builtProfiles={builtProfiles} cFreeglaz={cFreeglaz} setCFreeglaz={setCFreeglaz}
              validate={isValidate}
              method={method} availability={availability} onMethod={setMethodChoice}
              ptOptions={ptOptions} instrument={instrument} onInstrument={setInstrumentChoice}
              extFormats={extFormats} mediaCheck={mediaCheck} extKey={effMediaKey}
              layoutOpts={layoutOpts} layout={layout} onLayoutChoice={setLayoutChoice} estimate={estimate}
              strategyNode={strategyNode} twoPassNode={twoPassNode} methodLocked={!!continuation && twoPassActive}
              guidedAvailable={guidedAvailable && !twoPassActive} onePassNode={onePassNode}/>
          )}

          {phase === 'loading' && !error && <Spinner t={t} label={t('chart_print.loading')}/>}
          {phase === 'preview' && result?.existing && <ExistingChartNote t={t} result={result}/>}
          {phase === 'preview' && result?.strategy && !result.existing && <StrategyCreatedNote t={t} strategy={result.strategy}/>}
          {phase === 'preview' && result?.guided && !result.existing && <GuidedCreatedNote t={t} guided={result.guided}/>}
          {phase === 'preview' && result && !resultExternal && (
            <PreviewView t={t} result={result} mediaKey={mediaKey} geStr={geStr} dev={DEV}/>
          )}
          {phase === 'preview' && result && resultExternal && (
            <PrinttargPreviewView t={t} result={result} family={printerFamily} printBlockedBy={printBlockedBy} dev={DEV}/>
          )}

          {phase === 'printing' && <Spinner t={t} label={t('chart_create.printing')}/>}

          {phase === 'printed' && !selectedExternal && (
            <PrintedView t={t} chartId={selected?.chart_id} printInfo={printInfo}
                         onScan={() => setPhase('scanready')} onClose={handleClose}/>
          )}
          {phase === 'printed' && selectedExternal && (
            <PrintedExternalView t={t} chart={selected} onClose={handleClose}
                                 onOpenLibrary={() => { const id = selected?.chart_id; handleClose(); if (id) openInLibrary(id); }}/>
          )}

          {mode === 'scan' && phase === 'select' && (
            <SelectView t={t} charts={charts} paperName={paperName}
                        onPick={(c) => { setSelected(c); setPhase('scanready'); }}
                        onConcordance={openConcordance}
                        onDelete={(c) => setChartAction({ type: 'delete', chart: c })}
                        onLighten={(c) => setChartAction({ type: 'lighten', chart: c })}/>
          )}

          {isValidate && phase === 'select' && (
            <SelectView t={t} charts={charts} paperName={paperName} scannedMode
                        composeLabel={t('chart_create.validate_compose_new')}
                        onComposeNew={() => setPhase('compose')}
                        onPick={(c) => {
                          setSelected(c);
                          setScanInfo({ n_patches: c.patch_count });
                          setPhase('scanned');     // → ValidateReadyView → "Validate"
                        }}
                        onDelete={(c) => setChartAction({ type: 'delete', chart: c })}
                        onLighten={(c) => setChartAction({ type: 'lighten', chart: c })}/>
          )}

          {phase === 'scanready' && selected && selectedExternal && (
            <div className="space-y-2.5">
              <ScanReadyView chart={selected}/>
              <ExternalMeasurePanel chartId={selected.chart_id} instrument={chartMeasurement(selected).instrument}
                                    nScans={selected.n_scans ?? 0}/>
            </div>
          )}
          {phase === 'scanready' && selected && !selectedExternal && (
            <div className="space-y-2.5">
              <ScanReadyView chart={selected}/>
              {scanUnsupported && (
                <div className="flex items-start gap-2 text-tiny text-warn bg-icc-warn/10 rounded-md px-3 py-2 leading-snug"
                     data-testid="scan-unsupported-model">
                  <AlertTriangle size={14} className="flex-shrink-0 mt-0.5"/> {t('scan.unsupported_model')}
                </div>
              )}
              <div className="rounded-md border border-accent/30 bg-accent/5 px-3 py-2 text-tiny leading-snug">
                <strong>{t('scan.roll.reposition_title')}</strong>{' '}
                <span className="text-text-muted">{t('scan.roll.reposition_detail')}</span>
              </div>
              {nScans > 0 && (
                <p className="text-tiny text-text-faint">
                  {t('scan.roll.session_scans', { count: nScans })}
                </p>
              )}
              {cooldownSec > 0 && (
                <div className="flex items-center gap-2 text-tiny text-warn bg-icc-warn/10 rounded-md px-3 py-2">
                  <AlertTriangle size={14}/> {t('scan.roll.cooldown_banner', { n: cooldownSec })}
                </div>
              )}
            </div>
          )}
          {phase === 'scanning' && (
            <div className="space-y-3 py-2">
              <div className="flex items-center gap-2 text-text-strong">
                <ScanLine size={16} className="text-accent animate-pulse"/>
                <span className="font-medium">{t('scan.roll.scanning_title')}</span>
                {scanProg?.phase && <span className="font-mono text-tiny text-text-faint">{scanProg.phase}</span>}
              </div>
              <div className="w-full h-2 rounded-full bg-sunken overflow-hidden">
                <div className="h-full bg-accent transition-all" style={{ width: `${scanProg?.percent || 5}%` }}/>
              </div>
              <p className="text-tiny text-text-faint leading-relaxed">
                {t('scan.roll.scanning_hint_pre')} <strong>{t('scan.roll.scanning_hint_warn')}</strong>{' '}
                {t('scan.roll.scanning_hint_post')}
              </p>
            </div>
          )}

          {phase === 'scanned' && isValidate && (
            <ValidateReadyView t={t} scanInfo={scanInfo}
                               profileName={profile?.label || profile?.filename}/>
          )}
          {phase === 'scanned' && !isValidate && (
            <div className="space-y-4">
              <ScannedView t={t} scanInfo={scanInfo}/>

              {/* Intrinsic QC of THIS scan (advisory). Same presentation as Measurements. */}
              {scanInfo?.ti3 && (
                <div className="space-y-2">
                  <MeasureQcSummary compact state={qcPanelState(qcScan)}/>
                  <button type="button" onClick={() => setMeasuresOpen(true)}
                          className="text-xs2 font-semibold px-3 py-1.5 rounded-md border border-border-soft text-text-strong hover:bg-sunken">
                    {t('measure_qc.open_measures')}
                  </button>
                </div>
              )}

              {/* Scans + KEEP/EXCLUDE toggle (software mutation; exclude a bad scan before averaging) */}
              {chartDetail && chartDetail.scans.length >= 2 && (
                <div className="space-y-1">
                  <div className="text-tiny font-semibold uppercase tracking-wide text-text-faint">{t('scan.scans_label')}</div>
                  <ScanKeepList scans={chartDetail.scans} onSetRole={onSetRoleWizard} disabled={busy}/>
                </div>
              )}

              {/* Safety net: inter-scan concordance BEFORE averaging (drying vs contamination) */}
              {scanDelta && (
                <ScanDeltaCard delta={scanDelta} onSeeAll={() => setDeltaAllOpen(true)}/>
              )}

              {/* two-pass profiling: the run, and for its first pass the intermediate profile */}
              {scannedStrategy && (
                <StrategyRunPanel chartId={selected?.chart_id} identity={chartDetail?.identity} nIncluded={nKeptScanned}
                  stateStamp={stateStamp} onPreparePass2={startPass2} onBuilt={_loadScanned}
                  colprofFlags={colprofFlags} setColprofFlags={setColprofFlags} onVariant={setPresetSources}
                  preview={preview}/>
              )}

              {/* SHARED build controls (base + colprof + command). Button OMITTED here → the
                  wizard footer keeps its [Profile] (doProfile) unchanged (no regression). */}
              {!scannedIsFirstPass && <ProfileBuildPanel
                nIncluded={nKeptScanned}
                base={profileBase} setBase={setProfileBase}
                colprofFlags={colprofFlags} setColprofFlags={setColprofFlags}
                colprofPresets={colprofPresets}
                colprofPresetKey={colprofPresetKey} setColprofPresetKey={setColprofPresetKey}
                saveColprofPreset={saveColprofPreset}
                loadColprofHelp={loadColprofHelp} showColprofHelp={showColprofHelp}
                colprofHelp={colprofHelp}
                chartId={selected?.chart_id} onSelectionChange={setExtraSources}
                nameNotice={nameNotice} preview={preview} presetSources={presetSources}/>}

              <div className="rounded-md border border-border-soft px-3 py-2 text-tiny text-text-faint leading-relaxed">
                <Info size={12} className="inline mr-1 -mt-0.5"/>
                {t('scan.base.drying_note')}
              </div>
            </div>
          )}
          {phase === 'profiling' && <Spinner t={t} label={t(isValidate ? 'chart_create.validating' : 'chart_create.profiling')}/>}

          {phase === 'profiled' && (
            <ProfiledView t={t} profInfo={profInfo} onClose={handleClose} dev={DEV}/>
          )}

          {phase === 'validated' && (
            <ValidatedView t={t} report={profInfo}
                           profileName={profile?.label || profile?.filename}/>
          )}
        </div>

        {phase === 'compose' && (
          <div className="px-6 py-4 border-t border-border-soft flex items-center justify-end gap-2">
            <button type="button" onClick={handleClose}
                    className="text-xs2 text-text-muted hover:text-text-strong px-3 py-1.5">{t('common.cancel')}</button>
            <button type="button" onClick={() => doCreate()} disabled={!canCreate} data-action="create-chart"
                    className="flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md disabled:opacity-40 hover:bg-accent-press">
              <Grid3x3 size={14}/>{busy ? t('chart_create.creating')
                : twoPassActive ? t('strategy.compose.create_pass', { index: passId === 'pass2' ? 2 : 1 }) : t('chart_create.create')}
            </button>
          </div>
        )}

        {/* FIXED footer of the preview screen: the "Print" action is always visible
            (never below the fold), even with a large PNG in the scrollable area. */}
        {phase === 'preview' && (
          <div className="px-6 py-4 border-t border-border-soft flex items-center justify-end gap-2">
            <button type="button" onClick={handleClose}
                    className="text-xs2 text-text-muted hover:text-text-strong px-3 py-1.5">{t('common.cancel')}</button>
            <button type="button" onClick={() => setConfirmPrint(true)}
                    disabled={busy || !!printBlockedBy || (!!result?.existing && !result.existing.printable)}
                    data-action="print-chart"
                    className="flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press disabled:opacity-50">
              <Printer size={14}/>{result?.existing?.printed_at ? t('chart_print.reprint_btn') : t('chart_create.print_btn')}
            </button>
          </div>
        )}

        {phase === 'loading' && (
          <div className="px-6 py-4 border-t border-border-soft flex items-center justify-end gap-2">
            <button type="button" onClick={handleClose}
                    className="text-xs2 text-text-muted hover:text-text-strong px-3 py-1.5">{t('common.cancel')}</button>
          </div>
        )}

        {/* FIXED footer of the "ready to scan" screen: "Scan" action always
            visible (same principle as the preview screen — chart preview at top). */}
        {phase === 'scanready' && selected && selectedExternal && (
          <div className="px-6 py-4 border-t border-border-soft flex items-center justify-end gap-2">
            <button type="button" onClick={handleClose}
                    className="text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press">{t('common.close')}</button>
          </div>
        )}
        {phase === 'scanready' && selected && !selectedExternal && (
          <div className="px-6 py-4 border-t border-border-soft flex items-center justify-end gap-2">
            <button type="button" onClick={handleClose}
                    className="text-xs2 text-text-muted hover:text-text-strong px-3 py-1.5">{t('common.cancel')}</button>
            <button type="button" onClick={doScan} disabled={busy || cooldownSec > 0 || scanUnsupported}
                    className="flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press disabled:opacity-50">
              <ScanLine size={14}/>{cooldownSec > 0 ? t('scan.roll.cooldown_btn', { n: cooldownSec }) : (nScans > 0 ? t('scan.roll.scan_again') : t('chart_create.scan_btn'))}
            </button>
          </div>
        )}

        {/* FIXED footer of the "scanned" screen: "Build the profile" (profiling) or
            "Validate" (profcheck b). */}
        {phase === 'scanned' && (
          <div className="px-6 py-4 border-t border-border-soft flex items-center justify-end gap-2">
            <button type="button" onClick={handleClose}
                    className="text-xs2 text-text-muted hover:text-text-strong px-3 py-1.5">{t('common.cancel')}</button>
            {!isValidate && !selectedExternal && (
              <button type="button" onClick={() => { setError(null); setPhase('scanready'); }} disabled={busy || scanUnsupported}
                      className="flex items-center gap-1.5 text-xs2 font-medium border border-border-soft text-text-muted px-3 py-1.5 rounded-md hover:text-text-strong hover:bg-sunken disabled:opacity-50">
                <ScanLine size={14}/>{t('scan.roll.scan_again')}
              </button>
            )}
            {!scannedIsFirstPass && <button type="button" onClick={() => (isValidate ? doValidate() : doProfile())} data-action="wizard-build"
                    disabled={busy || (!isValidate && (preview.status !== 'ready' || !!preview.data?.would_be_refused))}
                    className="flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press disabled:opacity-50">
              {isValidate ? <ShieldCheck size={14}/> : <FlaskConical size={14}/>}
              {isValidate ? t('chart_create.validate_btn') : t('chart_create.profile_btn')}
            </button>}
          </div>
        )}

        {phase === 'validated' && (
          <div className="px-6 py-4 border-t border-border-soft flex items-center justify-end">
            <button type="button" onClick={handleClose}
                    className="text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press">{t('common.close')}</button>
          </div>
        )}
      </div>

      {/* Physical act confirmation (printing) — from the preview screen */}
      {confirmPrint && (
        <ConfirmModal open
          icon={<AlertTriangle size={18} className="text-warn"/>}
          title={t('chart_create.print_confirm_title')}
          message={resultExternal
            ? (result?.media_source === 'sheet'
              ? t('measurement.print_confirm_sheet', { paper: paperName, instrument: instrumentLabel(t, result?.instrument),
                                                      ge: _geLabel(geStr),
                                                      format: formats.find((f) => f.key === result?.media_key)?.name || result?.media_key,
                                                      w: Math.round(result?.tiff_w_mm ?? 0), h: Math.round(result?.tiff_h_mm ?? 0) })
              : t('measurement.print_confirm', { paper: paperName, instrument: instrumentLabel(t, result?.instrument),
                                                ge: _geLabel(geStr), mm: Math.round(result?.roll_length_mm ?? result?.tiff_h_mm ?? 0) }))
            : t('chart_create.print_confirm', { paper: paperName, format: (result?.media_key || mediaKey).toUpperCase(), ge: _geLabel(geStr) })}
          confirmLabel={t('chart_create.print_confirm_btn')}
          cancelLabel={t('common.cancel')}
          confirmKind="primary"
          onConfirm={() => doPrint()}
          onCancel={() => setConfirmPrint(false)}/>
      )}

      {/* Later pass of a two-pass profiling: the print conditions changed since the first pass */}
      {printConfirm && (
        <ConfirmModal open
          icon={<AlertTriangle size={18} className="text-warn"/>}
          title={t('strategy.print.confirm_title')}
          message={t('strategy.print.confirm_body', { list: printConfirm.codes.map((c) => t(`strategy.print.code.${c}`, { defaultValue: c })).join(' · ') })}
          confirmLabel={t('strategy.print.confirm_btn')} cancelLabel={t('common.cancel')} confirmKind="primary"
          onConfirm={() => { setPrintConfirm(null); doPrint(true, true); }}
          onCancel={() => setPrintConfirm(null)}/>
      )}

      {/* Chart deletion / lightening confirmation (manual, never auto) */}
      {chartAction && (
        <ConfirmModal open
          icon={chartAction.type === 'delete'
            ? <Trash2 size={18} className="text-danger"/>
            : <Archive size={18} className="text-accent"/>}
          title={t(chartAction.type === 'delete' ? 'chart_create.delete_confirm_title' : 'chart_create.lighten_confirm_title')}
          message={t(chartAction.type === 'delete' ? 'chart_create.delete_confirm' : 'chart_create.lighten_confirm', { id: chartAction.chart.chart_id })}
          confirmLabel={t(chartAction.type === 'delete' ? 'chart_create.delete_btn' : 'chart_create.lighten_btn')}
          cancelLabel={t('common.cancel')}
          confirmKind={chartAction.type === 'delete' ? 'danger' : 'primary'}
          busy={busy}
          onConfirm={doConfirmChartAction}
          onCancel={() => setChartAction(null)}/>
      )}

      {/* Placement confirmation before EVERY scan (first included): a scan is a
          machine op → ALWAYS confirm the right chart is loaded. Recalls the chart
          NAME + load instructions per media. NO "reposition exactly / same
          position" (honeycomb geometry absorbs small offsets, cf. #9bis). */}
      {chartMovedAsk && createPortal(
        <div className="fixed inset-0 z-[110] bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4"
             role="dialog" aria-modal="true">
          <div className="bg-bg border border-border-soft rounded-[14px] shadow-2xl w-[460px] max-w-[95vw] p-5 space-y-3">
            <div className="flex items-start gap-2.5">
              <AlertTriangle size={18} className="text-warn mt-0.5 shrink-0"/>
              <div className="space-y-2">
                <h3 className="text-[15px] font-semibold text-text-strong">{t('scan.moved_modal.title')}</h3>
                <p className="text-xs2 text-text-muted leading-relaxed">
                  {t('scan.moved_modal.body_prefix')} <span className="font-mono text-text-strong">{selected?.chart_id}</span> {t('scan.moved_modal.body_suffix')}
                </p>
                <ul className="text-xs2 text-text-muted leading-relaxed list-disc pl-4 space-y-0.5">
                  <li>{t('scan.moved_modal.roll')}</li>
                  <li>{t('scan.moved_modal.sheet')}</li>
                </ul>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 pt-1">
              <button type="button"
                      onClick={() => { setChartMovedAsk(false); handleClose(); }}
                      className="text-xs2 text-text-muted hover:text-text-strong px-3 py-1.5">
                {t('common.cancel')}
              </button>
              <button type="button" onClick={() => doScan()}
                      className="flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press">
                {t('scan.moved_modal.confirm')}
              </button>
            </div>
          </div>
        </div>, document.body)}

      {/* Inter-chart lock (409): a session is active on ANOTHER chart. Offer a
          DIRECT "finish it" action (was a dead-end text refusal → the aggravating
          factor of the orphan bug). */}
      {sessionBlockedBy && createPortal(
        <div className="fixed inset-0 z-[110] bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4"
             role="dialog" aria-modal="true">
          <div className="bg-bg border border-border-soft rounded-[14px] shadow-2xl w-[460px] max-w-[95vw] p-5 space-y-3">
            <div className="flex items-start gap-2.5">
              <AlertTriangle size={18} className="text-warn mt-0.5 shrink-0"/>
              <div>
                <h3 className="text-[15px] font-semibold text-text-strong">{t('scan.blocked_modal.title')}</h3>
                <p className="text-xs2 text-text-muted mt-1 leading-relaxed">
                  {t('scan.blocked_modal.body_prefix')} <span className="font-mono">{sessionBlockedBy}</span> {t('scan.blocked_modal.body_suffix')}
                </p>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 pt-1">
              <button type="button" onClick={() => setSessionBlockedBy(null)}
                      className="text-xs2 text-text-muted hover:text-text-strong px-3 py-1.5">
                {t('common.cancel')}
              </button>
              <button type="button"
                      onClick={async () => {
                        const blocked = sessionBlockedBy;
                        setSessionBlockedBy(null);
                        await api.abandonScan(blocked).catch(() => {});
                        doScan();   // retry scanning the CURRENT chart
                      }}
                      className="flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press">
                {t('scan.blocked_modal.finish_and_scan')}
              </button>
            </div>
          </div>
        </div>, document.body)}

      {/* CLC safety net (parity with ProfileWizard) BEFORE printing the chart. SAME ConfirmModal
          component as ProfileWizard → identical look. Print anyway / go calibrate. */}
      <ConfirmModal open={!!clcWarn}
        title={t(clcWarn === 'pending' ? 'wizard_profile_paper.validation_clc_pending_title' : 'wizard_profile_paper.validation_no_clc_title')}
        message={t(clcWarn === 'pending' ? 'wizard_profile_paper.validation_clc_pending_message' : 'wizard_profile_paper.validation_no_clc_message')}
        cancelLabel={t('wizard_profile_paper.button_cancel')}
        thirdLabel={t('wizard_profile_paper.validation_no_clc_run_clc_first')} thirdKind="primary"
        onThird={() => { setClcWarn(null); onClose?.(); }}
        confirmLabel={t('wizard_profile_paper.validation_no_clc_continue_anyway')} confirmKind="primary"
        onConfirm={() => { setClcWarn(null); doPrint(true); }} onCancel={() => setClcWarn(null)}/>

      {/* Collision (409) → OS 3-choice pop-up. Keep both = -N; Replace (danger) = overwrites
          + preserves tags/notes. Replay = doProfile(intent) with the same body. */}
      <ConfirmModal open={!!conflict}
        title={t('mesures.profile_conflict_title')}
        message={conflict ? t('mesures.profile_conflict_body_build', { name: conflict.name }) : ''}
        cancelLabel={t('common.cancel')}
        thirdLabel={t('mesures.profile_conflict_keep_both')}
        confirmLabel={t('mesures.profile_conflict_replace')} confirmKind="danger"
        busy={busy}
        onThird={() => { setConflict(null); doProfile('keep_both'); }}
        onConfirm={() => { setConflict(null); doProfile('replace'); }}
        onCancel={() => setConflict(null)}/>

      {/* Concordance card opened FROM the chart view (read-only, without re-scanning) */}
      {concordanceDelta && createPortal(
        <div className="fixed inset-0 z-[105] bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4"
             role="dialog" aria-modal="true"
             onClick={(e) => { if (e.target === e.currentTarget) setConcordanceDelta(null); }}>
          <div className="bg-bg border border-border-soft rounded-[14px] shadow-2xl w-[520px] max-w-[95vw] p-5 space-y-3">
            <div className="flex items-start gap-3">
              <div className="flex-1 min-w-0">
                <div className="text-[10px] font-bold tracking-[0.10em] uppercase text-accent font-mono">{t('scan.concordance_title')}</div>
                <h3 className="text-[15px] font-semibold text-text-strong font-mono truncate">{activeDeltaChartId}</h3>
              </div>
              <button type="button" onClick={() => setConcordanceDelta(null)} aria-label={t('common.close')}
                      className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-strong hover:bg-sunken">
                <X size={16}/>
              </button>
            </div>
            <ScanDeltaCard delta={concordanceDelta} onSeeAll={() => setDeltaAllOpen(true)}/>
          </div>
        </div>, document.body)}

      {confirmKey && confirmKey === preview.key && preview.status === 'ready' && (
        <BuildConfirmModal summary={summarizePreview(preview.data)}
          onCancel={() => setConfirmKey(null)} onConfirm={() => doProfile(null, true)}/>
      )}

      {/* Single-scan measurements + intrinsic QC (the SAME component as the Measurements tab) */}
      {measuresOpen && scanInfo?.ti3 && selected?.chart_id && (
        <MeasurementsModal open chartId={selected.chart_id} ti3={scanInfo.ti3} onChanged={_loadScanned}
          onClose={() => setMeasuresOpen(false)}/>
      )}

      {/* Complete data of the inter-scan comparison (reuses the generalized AllPatchesModal) */}
      {deltaAllOpen && activeDelta && (
        <AllPatchesModal open patches={activeDelta.patches}
          title={`${activeDeltaChartId} · ${activeDelta.n_scans} scans`}
          titleLabel={t('scan.concordance_all_label', { count: activeDelta.n_patches })}
          labTargetLabel={t('scan.lab_scan_first')} labAchievedLabel={t('scan.lab_scan_nth', { n: activeDelta.n_scans })}
          onClose={() => setDeltaAllOpen(false)}/>
      )}
    </div>,
    document.body,
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Profiling strategy selector (print mode) + the runs of this paper still in progress.
function StrategySelector({ t, value, onChange, locked, runs = [], onResume }) {
  return (
    <div className="space-y-2" data-strategy-selector={value}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold text-text-strong uppercase tracking-wide">{t('strategy.selector.title')}</span>
        <span className="whitespace-nowrap">
          <Segmented value={value} onChange={locked ? () => {} : onChange} options={[
            { value: 'one', label: t('strategy.selector.one') },
            { value: 'two', label: t('strategy.selector.two') },
          ]}/>
        </span>
      </div>
      <p className="text-tiny text-text-faint leading-snug">
        {t(value === 'two' ? 'strategy.selector.two_desc' : 'strategy.selector.one_desc')}
      </p>
      {!locked && runs.length > 0 && (
        <div className="space-y-1.5" data-resume-runs={runs.length}>
          {runs.slice(0, 3).map((r) => (
            <div key={r.run} data-resume-run={r.run}
                 className="flex items-center gap-2 rounded-md border border-accent/30 bg-accent/[0.05] px-3 py-2 text-xs2">
              <span className="flex-1 min-w-0">
                <span className="font-semibold text-text-strong">{t('strategy.resume.title')}</span>{' '}
                <span className="font-mono text-text-muted">{r.run}</span>
                <span className="block text-tiny text-text-muted">{t('strategy.resume.next', { step: t(`strategy.step.${r.next_step}`) })}</span>
              </span>
              <button type="button" onClick={() => onResume(r)} data-action="resume-run"
                      className="flex-shrink-0 text-xs2 font-semibold border border-accent/50 text-accent rounded-md px-3 py-1 hover:bg-accent/10">
                {t('strategy.resume.btn')}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// After creating a pass: which pass, the targen line actually run, the preconditioner used.
function StrategyCreatedNote({ t, strategy }) {
  const argv = (strategy.targen_argv || []).map((a, i) => (i === 0 ? 'targen' : (/[\\/]/.test(a) ? a.split(/[\\/]/).pop() : a)));
  return (
    <div className="rounded-md border border-accent/30 bg-accent/[0.05] px-3 py-2 text-tiny leading-snug space-y-1" data-strategy-created={strategy.pass}>
      <div className="flex items-center gap-2 text-xs2 font-semibold text-text-strong">
        <StrategyBadge strategy={strategy}/>{t(`strategy.created.${strategy.pass}`, { defaultValue: strategy.pass })}
      </div>
      {strategy.precondition && (
        <p className="text-text-muted">{t('strategy.created.precondition', { id: strategy.precondition.chart_id })}</p>
      )}
      {(strategy.notices || []).map((n) => (
        <p key={n.code} className="text-warn" data-strategy-notice={n.code}>{t(`strategy.notice.${n.code}`, { defaultValue: n.code })}</p>
      ))}
      {argv.length > 0 && (
        <p className="text-text-faint"><span>{t('strategy.created.argv')}</span>{' '}
          <code className="font-mono break-all" data-line="targen_argv">{argv.join(' ')}</code></p>
      )}
    </div>
  );
}

// An existing chart about to be printed (again): when it was made and printed, and its
// measurements, which a new print never replaces.
// A one-pass chart made from the guided form: the line targen ran and the colprof line the
// profile step will start from (still editable there).
function GuidedCreatedNote({ t, guided }) {
  const argv = (guided.targen_argv || []).map((a, i) => (i === 0 ? 'targen' : (/[\\/]/.test(a) ? a.split(/[\\/]/).pop() : a)));
  return (
    <div className="rounded-md border border-accent/30 bg-accent/[0.05] px-3 py-2 text-tiny leading-snug space-y-1" data-guided-created>
      <p className="font-semibold text-text-strong">{t('chart_create.guided.created')}</p>
      <p className="text-text-muted">{t('chart_create.guided.created_profile')}{' '}
        <code className="font-mono text-text-strong" data-line="planned_colprof">colprof {guided.lines?.colprof_flags}</code>
      </p>
      {argv.length > 0 && (
        <p className="text-text-faint"><span>{t('strategy.created.argv')}</span>{' '}
          <code className="font-mono break-all">{argv.join(' ')}</code></p>
      )}
    </div>
  );
}

function ExistingChartNote({ t, result }) {
  const ex = result.existing || {};
  const when = (iso) => (iso ? String(iso).replace('T', ' ').slice(0, 16) : '—');
  return (
    <div className="rounded-md border border-border-soft bg-sunken/50 px-3 py-2 text-tiny leading-snug space-y-1"
         data-existing-chart={ex.printed_at ? 'printed' : 'never_printed'}>
      <div className="flex items-center gap-2 text-xs2 font-semibold text-text-strong">
        {result.strategy && <StrategyBadge strategy={result.strategy}/>}
        <span>{t('chart_print.created_at', { date: when(ex.created_at) })}</span>
      </div>
      <p className="text-text-muted">
        {ex.printed_at ? t('chart_print.printed_at', { count: ex.n_prints || 1, date: when(ex.printed_at) })
          : t('chart_print.never_printed')}
      </p>
      {ex.printed_at && ex.n_scans > 0 && (
        <p className="text-text-muted" data-existing-scans={ex.n_scans}>{t('chart_print.scans_kept', { count: ex.n_scans })}</p>
      )}
      {!ex.printable && (
        <p className="text-danger" data-not-printable={ex.not_printable}>
          {t(`chart_print.not_printable.${ex.not_printable}`, { defaultValue: ex.not_printable })}
        </p>
      )}
    </div>
  );
}

// Pass 2 composition: what the first pass actually took.
function FirstPassPaper({ t, first }) {
  return (
    <p className="text-tiny text-text-muted leading-snug" data-first-pass-paper={first.roll_length_mm ?? ''}>
      {first.roll_length_mm != null
        ? t('strategy.paper.first_roll', { id: first.chart_id, n: first.patch_count ?? '—', mm: Math.round(first.roll_length_mm) })
        : t('strategy.paper.first_sheet', { id: first.chart_id, n: first.patch_count ?? '—', format: first.format || '—' })}
    </p>
  );
}

function Section({ n, title, children }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="w-5 h-5 rounded-full bg-accent/15 text-accent text-[10px] font-bold flex items-center justify-center">{n}</span>
        <h3 className="text-xs font-semibold text-text-strong uppercase tracking-wide">{title}</h3>
      </div>
      <div className="pl-7">{children}</div>
    </div>
  );
}

function Spinner({ label }) {
  return (
    <div className="flex items-center gap-3 py-8 justify-center text-text-muted">
      <div className="w-5 h-5 border-2 border-accent/30 border-t-accent rounded-full animate-spin"/>
      <span className="text-sm">{label}</span>
    </div>
  );
}

function ComposeView({
  t, formats, mediaKey, pickFormat, maxPatches, fmt,
  srcMode, setSrcMode, flags, setFlags, fOver, fCount, ti1Text, setTi1Text, ti1Name,
  onTi1File, presets, presetKey, setPresetKey, savePreset, loadHelp, showHelp, help,
  geStr, cChoice, setCChoice, cCustom, setCCustom, residentCurrent, customs, dev,
  validate = false,
  method, availability, onMethod, ptOptions, instrument, onInstrument, extFormats = [], mediaCheck,
  extKey = null, layoutOpts = null, layout = null, onLayoutChoice = () => {}, estimate = null,
  strategyNode = null, twoPassNode = null, methodLocked = false,
  builtProfiles = [], cFreeglaz = '', setCFreeglaz = () => {},
  guidedAvailable = false, onePassNode = null,
}) {
  const external = method === METHOD_EXTERNAL;
  return (
    <>
      {!validate && strategyNode}
      <Section n="1" title={t('measurement.method_title')}>
        <MethodChooser t={t} method={method} availability={availability} onMethod={onMethod}
                       ptOptions={ptOptions} instrument={instrument} onInstrument={onInstrument} mediaKey={extKey}
                       layoutOpts={layoutOpts} layout={layout} onLayoutChoice={onLayoutChoice} locked={methodLocked}/>
      </Section>

      <Section n="2" title={t('chart_create.layer_format')}>
        {external ? (
          <div className="space-y-1.5" data-format-external="1">
            <div className="flex items-center gap-3 flex-wrap">
              <NSelect value={extKey || extFormats[0]?.key || ''} onChange={() => {}} disabled
                       options={extFormats.length ? extFormats.map((f) => ({ value: f.key, label: f.name }))
                                                  : [{ value: '', label: '—' }]}/>
              <span className="text-tiny text-text-faint">{t('measurement.media.only', { formats: extFormats.map((f) => f.name || f.key).join(' / ') || '—' })}</span>
            </div>
            <MediaNotice t={t} check={mediaCheck}/>
            {twoPassNode ? <p className="text-tiny text-text-faint leading-snug">{t('strategy.paper.see_composition')}</p>
              : onePassNode ? <p className="text-tiny text-text-faint leading-snug">{t('chart_create.guided.see_composition')}</p>
              : estimate ? <PaperEstimate t={t} estimate={estimate}/>
                : <p className="text-tiny text-text-faint leading-snug">{t('measurement.dims_after_creation')}</p>}
          </div>
        ) : (
          <div className="flex items-center gap-3 flex-wrap">
            <NSelect value={mediaKey} onChange={pickFormat}
                     options={formats.map((f) => ({ value: f.key, label: f.name }))}/>
            {/* DERIVED columns (step-not-fixed model, native density imposed) — no more input;
                the user drives the NUMBER OF PATCHES (targen -f / .ti1) → rows */}
            {maxPatches != null && <span className="text-xs2 text-text-faint">{t('chart_create.max_patches', { n: maxPatches })}</span>}
            {fmt?.is_roll && <span className="text-tiny text-warn">{t('chart_create.roll_note')}</span>}
          </div>
        )}
      </Section>

      <Section n="3" title={t('chart_create.layer_composition')}>
        {twoPassNode || (<>
        <div data-src-mode={srcMode}>
          <Segmented value={srcMode} options={[
            ...(guidedAvailable && !validate ? [{ value: 'guided', label: t('chart_create.mode_guided') }] : []),
            { value: 'targen', label: t('chart_create.mode_targen') },
            { value: 'ti1', label: t('chart_create.mode_ti1') },
          ]} onChange={setSrcMode}/>
        </div>

        {srcMode === 'guided' && onePassNode ? <div className="mt-2">{onePassNode}</div>
        : srcMode === 'targen' ? (
          <div className="space-y-2 mt-2">
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-xs2 bg-sunken-deep text-text-muted px-2 py-1 rounded">targen -d2</span>
              <input value={flags} onChange={(e) => setFlags(e.target.value)} placeholder="-G -f 200"
                     className="flex-1 bg-sunken rounded px-2.5 py-1.5 font-mono text-xs2 text-text-strong"/>
            </div>
            {validate && (
              <p className="text-tiny text-text-faint leading-snug">{t('chart_create.validate_targen_hint')}</p>
            )}
            {fOver && (
              <div className="flex items-center gap-1.5 text-xs2 text-danger">
                <AlertTriangle size={13}/>{t('chart_create.f_over', { n: fCount, max: maxPatches })}
              </div>
            )}
            <div className="flex items-center gap-3 flex-wrap text-xs2">
              {presets.length > 0 && (
                <NSelect value="" onChange={(k) => { const p = presets.find((x) => x.key === k); if (p) setFlags(p.flags.replace(/^-d2\s*/, '')); }}
                  options={[{ value: '', label: t('chart_create.apply_preset') },
                    ...presets.map((p) => ({ value: p.key, label: p.key }))]}/>
              )}
              <button type="button" onClick={loadHelp} className="flex items-center gap-1 text-accent hover:underline">
                <HelpCircle size={13}/>{t('chart_create.targen_help')}
              </button>
              <span className="flex items-center gap-1">
                <input value={presetKey} onChange={(e) => setPresetKey(e.target.value)}
                       placeholder={t('chart_create.preset_name')}
                       className="w-28 bg-sunken rounded px-2 py-1 text-text-strong"/>
                <button type="button" onClick={savePreset} disabled={!presetKey.trim()}
                        className="flex items-center gap-1 text-text-muted hover:text-text-strong disabled:opacity-40">
                  <Save size={13}/>{t('chart_create.save_preset')}
                </button>
              </span>
            </div>
            {showHelp && (
              <pre className="max-h-[200px] overflow-auto bg-sunken/60 border border-border-soft rounded p-2 text-tiny font-mono whitespace-pre-wrap">{help || '…'}</pre>
            )}

            {/* -c reworded as usage INTENTIONS (no more resident/path jargon).
                HIDDEN in validation: no preconditioning (independence). */}
            {!validate && (
            <div className="space-y-1.5 pt-1">
              <span className="text-xs2 text-text-muted">{t('chart_create.start_from')}</span>
              <div className="space-y-1">
                <CRadio label={t('chart_create.start_refine')}
                        desc={t('chart_create.start_refine_desc')}
                        checked={cChoice === 'affiner'} disabled={!residentCurrent}
                        onClick={() => setCChoice('affiner')}/>
                {cChoice === 'affiner' && residentCurrent && (
                  <p className="pl-6 text-tiny text-text-faint">
                    {t('chart_create.precond_with', { name: residentCurrent.name })}
                  </p>
                )}
                <CRadio label={t('chart_create.start_zero')} desc={t('chart_create.start_zero_desc')}
                        checked={cChoice === 'zero'} onClick={() => setCChoice('zero')}/>
                {builtProfiles.length > 0 && (
                  <div data-precond-built={builtProfiles.length}>
                    <CRadio label={t('chart_create.start_built')} desc={t('chart_create.start_built_desc')}
                            checked={cChoice === 'freeglaz'} onClick={() => setCChoice('freeglaz')}/>
                    {cChoice === 'freeglaz' && (
                      <div className="pl-6 pt-1">
                        <NSelect value={cFreeglaz} onChange={setCFreeglaz}
                                 options={builtProfiles.map((p) => ({ value: p.id,
                                   label: [p.name, p.chart_id, (p.built_at || '').slice(0, 10)].filter(Boolean).join(' · ') }))}/>
                      </div>
                    )}
                  </div>
                )}
                {customs.length > 0 && (
                  <div>
                    <CRadio label={t('chart_create.start_custom')} desc={t('chart_create.start_custom_desc')}
                            checked={cChoice === 'custom'} onClick={() => setCChoice('custom')}/>
                    {cChoice === 'custom' && (
                      <div className="pl-6 pt-1">
                        <NSelect value={cCustom} onChange={setCCustom}
                                 options={customs.map((p) => ({ value: p.id, label: p.name }))}/>
                      </div>
                    )}
                  </div>
                )}
              </div>
              {dev && residentCurrent && (
                <p className="text-tiny text-text-faint font-mono">-c → {cChoice === 'affiner' ? residentCurrent.id : cChoice === 'custom' ? cCustom : cChoice === 'freeglaz' ? cFreeglaz : 'none'}</p>
              )}
            </div>
            )}
          </div>
        ) : (
          <div className="space-y-2 mt-2">
            <label className="flex items-center gap-2 text-xs2 text-accent cursor-pointer">
              <Upload size={14}/>{t('chart_create.import_ti1')}
              <input type="file" accept=".ti1,.txt" onChange={onTi1File} className="hidden"/>
            </label>
            {ti1Name && <span className="text-tiny text-text-faint">{ti1Name}</span>}
            <textarea value={ti1Text} onChange={(e) => setTi1Text(e.target.value)}
                      placeholder={t('chart_create.ti1_placeholder')} rows={4}
                      className="w-full bg-sunken rounded px-2.5 py-1.5 font-mono text-tiny text-text-strong"/>
          </div>
        )}
        </>)}
      </Section>

      {dev && (
        <p className="text-tiny text-text-faint italic flex items-center gap-1.5">
          <FlaskConical size={12}/>{t('chart_create.tag_note')}
        </p>
      )}
    </>
  );
}

// Measurement method: two cards (built-in HP / external) + the instrument list of the backend.
// An unavailable method stays visible, disabled, with its reason (never silently dropped).
function MethodChooser({ t, method, availability, onMethod, ptOptions, instrument, onInstrument, mediaKey = null,
                         layoutOpts = null, layout = null, onLayoutChoice = () => {}, locked = false }) {
  const cards = [
    { value: METHOD_INTEGRATED, title: t('measurement.method.integrated'), desc: t('measurement.method.integrated_desc') },
    { value: METHOD_EXTERNAL, title: t('measurement.method.external'), desc: t('measurement.method.external_desc') },
  ];
  const instruments = (ptOptions && ptOptions.instruments) || [];
  const current = instruments.find((i) => i.key === instrument);
  // the preset of the format the chart will be made for (the loaded paper's), else the first
  const preset = current?.presets?.find((p) => p.media_key === mediaKey) || current?.presets?.[0];
  return (
    <div className="space-y-2" data-method={method || ''} data-method-locked={locked ? '1' : '0'}>
      {locked && (
        <p className="text-tiny text-text-muted flex items-center gap-1" data-instrument-locked>
          <ShieldCheck size={12} className="text-accent"/>{t('strategy.compose.instrument_locked')}
        </p>
      )}
      <div className="grid grid-cols-2 gap-2">
        {cards.map((c) => {
          const av = availability?.[c.value] || {};
          const on = method === c.value;
          return (
            <button key={c.value} type="button" data-method-option={c.value} disabled={!av.available || (locked && !on)}
                    aria-pressed={on} onClick={() => onMethod(c.value)}
                    className={`text-left rounded-lg px-3 py-2 border transition-colors ${
                      on ? 'border-accent/50 bg-accent/[0.06]' : 'border-border-soft hover:border-border-strong'
                    } ${av.available ? '' : 'opacity-50 cursor-not-allowed'}`}>
              <span className="flex items-center gap-2">
                <span className={`w-3.5 h-3.5 rounded-full border-2 flex items-center justify-center flex-shrink-0 ${on ? 'border-accent' : 'border-border-strong'}`}>
                  {on && <span className="w-1.5 h-1.5 rounded-full bg-accent"/>}
                </span>
                <span className="text-xs2 font-semibold text-text-strong">{c.title}</span>
              </span>
              <span className="block text-tiny text-text-faint mt-1 leading-snug">{c.desc}</span>
              {av.note && (
                <span className={`block text-tiny mt-1 leading-snug ${av.available && av.note !== 'printtarg_missing' ? 'text-text-muted' : 'text-warn'}`}
                      data-method-note={av.note}>
                  {t(`measurement.note.${av.note}`)}
                </span>
              )}
            </button>
          );
        })}
      </div>
      {method === METHOD_EXTERNAL && instruments.length > 0 && (
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-xs2 text-text-muted">{t('measurement.instrument_label')}</span>
            <NSelect value={instrument || ''} onChange={onInstrument} disabled={locked}
                     options={instruments.map((i) => ({ value: i.key, label: instrumentLabel(t, i.key) }))}/>
          </div>
          {preset && (
            <p className="text-tiny text-text-faint leading-snug" data-preset={current.key}>
              {t('measurement.preset', { selector: current.printtarg_selector, page: Math.round(preset.page_h_mm) })}
              {preset.unlimited_strip && <> {t('measurement.preset_long_strips')}</>}
            </p>
          )}
          {layoutOpts && layout && (layoutOpts.patch_scales.length > 1 || layoutOpts.high_density) && (
            <LayoutOptions t={t} opts={layoutOpts} choice={layout} onChoice={onLayoutChoice}/>
          )}
          <p className="text-tiny text-text-faint leading-snug">{t('measurement.to_verify_physically')}</p>
        </div>
      )}
    </div>
  );
}

function useMm() {
  const { i18n } = useTranslation();
  return (v) => (v == null ? '—' : Number(v).toLocaleString(i18n.language, { minimumFractionDigits: 1, maximumFractionDigits: 2 }));
}

// printtarg's own options for an external chart (as ChromIQ presents them): reading density
// (ColorMunki -h) and patch size (-a), exclusive; the nominal patch they give; a reminder that
// only printtarg's geometry is known, not yet what the instrument reads.
function LayoutOptions({ t, opts, choice, onChoice }) {
  const mm = useMm();
  const geo = layoutGeometry(opts, choice);
  const def = opts.default_patch_scale ?? 1;
  const custom = choice.highDensity || choice.patchScale !== def;
  return (
    <div className="space-y-1.5 rounded-md border border-border-soft px-2.5 py-2" data-layout-options
         data-patch-scale={choice.patchScale} data-high-density={choice.highDensity ? '1' : '0'}>
      {opts.high_density && (
        <div className="flex items-center gap-2 flex-wrap" data-option="density">
          <span className="text-xs2 text-text-muted w-36">{t('measurement.layout_options.density')}</span>
          <span className="whitespace-nowrap">
            <Segmented value={choice.highDensity ? 'high' : 'standard'} options={[
              { value: 'standard', label: t('measurement.layout_options.density_standard') },
              { value: 'high', label: t('measurement.layout_options.density_high') },
            ]} onChange={(v) => onChoice({ patchScale: def, highDensity: v === 'high' })}/>
          </span>
        </div>
      )}
      {opts.patch_scales.length > 1 && (
        <div className="flex items-center gap-2 flex-wrap" data-option="patch_scale">
          <span className="text-xs2 text-text-muted w-36">{t('measurement.layout_options.patch_size')}</span>
          <span className="whitespace-nowrap">
            <Segmented value={String(choice.patchScale)}
                       options={opts.patch_scales.map((v) => ({ value: String(v), label: t('measurement.layout_options.scale_value', { pct: Math.round(v * 100) }) }))}
                       onChange={(v) => onChoice({ patchScale: Number(v), highDensity: false })}/>
          </span>
        </div>
      )}
      {geo && (
        <p className="text-tiny text-text-muted leading-snug" data-patch-geometry>
          {t('measurement.layout_options.geometry', { l: mm(geo.patch_length_mm), w: mm(geo.patch_width_mm), sp: mm(geo.spacer_mm) })}
          {choice.highDensity && <> {t('measurement.layout_options.stagger', { s: mm(geo.stagger_mm) })}</>}
        </p>
      )}
      {opts.high_density && (
        <p className="text-tiny text-text-faint leading-snug">
          {choice.highDensity ? t('measurement.layout_options.density_high_desc') : t('measurement.layout_options.not_combined')}
        </p>
      )}
      {custom && (
        <p className="text-tiny text-warn leading-snug flex items-start gap-1" data-not-validated>
          <AlertTriangle size={12} className="mt-0.5 flex-shrink-0"/>{t('measurement.layout_options.not_validated')}
        </p>
      )}
    </div>
  );
}

// Paper the chart will take, from printtarg's layout probes, before the TIFF is generated.
function PaperEstimate({ t, estimate }) {
  const mm = useMm();
  if (estimate.state === 'needs_count') {
    return <p className="text-tiny text-text-faint leading-snug" data-estimate="needs_count">{t('measurement.estimate.needs_count')}</p>;
  }
  if (estimate.state === 'error') {
    return (
      <div className="flex items-start gap-1.5 text-tiny text-warn leading-snug" data-estimate="error" data-estimate-code={estimate.code || ''}>
        <AlertTriangle size={12} className="mt-0.5 flex-shrink-0"/><span>{estimate.message}</span>
      </div>
    );
  }
  const d = estimate.data;
  return (
    <div className="rounded-md border border-border-soft bg-sunken/50 px-2.5 py-1.5 text-tiny leading-snug" data-estimate={estimate.state}>
      <span className="font-semibold text-text-strong">{t('measurement.estimate.title')}</span>
      {estimate.state === 'loading' && <span className="text-text-faint"> · {t('measurement.estimate.loading')}</span>}
      {d && (
        <span className={`block text-text-muted ${estimate.state === 'loading' ? 'opacity-60' : ''}`} data-estimate-roll={d.roll_length_mm ?? ''}>
          {t(d.roll_length_mm != null ? 'measurement.estimate.value_roll' : 'measurement.estimate.value_sheet', {
            patches: d.n_patches, strips: d.total_strips, per: d.patches_per_strip, blocks: d.n_pages,
            w: Math.round(d.chart_w_mm), h: Math.round(d.chart_h_mm), roll: Math.round(d.roll_length_mm ?? 0) })}
          {' '}{t('measurement.layout_options.geometry', { l: mm(d.patch_geometry?.patch_length_mm), w: mm(d.patch_geometry?.patch_width_mm), sp: mm(d.patch_geometry?.spacer_mm) })}
        </span>
      )}
    </div>
  );
}

// Patches of a created chart: as found on its pages, and the printtarg options used.
function PatchesValue({ t, p }) {
  const mm = useMm();
  const m = p.patchGeometry?.measured || {};
  const parts = [t('measurement.preview.patches_value', { l: mm(m.patch_h_mm), w: mm(m.patch_w_mm) })];
  if (p.highDensity) parts.push(t('measurement.preview.option_high_density', { s: mm(m.strip_stagger_mm) }));
  else if (p.patchScale !== 1) parts.push(t('measurement.preview.option_scale', { pct: Math.round(p.patchScale * 100) }));
  return (
    <span>
      {parts.join(' · ')}
      {p.patchGeometry?.physically_validated === false && (
        <span className="text-icc-warn-deep"> · {t('measurement.preview.not_read_yet')}</span>
      )}
    </span>
  );
}

// Loaded media vs the external chart (roll 24" on roll 1): informative before creating, the
// same check blocks printing on the preview screen.
function MediaNotice({ t, check }) {
  if (!check || check.status === 'ok') return null;
  const blocking = check.status !== 'unknown';
  return (
    <div className={`flex items-start gap-1.5 text-tiny leading-snug rounded-md px-2.5 py-1.5 ${
      blocking ? 'text-warn bg-icc-warn/10' : 'text-text-muted bg-sunken/60'}`} data-media-check={check.status}>
      <AlertTriangle size={12} className="mt-0.5 flex-shrink-0"/>
      <span>{t(`measurement.media.${check.status}`, {
        width: Math.round(check.width_mm || 0), source: check.source || '',
        expected: (check.expected || []).map((w) => Math.round(w)).join(' / ') || '610',
        formats: (check.accepted || []).join(' / ') || '—', chart: check.chart || '', loaded: check.loaded || '' })}</span>
    </div>
  );
}

function CRadio({ label, desc, checked, disabled, onClick }) {
  return (
    <button type="button" onClick={disabled ? undefined : onClick} disabled={disabled}
            className={`w-full text-left flex items-start gap-2 rounded-md px-2 py-1.5 transition-colors ${
              checked ? 'bg-accent/[0.05]' : 'hover:bg-sunken/60'} ${disabled ? 'opacity-40' : ''}`}>
      <span className={`mt-0.5 w-3.5 h-3.5 rounded-full border-2 flex items-center justify-center flex-shrink-0 ${
        checked ? 'border-accent' : 'border-border-strong'}`}>
        {checked && <span className="w-1.5 h-1.5 rounded-full bg-accent"/>}
      </span>
      <span>
        <span className="text-xs2 font-medium text-text-strong">{label}</span>
        {desc && <span className="block text-tiny text-text-faint">{desc}</span>}
      </span>
    </button>
  );
}

// "look before printing" screen: preview PNG + USEFUL real metrics.
// No action here ("Print" button in the FIXED footer). The PNG is height-bounded
// (object-contain) so it never pushes the footer out of view.
function PreviewView({ t, result, mediaKey, geStr, dev }) {
  const tag = result.tag || {};
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-ok">
        <CheckCircle2 size={18}/><span className="font-semibold">{result.existing ? t('chart_print.title') : t('chart_create.created')}</span>
      </div>
      <img src={api.chartPreviewUrl(result.chart_id)} alt="preview"
           className="w-full max-h-[42vh] object-contain rounded-md border border-border-soft bg-white"
           onError={(e) => { e.currentTarget.style.display = 'none'; }}/>
      <dl className="text-xs2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-text-muted">{t('chart_create.r_id')}</dt>
        <dd className="font-mono text-text-strong">{result.chart_id}</dd>
        <dt className="text-text-muted">{t('chart_create.r_patches')}</dt>
        <dd>{result.n_patches} · {result.cols} {t('chart_create.cols_short')} × {result.nrows}</dd>
        <dt className="text-text-muted">{t('chart_create.gloss')}</dt>
        <dd>{(result.media_key || mediaKey).toUpperCase()} · GE {_geLabel(geStr)}</dd>
        {dev && (
          <>
            <dt className="text-text-muted">{t('chart_create.r_feasibility')}</dt>
            <dd>gap {result.feasibility?.gap_mm} mm</dd>
            <dt className="text-text-muted">{t('chart_create.r_tag')}</dt>
            <dd className="text-ok">{t('chart_create.r_resident')} GE={_geLabel(tag.gloss_enhancer)} <span className="text-text-faint">({tag.icc_name || '—'})</span></dd>
          </>
        )}
      </dl>
      {(result.warnings || []).map((w, i) => (
        <div key={i} className="flex items-start gap-1.5 text-tiny text-warn">
          <Info size={12} className="mt-0.5"/><span>{w}</span>
        </div>
      ))}
      <div className="flex items-start gap-2 text-xs2 text-text-strong bg-sunken/60 rounded-md px-3 py-2.5">
        <Info size={14} className="mt-0.5 text-accent"/><span>{t('chart_create.preview_hint')}</span>
      </div>
    </div>
  );
}

// Preview of an external-instrument (printtarg) chart: what the generator computed — strips,
// orientation, physical size, roll consumed, resident/GE — and what is still to be proven by a
// physical reading. No columns, rows or scan feasibility: those belong to the honeycomb chart.
function PrinttargPreviewView({ t, result, family, printBlockedBy, dev }) {
  const p = printtargSummary(result);
  const row = (label, value, attr) => (
    <>
      <dt className="text-text-muted">{label}</dt>
      <dd className="text-text-strong" data-field={attr}>{value}</dd>
    </>
  );
  return (
    <div className="space-y-3" data-preview-layout="printtarg">
      <div className="flex items-center gap-2 text-ok">
        <CheckCircle2 size={18}/><span className="font-semibold">{result.existing ? t('chart_print.title') : t('chart_create.created')}</span>
        <MeasurementBadge chart={result}/>
      </div>
      <img src={api.chartPreviewUrl(result.chart_id)} alt="preview"
           className="w-full max-h-[42vh] object-contain rounded-md border border-border-soft bg-white"
           onError={(e) => { e.currentTarget.style.display = 'none'; }}/>
      <dl className="text-xs2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {row(t('chart_create.r_id'), <span className="font-mono">{result.chart_id}</span>, 'chart_id')}
        {row(t('measurement.preview.instrument'), instrumentLabel(t, p.instrument), 'instrument')}
        {row(t('chart_create.r_patches'), p.nPatches, 'n_patches')}
        {row(t('measurement.preview.strips'), t('measurement.preview.strips_value', { count: p.nStrips, per: p.patchesPerStrip }), 'strips')}
        {p.patchGeometry && row(t('measurement.preview.patches'), <PatchesValue t={t} p={p}/>, 'patches')}
        {row(p.onSheet ? t('measurement.preview.layout_sheet') : t('measurement.preview.layout'),
             !p.stripsCrossTheRoll ? t('measurement.preview.layout_pages')
               : p.onSheet ? t('measurement.preview.layout_transverse_sheet', { side: t(`measurement.side.${p.patch1Side || 'right'}`) })
                 : t('measurement.preview.layout_transverse', { side: t(`measurement.side.${p.patch1Side || 'right'}`) }),
             'roll_layout')}
        {p.nBlocks > 1 && row(t('measurement.preview.blocks'), p.nBlocks, 'blocks')}
        {row(t('measurement.preview.size'), t('measurement.preview.size_value', { w: Math.round(p.widthMm), h: Math.round(p.lengthMm) }), 'size')}
        {p.rollLengthMm != null && row(t('measurement.preview.roll'), t('measurement.preview.roll_value', { mm: Math.round(p.rollLengthMm) }), 'roll_length')}
        {row(t('measurement.preview.resident'),
             t('measurement.preview.resident_value', { name: p.iccName || '—', ge: _geLabel(p.ge) }), 'resident')}
      </dl>
      {p.generatorWarnings.map((w, i) => (
        <div key={`g${i}`} className="flex items-start gap-1.5 text-tiny text-text-muted" data-generator-warning={w.code}>
          <Info size={12} className="mt-0.5 flex-shrink-0"/>
          <span>{t(`measurement.generator_warning.${w.code}`, { count: w.count, defaultValue: w.code })}</span>
        </div>
      ))}
      {p.warnings.map((w, i) => (
        <div key={i} className="flex items-start gap-1.5 text-tiny text-warn">
          <Info size={12} className="mt-0.5"/><span>{w}</span>
        </div>
      ))}
      {printBlockedBy && <MediaNotice t={t} check={printBlockedBy}/>}
      <div className="grid grid-cols-2 gap-2 text-tiny leading-snug">
        <div className="rounded-md border border-border-soft bg-sunken/50 px-3 py-2 space-y-0.5" data-block="established">
          <div className="font-semibold text-text-strong">{t('measurement.preview.established_title')}</div>
          <p className="text-text-muted">{t('measurement.preview.established')}</p>
        </div>
        <div className="rounded-md border border-icc-warn/40 bg-icc-warn/10 px-3 py-2 space-y-0.5" data-block="to_verify">
          <div className="font-semibold text-text-strong">{t('measurement.preview.to_verify_title')}</div>
          <p className="text-text-muted">{t('measurement.preview.to_verify')}</p>
          {family !== 'z9' && <p className="text-text-muted">{t('measurement.preview.color_not_proven')}</p>}
        </div>
      </div>
      {dev && (
        <p className="text-tiny text-text-faint font-mono">layout=printtarg · roll_layout={p.rollLayout} · {p.stripOrder} · patch 1 {p.patch1Side}</p>
      )}
    </div>
  );
}

// After printing an external chart: the external measurement panel (chartread-fgK session),
// NO built-in scan action. The chart also waits in the library.
function PrintedExternalView({ t, chart, onClose, onOpenLibrary }) {
  const m = chartMeasurement(chart);
  return (
    <div className="space-y-3" data-printed-layout="printtarg">
      <div className="flex items-center gap-2 text-ok">
        <Printer size={18}/><span className="font-semibold">{t('measurement.printed.title')}</span>
        <MeasurementBadge chart={chart}/>
      </div>
      {chart?.chart_id && (
        <img src={api.chartPreviewUrl(chart.chart_id)} alt="preview"
             className="w-full max-h-[40vh] object-contain rounded-md border border-border-soft bg-white"
             onError={(e) => { e.currentTarget.style.display = 'none'; }}/>
      )}
      <div className="flex items-start gap-2 text-xs2 text-text-strong bg-sunken/60 rounded-md px-3 py-2.5">
        <Info size={14} className="mt-0.5 text-accent flex-shrink-0"/>
        <span>{t('measurement.printed.body', { instrument: instrumentLabel(t, m.instrument) })}</span>
      </div>
      {chart?.chart_id && <ExternalMeasurePanel chartId={chart.chart_id} instrument={m.instrument}/>}
      <div className="flex items-center justify-end gap-2 pt-1">
        <button type="button" onClick={onOpenLibrary} data-action="open-library"
                className="text-xs2 font-medium border border-border-soft text-text-strong px-3 py-1.5 rounded-md hover:bg-sunken">
          {t('measurement.printed.open_library')}
        </button>
        <button type="button" onClick={onClose}
                className="text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press">
          {t('common.close')}
        </button>
      </div>
    </div>
  );
}

function PrintedView({ t, chartId, printInfo, onScan, onClose }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-ok">
        <Printer size={18}/><span className="font-semibold">{t('chart_create.printed_title')}</span>
      </div>
      {chartId && (
        <img src={api.chartPreviewUrl(chartId)} alt="preview"
             className="w-full rounded-md border border-border-soft bg-white"
             onError={(e) => { e.currentTarget.style.display = 'none'; }}/>
      )}
      <div className="flex items-start gap-2 text-xs2 text-text-strong bg-sunken/60 rounded-md px-3 py-2.5">
        <Info size={14} className="mt-0.5 text-accent"/><span>{t('chart_create.printed_next')}</span>
      </div>
      <div className="flex items-center justify-end gap-2 pt-1">
        <button type="button" onClick={onClose} className="text-xs2 text-text-muted hover:text-text-strong px-3 py-1.5">{t('chart_create.later')}</button>
        <button type="button" onClick={onScan}
                className="flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press">
          <ScanLine size={14}/>{t('chart_create.go_to_scan')}
        </button>
      </div>
    </div>
  );
}

function SelectView({ t, charts, paperName, onPick, scannedMode = false, onComposeNew,
                      composeLabel, onDelete, onLighten, onConcordance }) {
  if (charts == null) return <Spinner t={t} label={t('common.loading')}/>;
  const ComposeBtn = onComposeNew ? (
    <button type="button" onClick={onComposeNew}
            className="w-full flex items-center justify-center gap-1.5 text-xs2 font-semibold border border-dashed border-accent/50 text-accent rounded-lg px-3 py-2.5 hover:bg-accent/5">
      <Grid3x3 size={14}/>{composeLabel || t('chart_create.create')}
    </button>
  ) : null;
  if (charts.length === 0) {
    return (
      <div className="space-y-3">
        <div className="text-center py-6 space-y-1">
          <p className="text-sm text-text-muted">
            {scannedMode ? t('chart_create.no_scanned_validation') : t('chart_create.no_printed_charts')}
          </p>
          <p className="text-tiny text-text-faint">{t('chart_create.no_printed_charts_hint', { paper: paperName })}</p>
        </div>
        {ComposeBtn}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <p className="text-xs2 text-text-muted">
        {scannedMode ? t('chart_create.pick_scanned_chart') : t('chart_create.pick_printed_chart')}
      </p>
      {ComposeBtn}
      {charts.map((c) => (
        <ChartCard key={c.chart_id} chart={c} onClick={onPick}
          info={<>
            {c.strategy && <><StrategyBadge strategy={c.strategy}/>{' '}</>}
            {c.patch_count != null && `${c.patch_count} ${t('chart_create.patches_short')} · `}
            {c.printed_at}{c.scanned && <span className="text-ok"> · {t('chart_create.already_scanned')}</span>}
            {c.lightened && <span className="text-text-faint"> · {t('chart_create.lightened_badge')}</span>}
          </>}
          actions={<>
            {onConcordance && c.n_scans >= 2 && (
              <button type="button" title={t('scan.delta.title_report', { count: c.n_scans })}
                      onClick={(e) => { e.stopPropagation(); onConcordance(c); }}
                      className="flex items-center gap-1 px-2 h-7 rounded-md text-tiny font-medium text-accent hover:bg-accent/10">
                <GitCompare size={13}/> ΔE ({c.n_scans})
              </button>
            )}
            {onLighten && !c.lightened && (
              <button type="button" title={t('chart_create.lighten_btn')}
                      onClick={(e) => { e.stopPropagation(); onLighten(c); }}
                      className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-strong hover:bg-sunken">
                <Archive size={14}/>
              </button>
            )}
            {onDelete && (
              <button type="button" title={t('chart_create.delete_btn')}
                      onClick={(e) => { e.stopPropagation(); onDelete(c); }}
                      className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted hover:text-danger hover:bg-danger/10">
                <Trash2 size={14}/>
              </button>
            )}
          </>}/>
      ))}
    </div>
  );
}

function ScanReadyView({ chart }) {
  // Compact preview (30vh): in scanready we don't inspect the chart (already printed),
  // we reposition it. The repositioning instruction lives in the callout below
  // (scanready body), so no redundant hint here → the screen fits without scrolling.
  return (
    <img src={api.chartPreviewUrl(chart.chart_id)} alt="preview"
         className="w-full max-h-[30vh] object-contain rounded-md border border-border-soft bg-white"
         onError={(e) => { e.currentTarget.style.display = 'none'; }}/>
  );
}

function ScannedView({ t, scanInfo }) {
  // Scan info + "next step" hint. The build CONTROLS (colprof + base) now live
  // in ProfileBuildPanel (shared wizard + Measurements) → rendered right after.
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-ok">
        <CheckCircle2 size={18}/><span className="font-semibold">{t('chart_create.scan_done')}</span>
      </div>
      <dl className="text-xs2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-text-muted">{t('chart_create.patches_short')}</dt><dd>{scanInfo?.n_patches}</dd>
        <dt className="text-text-muted">{t('chart_create.bands')}</dt><dd>{scanInfo?.bands}</dd>
      </dl>
      <div className="flex items-start gap-2 text-xs2 text-text-strong bg-sunken/60 rounded-md px-3 py-2.5">
        <Info size={14} className="mt-0.5 text-accent"/><span>{t('chart_create.scan_done_next')}</span>
      </div>
    </div>
  );
}

function ProfiledView({ t, profInfo, onClose, dev }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-ok">
        <CheckCircle2 size={18}/><span className="font-semibold">{t('chart_create.profile_done')}</span>
      </div>
      <dl className="text-xs2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-text-muted">{t('chart_create.profile_icc')}</dt>
        <dd className="font-mono text-text-strong break-all">{profInfo?.icc}</dd>
        {profInfo?.ranged_icc_path && (
          <>
            <dt className="text-text-muted">{t('chart_create.profile_ranged')}</dt>
            <dd className="font-mono text-text-faint break-all">{profInfo.ranged_icc_path}</dd>
          </>
        )}
        {dev && profInfo?.icc_size_bytes != null && (
          <>
            <dt className="text-text-muted">{t('chart_create.profile_size')}</dt>
            <dd>{profInfo.icc_size_bytes} o</dd>
          </>
        )}
      </dl>

      <div className="flex items-start gap-2 text-xs2 text-text-strong bg-sunken/60 rounded-md px-3 py-2.5">
        <Info size={14} className="mt-0.5 text-accent"/>
        <span>{profInfo?.installable ? t('chart_create.profile_done_next')
                                     : t('chart_create.profile_done_local')}</span>
      </div>
      <div className="flex items-center justify-end pt-1">
        <button type="button" onClick={onClose}
                className="text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press">{t('common.close')}</button>
      </div>
    </div>
  );
}

// "scanned" screen in validation mode: no colprof options (we don't build);
// reminder of the target profile + "Validate" action in the FIXED footer.
function ValidateReadyView({ t, scanInfo, profileName }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-ok">
        <CheckCircle2 size={16}/>
        <span className="text-sm font-medium">{t('chart_create.scan_done')}</span>
      </div>
      {scanInfo?.n_patches != null && (
        <p className="text-xs2 text-text-muted">{t('chart_create.scan_patches', { n: scanInfo.n_patches })}</p>
      )}
      <div className="rounded-md bg-sunken/60 border border-border-soft px-3 py-2.5 text-xs2 text-text-strong">
        {t('chart_create.validate_target', { name: profileName })}
      </div>
      <p className="text-tiny text-text-faint leading-snug">{t('chart_create.validate_ready_hint')}</p>
    </div>
  );
}

// Independent validation report (profcheck b) — reuses ProfcheckReport (scope b).
function ValidatedView({ t, report, profileName }) {
  if (!report) return <Spinner t={t} label={t('chart_create.validating')}/>;
  return (
    <div className="space-y-3">
      <ValidationIdentityNotice report={report}/>
      <ProfcheckReport report={report} scope="independent" profileName={profileName}/>
    </div>
  );
}

// Validation measured with another instrument or condition than the build: allowed, reported
// as a warning (the result then also contains the gap between instruments) — never an error.
function ValidationIdentityNotice({ report }) {
  const { t } = useTranslation();
  const n = validationIdentityNotice(report);
  if (!n) return null;
  const build = n.buildInstruments.map((k) => instrumentLabel(t, k)).join(', ') || t('measurement.value_unknown');
  const validation = n.validationInstrument ? instrumentLabel(t, n.validationInstrument) : t('measurement.value_unknown');
  return (
    <div className="rounded-md border border-icc-warn/40 bg-icc-warn/10 px-3 py-2 text-xs2 space-y-1" data-validation-identity={n.status}>
      {n.warnings.map((w) => (
        <div key={w} className="flex items-start gap-1.5 text-text-strong" data-warning={w}>
          <AlertTriangle size={13} className="mt-0.5 flex-shrink-0 text-icc-warn"/>
          <span>{t(`measurement.validation.${w}`, { build, validation })}</span>
        </div>
      ))}
    </div>
  );
}
