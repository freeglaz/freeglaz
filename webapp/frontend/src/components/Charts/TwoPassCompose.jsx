import { useState } from 'react';
import { AlertTriangle, Info, Lock } from 'lucide-react';
import Segmented from '../ui/Segmented.jsx';
import { Collapsible, ComposeHeader, NumberField, Row } from './ComposeFields.jsx';
import { FINAL_QUALITIES, NEUTRAL_CHOICES, SMOOTHING_CHOICES } from '../../lib/profilingStrategy.js';

// Composition of one pass of the two-pass adaptive profiling, inside the existing wizard (the
// method, instrument, format, density and patch size sections stay the wizard's own).
// Essentials first; less common settings in "Advanced"; the Argyll lines the SERVER will run in
// "Technical details" (returned by POST /api/profiling-strategies/compose, never rebuilt here).

export default function TwoPassCompose({
  t, passId, form, setForm, choices, composed, composeError = null, run = null,
  maxPatches = null, paperNode = null, otherPaperNode = null, totalNode = null, dev = false,
}) {
  const [advanced, setAdvanced] = useState(false);
  const [details, setDetails] = useState(false);
  const isPass2 = passId === 'pass2';
  const first = run?.first || null;
  const inter = first?.intermediate || null;
  const lines = composed || [];
  const l1 = lines.find((p) => p.pass === 'pass1');
  const l2 = lines.find((p) => p.pass === 'pass2');
  const gamuts = choices?.source_gamuts || [];
  const vcs = choices?.viewing_conditions || [];
  const bounds = choices?.bounds?.chart?.patches || [50, 5000];
  const cur = isPass2 ? form.pass2Patches : form.pass1Patches;
  const over = maxPatches != null && Number(cur) > maxPatches;
  const pass2Over = !isPass2 && maxPatches != null && Number(form.pass2Patches) > maxPatches;
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const smoothingCustom = !SMOOTHING_CHOICES.includes(Number(form.smoothing));

  return (
    <div className="space-y-3" data-two-pass-compose={passId}>
      <ComposeHeader attr={passId} title={t(isPass2 ? 'strategy.compose.pass2_title' : 'strategy.compose.pass1_title')}
                     intro={t(isPass2 ? 'strategy.compose.pass2_intro' : 'strategy.compose.pass1_intro')}/>

      {/* essentials */}
      <div className="space-y-2">
        {!isPass2 && (
          <Row label={t('strategy.compose.pass1_patches')}>
            <NumberField value={form.pass1Patches} min={bounds[0]} max={bounds[1]} attr="pass1_patches"
                         onChange={(v) => set({ pass1Patches: v })}/>
          </Row>
        )}
        {isPass2 && first && (
          <Row label={t('strategy.compose.pass1_done')}>
            <span className="text-xs2 text-text-strong" data-field="pass1_done">
              {t('strategy.compose.pass1_done_value', { n: first.patch_count ?? '—', id: first.chart_id })}
            </span>
          </Row>
        )}
        <Row label={t('strategy.compose.pass2_patches')}
             hint={!isPass2 ? t('strategy.compose.pass2_patches_hint') : null}>
          <NumberField value={form.pass2Patches} min={bounds[0]} max={bounds[1]} attr="pass2_patches"
                       onChange={(v) => set({ pass2Patches: v })}/>
        </Row>
        <Row label={t('strategy.compose.neutral')} hint={t('strategy.compose.neutral_hint')}>
          <span className="whitespace-nowrap">
            <Segmented value={String(form.neutral)}
                       options={NEUTRAL_CHOICES.map((v) => ({ value: String(v), label: v.toFixed(2) }))}
                       onChange={(v) => set({ neutral: Number(v) })}/>
          </span>
        </Row>
        <Row label={t('strategy.compose.final_quality')}>
          <span className="whitespace-nowrap">
            <Segmented value={form.finalQuality}
                       options={FINAL_QUALITIES.map((q) => ({ value: q, label: t(`strategy.quality.${q}`) }))}
                       onChange={(v) => set({ finalQuality: v })}/>
          </span>
        </Row>
        <Row label={t('strategy.compose.precondition')}>
          <span className="text-xs2 text-text-strong flex items-start gap-1" data-field="precondition">
            {isPass2 && <Lock size={12} className="mt-0.5 text-text-faint flex-shrink-0"/>}
            {isPass2
              ? t('strategy.compose.precondition_pass2', { id: first?.chart_id || run?.run || '—',
                                                         date: (inter?.created_at || '').slice(0, 16).replace('T', ' ') || '—' })
              : t('strategy.compose.precondition_pass1')}
          </span>
        </Row>
        {isPass2 && (
          <p className="text-tiny text-text-faint leading-snug">{t('strategy.compose.precondition_note')}</p>
        )}
      </div>

      {over && (
        <div className="flex items-center gap-1.5 text-xs2 text-danger" data-capacity="over">
          <AlertTriangle size={13}/>{t('strategy.compose.over_capacity', { n: cur, max: maxPatches })}
        </div>
      )}
      {pass2Over && (
        <div className="flex items-start gap-1.5 text-tiny text-text-muted" data-capacity="pass2_over">
          <Info size={12} className="mt-0.5 flex-shrink-0"/>{t('strategy.compose.pass2_over_capacity', { n: form.pass2Patches, max: maxPatches })}
        </div>
      )}

      {(paperNode || otherPaperNode) && (
        <div className="space-y-1" data-strategy-paper>
          {paperNode && (
            <div>
              <div className="text-tiny font-semibold uppercase tracking-wide text-text-faint">
                {t(isPass2 ? 'strategy.paper.pass2' : 'strategy.paper.pass1')}
              </div>
              {paperNode}
            </div>
          )}
          {otherPaperNode && (
            <div>
              <div className="text-tiny font-semibold uppercase tracking-wide text-text-faint">
                {t(isPass2 ? 'strategy.paper.pass1_used' : 'strategy.paper.pass2_planned')}
              </div>
              {otherPaperNode}
            </div>
          )}
          {totalNode}
        </div>
      )}

      <Collapsible title={t('strategy.compose.advanced')} open={advanced} onToggle={() => setAdvanced((v) => !v)} attr="advanced">
        {!isPass2 && (
          <>
            <Row label={t('strategy.compose.ofps_pass1')}>
              <span className="whitespace-nowrap">
                <Segmented value={form.pass1Ofps} options={[
                  { value: 'G', label: t('strategy.compose.ofps_G') }, { value: 'Q', label: t('strategy.compose.ofps_Q') }]}
                  onChange={(v) => set({ pass1Ofps: v })}/>
              </span>
            </Row>
            <Row label={t('strategy.compose.grey_steps')} hint={t('strategy.compose.grey_steps_hint')}>
              <NumberField value={form.pass1Grey} min={0} max={100} attr="grey_steps" onChange={(v) => set({ pass1Grey: v })}/>
            </Row>
            <Row label={t('strategy.compose.smoothing')} hint={t('strategy.compose.smoothing_hint')}>
              <span className="whitespace-nowrap">
                <Segmented value={smoothingCustom ? 'custom' : String(Number(form.smoothing))}
                           options={[...SMOOTHING_CHOICES.map((v) => ({ value: String(v), label: v.toFixed(1) })),
                                     { value: 'custom', label: t('strategy.compose.other') }]}
                           onChange={(v) => set({ smoothing: v === 'custom' ? 1.0 : Number(v) })}/>
              </span>
              {smoothingCustom && (
                <NumberField value={form.smoothing} min={0.05} max={5} step={0.05} width="w-20" attr="smoothing"
                             onChange={(v) => set({ smoothing: v })}/>
              )}
            </Row>
          </>
        )}
        <Row label={t('strategy.compose.ofps_pass2')}>
          <span className="whitespace-nowrap">
            <Segmented value={form.pass2Ofps} options={[
              { value: 'G', label: t('strategy.compose.ofps_G') }, { value: 'Q', label: t('strategy.compose.ofps_Q') }]}
              onChange={(v) => set({ pass2Ofps: v })}/>
          </span>
        </Row>
        <Row label={t('strategy.compose.neutral_steps')} hint={t('strategy.compose.neutral_steps_hint')}>
          <NumberField value={form.neutralSteps} min={0} max={100} attr="neutral_steps" onChange={(v) => set({ neutralSteps: v })}/>
        </Row>
        <Row label={t('strategy.compose.source_gamut')} hint={t('strategy.compose.source_gamut_hint')}>
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
        <p className="text-tiny text-text-faint leading-snug">{t('strategy.compose.fwa_later')}</p>
      </Collapsible>

      <Collapsible title={t('strategy.compose.details')} open={details} onToggle={() => setDetails((v) => !v)} attr="details">
        {composeError && (
          <p className="text-tiny text-danger leading-snug" data-compose-error={composeError.code || ''}>{composeError.message}</p>
        )}
        {!composeError && !composed && <p className="text-tiny text-text-faint">{t('common.loading')}</p>}
        {l1 && l2 && (
          <div className="space-y-1.5 text-tiny" data-strategy-lines>
            {[
              [t('strategy.lines.pass1_chart'), `targen -d2 ${l1.targen_flags}`, 'pass1_targen'],
              [t('strategy.lines.intermediate'), `colprof ${l1.colprof_flags}`, 'intermediate_colprof'],
              [t('strategy.lines.pass2_chart'), `targen -d2 ${l2.targen_flags} -c ${t('strategy.lines.intermediate_file')}`, 'pass2_targen'],
              [t('strategy.lines.final'), `colprof ${l2.colprof_flags}`, 'final_colprof'],
            ].map(([label, line, attr]) => (
              <div key={attr}>
                <div className="text-text-muted">{label}</div>
                <code className="font-mono text-text-strong break-all" data-line={attr}>{line}</code>
              </div>
            ))}
            {l2.source_gamut && (
              <p className="text-text-muted" data-field="source_gamut_file">
                {t('strategy.lines.gamut_alias', { alias: l2.source_gamut.alias, file: l2.source_gamut.file || '—' })}
              </p>
            )}
            <p className="text-text-faint">{t('strategy.lines.not_validated')}</p>
          </div>
        )}
        {dev && run && <p className="text-tiny text-text-faint font-mono">run={run.run} · step={run.next_step}</p>}
      </Collapsible>
    </div>
  );
}
