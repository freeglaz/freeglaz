import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, ArrowRight, CheckCircle2, Layers, Loader2, Lock } from 'lucide-react';
import * as api from '../../api/client.js';
import Segmented from '../ui/Segmented.jsx';
import BuildPreviewSummary from './BuildPreviewSummary.jsx';
import BuildConfirmModal from './BuildConfirmModal.jsx';
import { useBuildPreview, buildWithPreview } from '../../hooks/useBuildPreview.js';
import { buildRequestBody, summarizePreview } from '../../lib/buildPreview.js';
import { errorText } from '../../lib/errorText.js';
import {
  SMOOTHING_CHOICES, STRATEGY_TWO_PASS, hasFwa, intermediateState, variantSources, withFwa,
} from '../../lib/profilingStrategy.js';

// The run a chart belongs to (two-pass adaptive profiling), shared by the Measurements drawer
// and the wizard. Everything comes from GET /api/profiling-strategies/runs/{run}, derived from
// the charts by the server (resume after closing freeglaz).
//  - first pass: its INTERMEDIATE profile (never installed, never offered for printing) and
//    "Prepare pass 2", which builds it through the usual preview → confirmation → build
//    contract when needed, then opens the pass-2 composition;
//  - final pass: the variant (pass 2 alone by default, passes 1 + 2 for comparison), the
//    source gamut really used, FWA allowed or not (with the reason). The build itself is the
//    shared build panel below, which these choices preset.

const fmtDate = (s) => (s || '').slice(0, 16).replace('T', ' ');

