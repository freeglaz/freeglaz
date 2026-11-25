import { useState } from 'react';
import { AlertTriangle, PenLine } from 'lucide-react';
import Segmented from '../ui/Segmented.jsx';
import { Collapsible, ComposeHeader, NumberField, Row } from './ComposeFields.jsx';
import { FINAL_QUALITIES, NEUTRAL_CHOICES } from '../../lib/profilingStrategy.js';

// Composition of a one-pass chart from the guided form: the layout and the controls of the
// two-pass composition (TwoPassCompose), for one chart and its profile. Not a strategy: no run,
// no pass. The -c is the wizard's own menu (startNode); the Argyll lines are the SERVER's
// (POST /api/profiling-strategies/compose-one-pass), never rebuilt here. The free targen line
// stays one click away (onEditLine), prefilled with the composed line.

export default function OnePassCompose({
  t, form, setForm, choices, composed, composeError = null, maxPatches = null,
  startNode = null, startHint = null, preconditioned = true, cLabel = null,
  paperNode = null, onEditLine = null,
}) {
  const [advanced, setAdvanced] = useState(false);
  const [details, setDetails] = useState(false);
  const gamuts = choices?.source_gamuts || [];
  const vcs = choices?.viewing_conditions || [];
  const bounds = choices?.bounds?.chart?.patches || [50, 5000];
  const over = maxPatches != null && Number(form.patches) > maxPatches;
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const needsC = t('chart_create.guided.needs_c');

  return (
    <div className="space-y-3" data-one-pass-compose data-preconditioned={preconditioned ? '1' : '0'}>
      <ComposeHeader attr="one_pass" title={t('chart_create.guided.title')} intro={t('chart_create.guided.intro')}/>

      {/* essentials */}
      <div className="space-y-2">
        <Row label={t('chart_create.guided.patches')}>
          <NumberField value={form.patches} min={bounds[0]} max={bounds[1]} attr="patches"
                       onChange={(v) => set({ patches: v })}/>
        </Row>
        <Row label={t('chart_create.guided.start')} hint={startHint}>
          {startNode}
        </Row>
        <Row label={t('strategy.compose.neutral')}
             hint={preconditioned ? t('chart_create.guided.neutral_hint') : needsC}>
          <span className="whitespace-nowrap" data-field="neutral" data-disabled={preconditioned ? '0' : '1'}>
            <Segmented value={String(form.neutral)} disabled={!preconditioned}
                       options={NEUTRAL_CHOICES.map((v) => ({ value: String(v), label: v.toFixed(2) }))}
                       onChange={(v) => set({ neutral: Number(v) })}/>
          </span>
        </Row>
        <Row label={t('chart_create.guided.quality')} hint={t('chart_create.guided.quality_hint')}>
          <span className="whitespace-nowrap" data-field="quality">
            <Segmented value={form.quality}
                       options={FINAL_QUALITIES.map((q) => ({ value: q, label: t(`strategy.quality.${q}`) }))}
                       onChange={(v) => set({ quality: v })}/>
          </span>
        </Row>
      </div>

      {over && (
        <div className="flex items-center gap-1.5 text-xs2 text-danger" data-capacity="over">
          <AlertTriangle size={13}/>{t('strategy.compose.over_capacity', { n: form.patches, max: maxPatches })}
        </div>
      )}

      {paperNode && (
        <div className="space-y-1" data-strategy-paper>
          <div className="text-tiny font-semibold uppercase tracking-wide text-text-faint">{t('chart_create.guided.paper')}</div>
          {paperNode}
        </div>
      )}

      <Collapsible title={t('strategy.compose.advanced')} open={advanced} onToggle={() => setAdvanced((v) => !v)} attr="advanced">
        <Row label={t('chart_create.guided.ofps')}>
          <span className="whitespace-nowrap">
            <Segmented value={form.ofps} options={[
              { value: 'G', label: t('strategy.compose.ofps_G') }, { value: 'Q', label: t('strategy.compose.ofps_Q') }]}
              onChange={(v) => set({ ofps: v })}/>
          </span>
        </Row>
        <Row label={t('chart_create.guided.grey_steps')} hint={t('strategy.compose.grey_steps_hint')}>
          <NumberField value={form.grey} min={0} max={100} attr="grey_steps" onChange={(v) => set({ grey: v })}/>
        </Row>
        <Row label={t('chart_create.guided.neutral_steps')}
             hint={preconditioned ? t('chart_create.guided.neutral_steps_hint') : needsC}>
          <NumberField value={form.neutralSteps} min={0} max={100} attr="neutral_steps" disabled={!preconditioned}
                       onChange={(v) => set({ neutralSteps: v })}/>
        </Row>
        <Row label={t('chart_create.guided.source_gamut')} hint={t('strategy.compose.source_gamut_hint')}>
          <select value={form.gamut} onChange={(e) => set({ gamut: e.target.value })} data-field="source_gamut"
                  className="text-xs2 bg-sunken text-text-strong rounded px-2 py-1">
            {gamuts.map((g) => (
              <option key={g.alias} value={g.alias}>
                {g.alias === 'none' ? t('strategy.compose.gamut_none') : `${g.alias}${g.file ? ` → ${g.file}` : ''}`}
              </option>
            ))}
          </select>
        </Row>
        {form.gamut !== 'none' && (
          <Row label={t('strategy.compose.viewing')} hint={t('strategy.compose.viewing_hint')}>
            {[['vcIn', 'viewcond_in'], ['vcOut', 'viewcond_out']].map(([k, attr]) => (
              <select key={k} value={form[k]} onChange={(e) => set({ [k]: e.target.value })} data-field={attr}
                      className="text-xs2 bg-sunken text-text-strong rounded px-2 py-1 max-w-[260px]">
                {vcs.map((v) => <option key={v.code} value={v.code}>{`${v.code} — ${v.description}`}</option>)}
              </select>
            ))}
          </Row>
        )}
        <p className="text-tiny text-text-faint leading-snug">{t('chart_create.guided.fwa_later')}</p>
      </Collapsible>

      <Collapsible title={t('strategy.compose.details')} open={details} onToggle={() => setDetails((v) => !v)} attr="details">
        {composeError && (
          <p className="text-tiny text-danger leading-snug" data-compose-error={composeError.code || ''}>{composeError.message}</p>
        )}
        {!composeError && !composed && <p className="text-tiny text-text-faint">{t('common.loading')}</p>}
        {composed && (
          <div className="space-y-1.5 text-tiny" data-strategy-lines>
            {[
              [t('chart_create.guided.line_chart'), `targen -d2 ${composed.targen_flags}${cLabel ? ` -c ${cLabel}` : ''}`, 'targen'],
              [t('chart_create.guided.line_profile'), `colprof ${composed.colprof_flags}`, 'colprof'],
            ].map(([label, line, attr]) => (
              <div key={attr}>
                <div className="text-text-muted">{label}</div>
                <code className="font-mono text-text-strong break-all" data-line={attr}>{line}</code>
              </div>
            ))}
            {composed.source_gamut && (
              <p className="text-text-muted" data-field="source_gamut_file">
                {t('strategy.lines.gamut_alias', { alias: composed.source_gamut.alias, file: composed.source_gamut.file || '—' })}
              </p>
            )}
            <p className="text-text-faint">{t('chart_create.guided.profile_line_note')}</p>
            {onEditLine && (
              <button type="button" data-action="edit-targen-line" onClick={() => onEditLine(composed.targen_flags)}
                      className="flex items-center gap-1 text-accent hover:underline">
                <PenLine size={12}/>{t('chart_create.guided.edit_line')}
              </button>
            )}
          </div>
        )}
      </Collapsible>
    </div>
  );
}
