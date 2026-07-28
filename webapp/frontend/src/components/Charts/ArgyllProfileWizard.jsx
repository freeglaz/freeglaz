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
import ProfcheckReport from '../Profiles/ProfcheckReport.jsx';
import AllPatchesModal from '../Profiles/AllPatchesModal.jsx';
import ScanDeltaCard from './ScanDeltaCard.jsx';   // extracted (shared wizard + Measurements tab)
import ChartCard from './ChartCard.jsx';           // extracted chart-card (shared)
import ScanKeepList from './ScanKeepList.jsx';     // keep/exclude toggle (shared)

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

export default function ArgyllProfileWizard({ open, onClose, paper, ge, mode = 'print', onDone, profile, initialChart = null }) {
  const { t } = useTranslation();
  const loadedPaper = useLoadedPaper();   // live status.paper → default chart format (roll if a roll is loaded)
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
  const [srcMode, setSrcMode] = useState('targen');         // targen | ti1
  const [flags, setFlags] = useState('-G -f 200');
  const [ti1Text, setTi1Text] = useState('');
  const [ti1Name, setTi1Name] = useState('');
  const [preconds, setPreconds] = useState([]);
  // KISS: default = 'zero' (Start from scratch, NO preconditioning) — no silent
  // inheritance of the resident as -c. The user picks 'affiner' explicitly for a
  // passe-2 refinement (and sees which profile is used, below).
  const [cChoice, setCChoice] = useState('zero');           // zero | affiner | custom
  const [cCustom, setCCustom] = useState('');               // id path:… if custom
  const [presets, setPresets] = useState([]);
  const [presetKey, setPresetKey] = useState('');
  const [help, setHelp] = useState(null);
  const [showHelp, setShowHelp] = useState(false);
  const [confirmPrint, setConfirmPrint] = useState(false);
  const [result, setResult] = useState(null);               // created chart (preview before printing)

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
  const formatDefaultedRef = useRef(false);   // format default applied once per open (see effect)
  const [scanDelta, setScanDelta] = useState(null);   // mini inter-scan ΔE report (scanned screen)
  const [chartDetail, setChartDetail] = useState(null);  // scans+kept (keep/exclude toggle)
  const [concordanceDelta, setConcordanceDelta] = useState(null);  // report opened from the chart view
  const [deltaAllOpen, setDeltaAllOpen] = useState(false);

  // reset on opening
  useEffect(() => {
    if (!open) return;
    // DIRECT opening on a chart ("Open in the scan flow"
    // from Measurements) → we skip the filtered list, straight to scanready, at any
    // stage (bypasses the list's printed:true filter, without touching it).
    // ADDITIVE path: without initialChart, behavior UNCHANGED (select + list).
    const directScan = mode === 'scan' && !!initialChart;
    setPhase(directScan ? 'scanready' : (mode === 'scan' || isValidate) ? 'select' : 'compose');
    setError(null); setBusy(false); setSelected(directScan ? initialChart : null); setResult(null);
    setScanInfo(null); setProfInfo(null); setPrintInfo(null); setConfirmPrint(false);
    setScanProg(null); setCooldownSec(0); setNScans(0);
    setProfileBase('average'); setChartMovedAsk(false);
    setCChoice('zero');                 // reset precond default (Start from scratch) each open
    formatDefaultedRef.current = false; // re-arm the format default (applied once formats load)
    setClcWarn(null);
    setConcordanceDelta(null); setDeltaAllOpen(false);
    if (scanPollRef.current) clearInterval(scanPollRef.current);
    if (cooldownRef.current) clearInterval(cooldownRef.current);
    // Validation: INDEPENDENT distribution by default = quasi-random device (-q),
    // != the deterministic OFPS of creation + moderate count (we verify, we don't build).
    // Editable. The non-overlap vs creation is cross-checked in the report.
    if (mode === 'validate') setFlags('-q -f 64');
  }, [open, mode, initialChart]);

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
  }, [open, mode]);

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

  const fmt = useMemo(() => formats.find((f) => f.key === mediaKey), [formats, mediaKey]);
  const maxPatches = fmt?.max_patches ?? null;
  const fCount = srcMode === 'targen' ? _extractF(flags) : null;
  const fOver = fCount != null && maxPatches != null && fCount > maxPatches;
  const residentCurrent = preconds.find((p) => p.id === `resident:${geStr}`);
  const customs = preconds.filter((p) => p.kind === 'custom');

  function resolveCProfile() {
    if (cChoice === 'zero') return 'none';
    if (cChoice === 'custom') return cCustom || 'none';
    return residentCurrent ? residentCurrent.id : 'none';   // affiner
  }

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
  const canCreate = mediaid && !fOver && !busy
    && (srcMode === 'ti1' ? ti1Text.trim() : flags.trim());

  async function doCreate() {
    setBusy(true); setError(null);
    try {
      const body = {
        media_key: mediaKey,   // cols derived on the backend (step-not-fixed model), no longer sent
        paper_mediaid: mediaid, paper_name: paperName,
        gloss_enhancer: geStr,
        // validation: NEVER -c (independence); purpose='validation' on the server
        c_profile: isValidate ? 'none' : resolveCProfile(),
        purpose: isValidate ? 'validation' : 'profiling',
      };
      if (srcMode === 'ti1') body.ti1_text = ti1Text;
      else body.targen_flags = `-d2 ${flags}`.trim();
      const created = await api.createChart(body);
      setResult(created); setPhase('preview');
    } catch (e) { setError(e.message); setPhase('compose'); }
    finally { setBusy(false); }
  }

  async function doPrint(skipClc = false) {
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
      const info = await api.printChart(result.chart_id, { quality: 'HIGH' });
      setSelected(result); setPrintInfo(info); setPhase('printed');
    } catch (e) { setError(e.message); setPhase('preview'); }
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
      if (e?.status === 409 && e?.detail?.code === 'session_active_other_chart') {
        // Inter-chart lock → offer a DIRECT "finish the blocking session" action
        // (modal below) instead of a dead-end text refusal.
        setSessionBlockedBy(e.detail.chart_id);
      } else if (msg.includes('429')) {             // SOL cooldown (server hard guard)
        const st = await api.getScanStatus(selected.chart_id).catch(() => null);
        _startCooldown(st?.cooldown_remaining_s || 30);
        setError(t('scan.roll.cooldown_sol'));
      } else if (e?.status === 409 || msg.includes('409')) {  // scan already in progress
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
      setError((e?.message || '').includes('409')
        ? t('scan.roll.less_than_two_scans')
        : (e?.message || t('scan.roll.concordance_unavailable')));
    }
  }

  // ─── Profile (local, colprof) ───
  // onConflict: null (collision → pop-up) | 'replace' | 'keep_both' (replay from the pop-up).
  async function doProfile(onConflict = null) {
    setBusy(true); setError(null); setPhase('profiling');
    try {
      const body = {};
      if (colprofFlags.trim()) body.colprof_flags = colprofFlags.trim();
      const nKept = (chartDetail?.scans || []).filter((s) => s.kept).length;
      if (nKept >= 2) body.profile_base = profileBase;   // average (KEPT set) / last
      if (extraSources.extra_chart_ids.length) body.extra_chart_ids = extraSources.extra_chart_ids;
      if (extraSources.source_profiles.length) body.source_profiles = extraSources.source_profiles;
      if (extraSources.name) body.name = extraSources.name;     // custom name (null if auto default)
      // Defensive type guard (the button is already wired arrow, but we align
      // with buildProfile to harden against a future onClick={handler} rewiring).
      if (typeof onConflict === 'string') body.on_conflict = onConflict;  // replay after pop-up choice
      setConflict(null); setNameNotice(null);
      // Build = background job: starts + polls until done|error (info = same shape as before).
      const info = await api.buildProfileAndWait(selected.chart_id, body);
      setProfInfo(info); setPhase('profiled');
      onDone?.(info);
    } catch (e) {
      if (e?.status === 409 && e.detail?.error === 'name_conflict') {  // collision → 3-choice pop-up
        setConflict(e.detail);
      } else if (e?.status === 422) {                           // length → readable message BELOW the field
        setNameNotice(t('mesures.profile_name_maxlen'));
      } else if (e?.status === 400) {                           // invalid name → backend detail BELOW the field
        setNameNotice(e.message);
      } else { setError(e.message); }
      setPhase('scanned');
    }
    finally { setBusy(false); }
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
    const cid = selected?.chart_id || initialChart?.chart_id;
    if (cid && phase !== 'scanning') api.abandonScan(cid).catch(() => {});
    onClose?.();
  }

  if (!open) return null;

  // Source of the ΔE report: the card/table serves the scanned screen (scanDelta) OR the opening
  // from the chart view (concordanceDelta). Mutually exclusive (different phases).
  const activeDelta = concordanceDelta || scanDelta;
  const activeDeltaChartId = concordanceDelta?._chartId || selected?.chart_id;
  // Number of KEPT scans (the toggle excludes) → drives the base selector + the average.
  const nKeptScanned = chartDetail ? chartDetail.scans.filter((s) => s.kept).length : nScans;
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
              <AlertTriangle size={14} className="mt-0.5"/><span>{error}</span>
            </div>
          )}

          {phase === 'compose' && (
            <ComposeView
              t={t} formats={formats} mediaKey={mediaKey} pickFormat={pickFormat}
              maxPatches={maxPatches}
              fmt={fmt} srcMode={srcMode} setSrcMode={setSrcMode} flags={flags} setFlags={setFlags}
              fOver={fOver} fCount={fCount} ti1Text={ti1Text} setTi1Text={setTi1Text}
              ti1Name={ti1Name} onTi1File={onTi1File} presets={presets} presetKey={presetKey}
              setPresetKey={setPresetKey} savePreset={savePreset} loadHelp={loadHelp}
              showHelp={showHelp} help={help} geStr={geStr}
              cChoice={cChoice} setCChoice={setCChoice} cCustom={cCustom} setCCustom={setCCustom}
              residentCurrent={residentCurrent} customs={customs} dev={DEV}
              validate={isValidate}/>
          )}

          {phase === 'preview' && result && (
            <PreviewView t={t} result={result} mediaKey={mediaKey} geStr={geStr} dev={DEV}/>
          )}

          {phase === 'printing' && <Spinner t={t} label={t('chart_create.printing')}/>}

          {phase === 'printed' && (
            <PrintedView t={t} chartId={selected?.chart_id} printInfo={printInfo}
                         onScan={() => setPhase('scanready')} onClose={handleClose}/>
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

          {phase === 'scanready' && selected && (
            <div className="space-y-2.5">
              <ScanReadyView chart={selected}/>
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

              {/* SHARED build controls (base + colprof + command). Button OMITTED here → the
                  wizard footer keeps its [Profile] (doProfile) unchanged (no regression). */}
              <ProfileBuildPanel
                nIncluded={nKeptScanned}
                base={profileBase} setBase={setProfileBase}
                colprofFlags={colprofFlags} setColprofFlags={setColprofFlags}
                colprofPresets={colprofPresets}
                colprofPresetKey={colprofPresetKey} setColprofPresetKey={setColprofPresetKey}
                saveColprofPreset={saveColprofPreset}
                loadColprofHelp={loadColprofHelp} showColprofHelp={showColprofHelp}
                colprofHelp={colprofHelp}
                chartId={selected?.chart_id} onSelectionChange={setExtraSources}
                nameNotice={nameNotice}/>

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
            <button type="button" onClick={doCreate} disabled={!canCreate}
                    className="flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md disabled:opacity-40 hover:bg-accent-press">
              <Grid3x3 size={14}/>{busy ? t('chart_create.creating') : t('chart_create.create')}
            </button>
          </div>
        )}

        {/* FIXED footer of the preview screen: the "Print" action is always visible
            (never below the fold), even with a large PNG in the scrollable area. */}
        {phase === 'preview' && (
          <div className="px-6 py-4 border-t border-border-soft flex items-center justify-end gap-2">
            <button type="button" onClick={handleClose}
                    className="text-xs2 text-text-muted hover:text-text-strong px-3 py-1.5">{t('common.cancel')}</button>
            <button type="button" onClick={() => setConfirmPrint(true)} disabled={busy}
                    className="flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press disabled:opacity-50">
              <Printer size={14}/>{t('chart_create.print_btn')}
            </button>
          </div>
        )}

        {/* FIXED footer of the "ready to scan" screen: "Scan" action always
            visible (same principle as the preview screen — chart preview at top). */}
        {phase === 'scanready' && selected && (
          <div className="px-6 py-4 border-t border-border-soft flex items-center justify-end gap-2">
            <button type="button" onClick={handleClose}
                    className="text-xs2 text-text-muted hover:text-text-strong px-3 py-1.5">{t('common.cancel')}</button>
            <button type="button" onClick={doScan} disabled={busy || cooldownSec > 0}
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
            {!isValidate && (
              <button type="button" onClick={() => { setError(null); setPhase('scanready'); }} disabled={busy}
                      className="flex items-center gap-1.5 text-xs2 font-medium border border-border-soft text-text-muted px-3 py-1.5 rounded-md hover:text-text-strong hover:bg-sunken disabled:opacity-50">
                <ScanLine size={14}/>{t('scan.roll.scan_again')}
              </button>
            )}
            <button type="button" onClick={() => (isValidate ? doValidate() : doProfile())} disabled={busy}
                    className="flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press disabled:opacity-50">
              {isValidate ? <ShieldCheck size={14}/> : <FlaskConical size={14}/>}
              {isValidate ? t('chart_create.validate_btn') : t('chart_create.profile_btn')}
            </button>
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
          message={t('chart_create.print_confirm', { paper: paperName, format: mediaKey.toUpperCase(), ge: _geLabel(geStr) })}
          confirmLabel={t('chart_create.print_confirm_btn')}
          cancelLabel={t('common.cancel')}
          confirmKind="primary"
          onConfirm={() => doPrint()}
          onCancel={() => setConfirmPrint(false)}/>
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
        message={conflict ? t('mesures.profile_conflict_body', { name: conflict.name }) : ''}
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
}) {
  return (
    <>
      <Section n="1" title={t('chart_create.layer_format')}>
        <div className="flex items-center gap-3 flex-wrap">
          <NSelect value={mediaKey} onChange={pickFormat}
                   options={formats.map((f) => ({ value: f.key, label: f.name }))}/>
          {/* DERIVED columns (step-not-fixed model, native density imposed) — no more input;
              the user drives the NUMBER OF PATCHES (targen -f / .ti1) → rows */}
          {maxPatches != null && <span className="text-xs2 text-text-faint">{t('chart_create.max_patches', { n: maxPatches })}</span>}
          {fmt?.is_roll && <span className="text-tiny text-warn">{t('chart_create.roll_note')}</span>}
        </div>
      </Section>

      <Section n="2" title={t('chart_create.layer_composition')}>
        <Segmented value={srcMode} options={[
          { value: 'targen', label: t('chart_create.mode_targen') },
          { value: 'ti1', label: t('chart_create.mode_ti1') },
        ]} onChange={setSrcMode}/>

        {srcMode === 'targen' ? (
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
                <p className="text-tiny text-text-faint font-mono">-c → {cChoice === 'affiner' ? residentCurrent.id : cChoice === 'custom' ? cCustom : 'none'}</p>
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
      </Section>

      {dev && (
        <p className="text-tiny text-text-faint italic flex items-center gap-1.5">
          <FlaskConical size={12}/>{t('chart_create.tag_note')}
        </p>
      )}
    </>
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
        <CheckCircle2 size={18}/><span className="font-semibold">{t('chart_create.created')}</span>
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
        <dd>{mediaKey.toUpperCase()} · GE {_geLabel(geStr)}</dd>
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
  return <ProfcheckReport report={report} scope="independent" profileName={profileName}/>;
}