export default function StrategyRunPanel({
  chartId, identity, nIncluded = 0, stateStamp = '', refreshKey = '',
  onPreparePass2, onOpenChart, onBuilt,
  colprofFlags = '', setColprofFlags, onVariant, preview = null,
}) {
  const { t } = useTranslation();
  const st = identity?.strategy || null;
  const runId = st?.run || null;
  const [reload, setReload] = useState(0);
  const key = runId ? `${runId}|${chartId}|${refreshKey}|${reload}` : null;
  const [res, setRes] = useState({ key: null, run: null, error: null });
  useEffect(() => {
    if (!key) return undefined;
    let alive = true;
    api.getStrategyRun(runId)
      .then((r) => { if (alive) setRes({ key, run: r, error: null }); })
      .catch((e) => { if (alive) setRes({ key, run: null, error: errorText(e, t) }); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runId is in key
  }, [key, t]);
  // the previous answer for the same run stays shown while it is read again (no flicker)
  const run = res.run && res.run.run === runId ? res.run : null;
  const error = res.key === key ? res.error : null;

  if (!st || st.id !== STRATEGY_TWO_PASS) return null;
  const isFirst = st.pass === 'pass1';
  const index = isFirst ? 1 : 2;

  return (
    <div className="space-y-2.5 rounded-md border border-accent/30 bg-accent/[0.04] px-3 py-2.5" data-strategy-run={runId}
         data-strategy-pass={st.pass}>
      <div className="flex items-start gap-2">
        <Layers size={15} className="text-accent mt-0.5 flex-shrink-0"/>
        <div className="flex-1 min-w-0">
          <div className="text-xs2 font-semibold text-text-strong">{t('strategy.run.title', { index, total: 2 })}</div>
          <div className="text-tiny text-text-faint font-mono truncate">{t('strategy.run.run_id', { id: runId })}</div>
        </div>
      </div>
      {error && <p className="text-tiny text-danger">{error}</p>}
      {run && <RunSteps t={t} run={run} chartId={chartId} onOpenChart={onOpenChart}/>}
      {run && isFirst && (
        <IntermediateBlock t={t} run={run} chartId={chartId} identity={identity} nIncluded={nIncluded}
                           stateStamp={stateStamp} onPreparePass2={onPreparePass2}
                           onBuilt={() => { setReload((n) => n + 1); onBuilt?.(); }}/>
      )}
      {run && !isFirst && (
        <FinalBlock t={t} run={run} chartId={chartId} identity={identity} colprofFlags={colprofFlags}
                    setColprofFlags={setColprofFlags} onVariant={onVariant} preview={preview}/>
      )}
    </div>
  );
}

// The run's charts and its next step (derived by the server).
function RunSteps({ t, run, chartId, onOpenChart }) {
  const first = run.first;
  const link = (id) => (onOpenChart && id !== chartId
    ? <button type="button" onClick={() => onOpenChart(id)} className="font-mono text-accent hover:underline">{id}</button>
    : <span className="font-mono">{id}</span>);
  return (
    <div className="space-y-1 text-tiny leading-snug" data-run-steps>
      <div className="flex items-center gap-1.5 text-text-strong" data-next-step={run.next_step}>
        <ArrowRight size={12} className="text-accent flex-shrink-0"/>
        <span><strong>{t('strategy.run.next')}</strong> {t(`strategy.step.${run.next_step}`)}</span>
      </div>
      <ul className="space-y-0.5 text-text-muted pl-4 list-disc">
        <li>
          {t('strategy.run.pass1')} {first ? link(first.chart_id) : <em>{t('strategy.run.first_missing')}</em>}
          {first && <> · {t('strategy.run.chart_state', { patches: first.patch_count ?? '—', scans: first.n_included })}</>}
          {first?.intermediate?.built && <> · {t(`strategy.intermediate.state_short.${first.intermediate.state === 'stale' ? 'stale' : 'ready'}`)}</>}
        </li>
        {(run.later || []).map((c, i) => (
          <li key={c.chart_id} data-later-chart={c.chart_id}>
            {t('strategy.run.pass2_attempt', { n: i + 1 })} {link(c.chart_id)}
            {' · '}{t('strategy.run.chart_state', { patches: c.patch_count ?? '—', scans: c.n_included })}
            {c.precondition?.build_id && <> · {t('strategy.run.made_from', { date: fmtDate(c.precondition.built_at) || c.precondition.build_id })}</>}
            {c.final_built && <> · {t('strategy.run.final_built')}</>}
          </li>
        ))}
      </ul>
    </div>
  );
}

// First pass: the intermediate profile and "Prepare pass 2".
function IntermediateBlock({ t, run, chartId, identity, nIncluded, stateStamp, onPreparePass2, onBuilt }) {
  const frozen = identity?.strategy?.params?.pass1?.profile?.smoothing ?? 2.0;
  const [smoothing, setSmoothing] = useState(frozen);
  const [flags, setFlags] = useState(identity?.strategy?.lines?.colprof_flags || '');
  const [composeErr, setComposeErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState(null);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const [confirmKey, setConfirmKey] = useState(null);
  const inFlight = useRef(false);

  // the line the SERVER composes for this smoothing (single source of the Argyll text)
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      api.composeStrategy({ strategy: STRATEGY_TWO_PASS, run: run.run, params: { pass1: { profile: { smoothing: Number(smoothing) } } } })
        .then((d) => { if (alive) { setFlags(d.passes.find((p) => p.pass === 'pass1')?.colprof_flags || ''); setComposeErr(null); } })
        .catch((e) => { if (alive) setComposeErr(errorText(e, t)); });
    }, 300);
    return () => { alive = false; clearTimeout(timer); };
  }, [smoothing, run.run, t]);

  const body = useMemo(() => buildRequestBody({ nKept: nIncluded, profileBase: 'average', colprofFlags: flags }),
                       [nIncluded, flags]);
  const state = intermediateState(run.first, flags);
  const needsBuild = state !== 'ready';
  const preview = useBuildPreview(chartId, body, { enabled: needsBuild && !!flags && nIncluded > 0, stateStamp, nonce });
  const custom = !SMOOTHING_CHOICES.includes(Number(smoothing));

  async function prepare(confirmed = false) {
    if (inFlight.current) return;
    setError(null);
    if (!needsBuild) { onPreparePass2?.({ run: run.run, buildId: run.first.intermediate.build_id }); return; }
    if (preview.status !== 'ready' || preview.data?.would_be_refused) return;
    if (!confirmed && summarizePreview(preview.data).needsConfirmation) { setConfirmKey(preview.key); return; }
    setConfirmKey(null);
    inFlight.current = true; setBusy(true); setPhase('preparing');
    try {
      const info = await buildWithPreview(chartId, body, preview.data, setPhase);
      onBuilt?.();
      onPreparePass2?.({ run: run.run, buildId: info?.build_id });
    } catch (e) {
      setError(errorText(e, t)); setNonce((n) => n + 1);
    } finally { inFlight.current = false; setBusy(false); setPhase(null); }
  }

  return (
    <div className="space-y-2 pt-2 border-t border-border-soft" data-intermediate-block data-intermediate-state={state}>
      <div className="text-tiny font-semibold uppercase tracking-wide text-text-faint">{t('strategy.intermediate.title')}</div>
      <p className="text-tiny text-text-muted leading-snug">{t('strategy.intermediate.purpose')}</p>
      <p className="text-tiny text-text-strong" data-field="intermediate_state">
        {t(`strategy.intermediate.state.${state}`, { date: fmtDate(run.first?.intermediate?.created_at) })}
      </p>
      {nIncluded >= 2 && <p className="text-tiny text-text-faint">{t('strategy.intermediate.acquisitions', { n: nIncluded })}</p>}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs2 text-text-muted">{t('strategy.compose.smoothing')}</span>
        <span className="whitespace-nowrap">
          <Segmented value={custom ? 'custom' : String(Number(smoothing))}
                     options={[...SMOOTHING_CHOICES.map((v) => ({ value: String(v), label: v.toFixed(1) })),
                               { value: 'custom', label: t('strategy.compose.other') }]}
                     onChange={(v) => setSmoothing(v === 'custom' ? 1.0 : Number(v))}/>
        </span>
        {custom && (
          <input type="number" min={0.05} max={5} step={0.05} value={smoothing} data-field="smoothing"
                 onChange={(e) => setSmoothing(e.target.value === '' ? '' : Number(e.target.value))}
                 className="w-20 bg-sunken rounded px-2 py-1 text-xs2 font-mono text-text-strong"/>
        )}
      </div>
      <p className="text-tiny text-text-faint leading-snug">{t('strategy.compose.smoothing_hint')}</p>
      <div className="text-tiny text-text-faint">
        {t('mesures.build_will_run')}{' '}
        <code className="font-mono text-text-muted break-all" data-line="intermediate_colprof">colprof {flags}</code>
      </div>
      {composeErr && <p className="text-tiny text-danger">{composeErr}</p>}
      {needsBuild && <BuildPreviewSummary preview={preview}/>}
      {error && <p className="text-tiny text-danger" data-intermediate-error>{error}</p>}
      <button type="button" onClick={() => prepare()} data-action="prepare-pass2"
              disabled={busy || nIncluded < 1 || !flags || (needsBuild && (preview.status !== 'ready' || !!preview.data?.would_be_refused))}
              className="inline-flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-4 py-1.5 rounded-md hover:bg-accent-press disabled:opacity-50">
        {busy ? <><Loader2 size={13} className="animate-spin"/> {t(`strategy.intermediate.phase.${phase || 'preparing'}`, { defaultValue: t('mesures.building') })}</>
              : <><ArrowRight size={13}/> {t('strategy.intermediate.prepare')}</>}
      </button>
      {needsBuild && nIncluded >= 1 && (
        <p className="text-tiny text-text-faint leading-snug">{t('strategy.intermediate.prepare_hint')}</p>
      )}
      {nIncluded < 1 && <p className="text-tiny text-text-faint">{t('strategy.intermediate.needs_scan')}</p>}
      {confirmKey && confirmKey === preview.key && preview.status === 'ready' && (
        <BuildConfirmModal summary={summarizePreview(preview.data)}
          onCancel={() => setConfirmKey(null)} onConfirm={() => prepare(true)}/>
      )}
    </div>
  );
}

// Final pass: variant, source gamut, viewing conditions, FWA. Presets the shared build panel.
function FinalBlock({ t, run, chartId, identity, colprofFlags, setColprofFlags, onVariant, preview }) {
  const [variant, setVariant] = useState('pass2');
  const composed = (run.passes_composed || []).find((p) => p.pass === 'pass2');
  const gate = run.fwa?.[variant] || null;
  const fwaOn = hasFwa(colprofFlags);
  const me = (run.later || []).find((c) => c.chart_id === chartId);
  const builds = (me?.builds || []).filter((b) => b.role === 'final');
  const pre = identity?.precondition;

  useEffect(() => { onVariant?.(variantSources(variant, run.run)); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [variant, run.run]);

  const planned = preview?.status === 'ready' ? preview.data?.strategy?.variant : null;
  return (
    <div className="space-y-2 pt-2 border-t border-border-soft" data-final-block>
      <div className="text-tiny font-semibold uppercase tracking-wide text-text-faint">{t('strategy.final.title')}</div>
      {pre && (
        <p className="text-tiny text-text-muted leading-snug flex items-start gap-1" data-field="precondition_used">
          <Lock size={11} className="mt-0.5 flex-shrink-0"/>
          {t('strategy.final.made_from', { id: pre.chart_id, date: fmtDate(pre.built_at) || pre.build_id })}
        </p>
      )}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs2 text-text-muted">{t('strategy.final.variant')}</span>
        <span className="whitespace-nowrap">
          <Segmented value={variant} onChange={(v) => (v === 'pass1_pass2' && !run.variant_available ? null : setVariant(v))}
                     options={[{ value: 'pass2', label: t('strategy.variant.pass2') },
                               { value: 'pass1_pass2', label: t('strategy.variant.pass1_pass2') }]}/>
        </span>
      </div>
      <p className="text-tiny text-text-faint leading-snug">
        {t(variant === 'pass1_pass2' ? 'strategy.final.variant_pass1_pass2_hint' : 'strategy.final.variant_pass2_hint')}
      </p>
      {!run.variant_available && <p className="text-tiny text-warn">{t('strategy.final.variant_unavailable')}</p>}
      {planned && (
        <p className="text-tiny text-text-strong" data-planned-variant={planned}>
          {t('strategy.final.planned', { variant: t(`strategy.variant.${planned}`, { defaultValue: planned }) })}
        </p>
      )}
      {composed?.source_gamut && (
        <p className="text-tiny text-text-muted leading-snug" data-field="source_gamut_used">
          {t('strategy.final.gamut', { alias: composed.source_gamut.alias, file: composed.source_gamut.file || '—' })}
        </p>
      )}
      {composed?.viewing_conditions && (
        <p className="text-tiny text-text-muted leading-snug" data-field="viewing_conditions">
          {t('strategy.final.viewing', {
            vin: composed.viewing_conditions.viewcond_in?.code, din: composed.viewing_conditions.viewcond_in?.description,
            vout: composed.viewing_conditions.viewcond_out?.code, dout: composed.viewing_conditions.viewcond_out?.description })}
        </p>
      )}
      <label className={`flex items-start gap-2 text-xs2 ${gate?.allowed ? 'cursor-pointer text-text-strong' : 'text-text-faint'}`}
             data-fwa={gate?.allowed ? 'allowed' : (gate?.reason || 'unknown')}>
        <input type="checkbox" className="accent-accent mt-0.5" disabled={!gate?.allowed} checked={fwaOn && !!gate?.allowed}
               onChange={(e) => setColprofFlags?.(withFwa(colprofFlags, e.target.checked))}/>
        <span>
          {t('strategy.final.fwa')}
          {!gate?.allowed && <span className="block text-tiny">{t(`strategy.fwa.${gate?.reason || 'identity_unknown'}`)}</span>}
        </span>
      </label>
      {fwaOn && !gate?.allowed && (
        <p className="text-tiny text-danger flex items-start gap-1"><AlertTriangle size={12} className="mt-0.5"/>{t('strategy.final.fwa_refused')}</p>
      )}
      <p className="text-tiny text-text-faint leading-snug">{t('strategy.lines.not_validated')}</p>
      {builds.length > 0 && (
        <ul className="text-tiny text-text-muted space-y-0.5" data-final-builds>
          {builds.map((b) => (
            <li key={b.build_id} className="flex items-center gap-1.5">
              <CheckCircle2 size={11} className="text-ok flex-shrink-0"/>
              <span className="font-mono truncate">{b.ranged?.filename || b.label || b.build_id}</span>
              <span>· {t(`strategy.variant.${b.variant}`, { defaultValue: b.variant || '—' })}</span>
              {b.current && <span className="text-text-faint">· {t('strategy.final.current')}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
