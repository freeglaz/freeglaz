import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, CheckCircle2, Info, Loader2, Pause, Play, Send, Usb, X, XCircle } from 'lucide-react';
import Stepper from '../wizards/shared/Stepper.jsx';
import StripMap from './StripMap.jsx';
import { errorText } from '../../lib/errorText.js';
import { instrumentLabel } from '../../lib/measurementMethod.js';
import {
  STEPS, allowed, canCreate, conditionChoices, createPresenceWatch, durability, presenceState, problem, progress,
  promptView, publishChecks, recentEvents, startBlock, stepOf, watchesPresence,
} from '../../lib/externalSession.js';

/**
 * External measurement wizard (chartread-fgK): Prepare → Calibrate → Read → Verify and publish.
 * The step shown is the backend's state (stepOf); every button is an action the snapshot
 * allows, sent with the snapshot's state and seq. There is no button that starts a reading:
 * the instrument's own button does. Closing the window does not touch the measurement.
 *
 * Instrument presence: while the window is open on a chart without a live run, the snapshot is
 * read again every 2 s (createPresenceWatch), so plugging or unplugging shows without a reload.
 * "Start" is offered only while the chart's instrument is confirmed present (startBlock): it
 * waits while it is absent, another one or several are plugged in, the USB devices cannot be
 * listed or freeglaz does not answer, and comes back by itself once it is detected. The backend
 * still decides and refuses a start without it. The watch stops when the window closes or a run
 * starts.
 *
 * Props: open, onClose, chartId, session (useExternalSession result).
 */
export default function ExternalMeasureWizard({ open, onClose, chartId, session }) {
  const { t } = useTranslation();
  const { snap, error, notice, busy } = session;
  const [filter, setFilter] = useState(null);
  // A confirmation belongs to the state it was asked in: any new state drops it (no effect needed).
  const [pending, setPending] = useState(null);        // {what: 'abandon' | 'accept' | {correct}, seq}
  const [correctPick, setCorrectPick] = useState('');
  // Last answer of the presence watch ({ok}); null while the first one is awaited ("checking").
  const [check, setCheck] = useState(null);
  const watching = open && watchesPresence(snap);
  const { poll } = session;
  useEffect(() => {
    if (!watching) return undefined;
    const watch = createPresenceWatch({ check: poll, onResult: (ok) => setCheck({ ok }) });
    watch.start();
    return () => { watch.stop(); setCheck(null); };
  }, [watching, poll]);

  useEffect(() => {
    if (!open) return undefined;
    // Capture phase + stopPropagation: Esc closes this window only, not the one under it.
    // (Closing never touches the measurement; no key ever reaches chartread.)
    const h = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose?.(); } };
    window.addEventListener('keydown', h, true);
    return () => window.removeEventListener('keydown', h, true);
  }, [open, onClose]);
  const confirm = pending && pending.seq === snap?.state_seq ? pending.what : null;
  const setConfirm = (what) => setPending(what == null ? null : { what, seq: snap?.state_seq });

  if (!open) return null;
  const step = stepOf(snap);
  const steps = STEPS.map((id) => ({ id, label: t(`external_measure.steps.${id}`) }));
  const ctx = snap?.context || {};
  const instrument = instrumentLabel(t, ctx.model);
  const pb = problem(snap);
  const can = (a) => allowed(snap, a) && !busy;
  const presence = presenceState(snap, check);
  const block = allowed(snap, 'start') ? startBlock(presence) : null;
  const blockText = block ? t(`external_measure.start_blocked.${block}`, { instrument }) : undefined;

  const footer = [];
  const act = (id, label, onClick, kind = 'secondary', icon = null, title = undefined) =>
    footer.push({ id, label, onClick, kind, icon, title });
  if (step === 'prepare') {
    if (allowed(snap, 'create')) act('create', t('external_measure.prepare.create'), () => session.create(filter),
                                     canCreate(snap, filter) ? 'primary' : 'disabled');
    if (allowed(snap, 'start')) act('start', t('external_measure.prepare.start'), session.start, can('start') && !block ? 'primary' : 'disabled', Play, blockText);
  } else if (step === 'calibrate') {
    if (allowed(snap, 'abort_calibration')) act('abort', t('external_measure.calibrate.abort'), () => session.command('abort_calibration'));
    if (allowed(snap, 'calibrate')) act('calibrate', t('external_measure.calibrate.confirm'), () => session.command('calibrate'), can('calibrate') ? 'primary' : 'disabled');
  } else if (step === 'read') {
    if (allowed(snap, 'abandon')) act('abandon', t('external_measure.read.abandon'), () => setConfirm('abandon'), 'danger-ghost');
    if (allowed(snap, 'pause')) act('pause', t('external_measure.read.pause'), () => session.command('pause'), 'secondary', Pause);
    if (allowed(snap, 'start')) act('start', t('external_measure.read.resume'), session.start, can('start') && !block ? 'primary' : 'disabled', Play, blockText);
  } else if (step === 'publish') {
    if (allowed(snap, 'abandon')) act('abandon', t('external_measure.read.abandon'), () => setConfirm('abandon'), 'danger-ghost');
    if (allowed(snap, 'finalize')) {
      const ok = publishChecks(snap).every((c) => c.ok);
      act('finalize', t('external_measure.publish.publish'), session.finalize, ok && can('finalize') ? 'primary' : 'disabled', Send);
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[120] bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4"
         role="dialog" aria-modal="true" aria-label={t('external_measure.title')} data-external-wizard={step}>
      <div className="bg-bg border border-border-soft rounded-[14px] shadow-2xl w-[780px] max-w-full max-h-full overflow-hidden flex flex-col">
        <div className="px-[22px] pt-[18px] pb-[12px] flex items-start gap-3.5">
          <div className="flex-1 min-w-0">
            <div className="text-[10px] font-bold tracking-[0.10em] uppercase text-accent mb-1.5 font-mono">
              {t('external_measure.title')} · {chartId}
            </div>
            <h2 className="text-[17px] font-semibold text-text-strong leading-[1.3]">
              {t(`external_measure.heading.${step}`, { instrument })}
            </h2>
            <p className="text-xs text-text-muted mt-1" data-state={snap?.state || ''}>
              {snap ? t(`external_measure.state.${snap.state}`, { defaultValue: snap.state }) : t('common.loading')}
              {snap?.running && <span className="ml-2 inline-flex items-center gap-1 text-accent"><Loader2 size={11} className="animate-spin"/>{t('external_measure.running')}</span>}
            </p>
          </div>
          <button type="button" onClick={onClose} title={t('common.close_esc')}
                  className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-strong hover:bg-sunken flex-shrink-0">
            <X size={13} strokeWidth={1.7}/>
          </button>
        </div>
        <Stepper steps={steps} currentId={step}/>
        <div className="px-[22px] py-4 space-y-3 overflow-y-auto">
          {notice === 'stale' && <Notice kind="info">{t('external_measure.notice.stale')}</Notice>}
          {notice === 'sse_lost' && <p className="text-tiny text-text-faint">{t('external_measure.notice.sse_lost')}</p>}
          {error && (
            <Notice kind="danger" title={t('external_measure.refused')}>
              {errorText(error, t)} {t('external_measure.saved_counts', countArgs(snap))}
            </Notice>
          )}
          {pb && (
            <Notice kind={pb.tone === 'warn' ? 'warn' : 'danger'} title={t(`external_measure.problem.${pb.code}.title`)}>
              {t(`external_measure.problem.${pb.code}.body`)} {t('external_measure.saved_counts', countArgs(snap))}
            </Notice>
          )}
          {block && block !== 'checking' && (step === 'prepare' || step === 'read') && (
            <Notice kind="warn"><span data-start-block={block}>{blockText}</span></Notice>
          )}
          {step === 'prepare' && <PrepareStep t={t} snap={snap} instrument={instrument} presence={presence} filter={filter} setFilter={setFilter}/>}
          {step === 'calibrate' && <CalibrateStep t={t} snap={snap} instrument={instrument}/>}
          {step === 'read' && (
            <ReadStep t={t} snap={snap} session={session} can={can} confirm={confirm} setConfirm={setConfirm}
                      instrument={instrument} presence={presence}
                      correctPick={correctPick} setCorrectPick={setCorrectPick}/>
          )}
          {step === 'publish' && <PublishStep t={t} snap={snap} session={session} can={can} correctPick={correctPick}
                                              setCorrectPick={setCorrectPick} confirm={confirm} setConfirm={setConfirm}/>}
          {confirm === 'abandon' && (
            <ConfirmStrip t={t} text={t('external_measure.read.abandon_confirm')} danger
                          onYes={() => { setConfirm(null); session.abandon(); }} onNo={() => setConfirm(null)}/>
          )}
        </div>
        <div className="px-[18px] py-[12px] bg-sunken border-t border-border-soft flex items-center gap-[9px]">
          <span className="text-tiny text-text-faint flex-1">{t('external_measure.close_hint')}</span>
          {footer.map((b) => <FooterButton key={b.id} {...b}/>)}
          <button type="button" onClick={onClose}
                  className="py-[8px] px-[14px] rounded-[7px] text-[12.5px] font-medium border border-border-soft text-text-strong hover:bg-sunken">
            {t('common.close')}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

// Tints through color-mix: the theme's colours are CSS variables, on which Tailwind's opacity
// modifiers (bg-danger/10) generate nothing.
const tint = (v, pct) => ({ background: `color-mix(in oklch, var(${v}) ${pct}%, transparent)` });
const NOTICE = { info: '--accent', warn: '--warn', danger: '--danger', success: '--ok' };

function Notice({ kind = 'info', title, children }) {
  const v = NOTICE[kind] || NOTICE.info;
  return (
    <div role={kind === 'danger' ? 'alert' : 'status'} data-notice={kind}
         className="rounded-md border px-3 py-2 text-xs2 text-text-strong leading-relaxed"
         style={{ borderColor: `var(${v})`, ...tint(v, kind === 'warn' ? 16 : 9) }}>
      {title && <span className="font-semibold mr-1" style={{ color: kind === 'warn' ? 'var(--text)' : `var(${v})` }}>{title}</span>}
      {children}
    </div>
  );
}

function countArgs(snap) {
  const p = progress(snap);
  return { passes: p.passesSaved, total: p.passesTotal, patches: p.patchesSaved, patches_total: p.patchesTotal };
}

function FooterButton({ id, label, onClick, kind, icon: Icon, title }) {
  const cls = kind === 'primary' ? 'bg-accent hover:bg-accent-press text-on-accent font-semibold border-transparent'
    : kind === 'disabled' ? 'bg-bg text-text-faint cursor-not-allowed border-border-soft'
      : kind === 'danger-ghost' ? 'text-danger border-danger hover:bg-sunken'
        : 'text-text-strong border-border-soft hover:bg-sunken';
  return (
    <button type="button" onClick={kind === 'disabled' ? undefined : onClick} disabled={kind === 'disabled'}
            data-action={id} title={title}
            className={`inline-flex items-center gap-1.5 py-[8px] px-[14px] rounded-[7px] text-[12.5px] border ${cls}`}>
      {Icon && <Icon size={13}/>}{label}
    </button>
  );
}

function ConfirmStrip({ t, text, onYes, onNo, danger = false, yesLabel }) {
  return (
    <div className={`flex items-center gap-2 rounded-md border px-3 py-2 text-xs2 ${danger ? 'border-danger' : 'border-accent'}`}
         style={tint(danger ? '--danger' : '--accent', 8)} data-confirm="1">
      <span className="flex-1 text-text-strong">{text}</span>
      <button type="button" onClick={onNo} className="px-2.5 py-1 rounded-md border border-border-soft hover:bg-sunken">{t('common.cancel')}</button>
      <button type="button" onClick={onYes}
              className={`px-2.5 py-1 rounded-md font-semibold ${danger ? 'bg-danger text-white' : 'bg-accent text-on-accent'}`}>
        {yesLabel || t('common.confirm')}
      </button>
    </div>
  );
}

function Row({ label, children }) {
  return (
    <div className="flex items-start gap-3 text-xs2">
      <span className="w-[150px] flex-shrink-0 text-text-muted">{label}</span>
      <span className="flex-1 text-text-strong">{children}</span>
    </div>
  );
}

function PresenceRow({ t, instrument, presence }) {
  const s = presence.status;
  const color = s === 'present' ? 'text-success'
    : s === 'error' || s === 'checking' || s === 'unreachable' ? 'text-text-muted' : 'text-danger';
  return (
    <Row label={t('external_measure.prepare.presence_label')}>
      <span className={`inline-flex items-center gap-1.5 ${color}`} data-presence={s}>
        {s === 'checking' ? <Loader2 size={13} className="animate-spin"/> : <Usb size={13}/>}
        {t(`external_measure.presence.${presence.familyOnly ? 'present_family' : s}`, { instrument })}
      </span>
      {s === 'error' && <span className="block text-tiny text-text-faint mt-0.5">{t('external_measure.presence_unchecked')}</span>}
    </Row>
  );
}

function PrepareStep({ t, snap, instrument, presence, filter, setFilter }) {
  const ctx = snap?.context || {};
  const p = progress(snap);
  const eng = ctx.engine || {};
  const choices = conditionChoices(snap);
  const sessionCondition = snap?.session?.condition;
  return (
    <div className="space-y-3" data-step="prepare">
      <Row label={t('external_measure.prepare.chart')}>
        {t('external_measure.prepare.patches', { count: p.patchesTotal, strips: p.passesTotal })}
      </Row>
      <Row label={t('external_measure.prepare.instrument')}>{instrument}</Row>
      <PresenceRow t={t} instrument={instrument} presence={presence}/>
      <Row label={t('external_measure.prepare.engine')}>
        {eng.ok
          ? <span data-engine={eng.origin}>{t('external_measure.engine.ok', { argyll: eng.argyll_version || '?', patch: eng.patch_version || '?' })}</span>
          : <span className="text-danger" data-engine="missing">{t(`errors.${eng.code}`, { defaultValue: t('external_measure.engine.unavailable') })}</span>}
      </Row>
      {eng.ok && !eng.publishable && <Notice kind="warn">{t('external_measure.engine.development')}</Notice>}
      {choices.length > 0 && !sessionCondition && (
        <div className="space-y-1.5" data-conditions={choices.length}>
          <div className="text-xs2 font-semibold text-text-strong">{t('external_measure.prepare.condition_title')}</div>
          <div className="flex gap-2">
            {choices.map((c) => (
              <label key={c.option} className={`inline-flex items-center gap-1.5 text-xs2 px-2.5 py-1.5 rounded-md border cursor-pointer ${filter === c.option ? 'border-accent bg-accent-soft text-accent' : 'border-border-soft'}`}>
                <input type="radio" name="em-condition" value={c.option} checked={filter === c.option}
                       onChange={() => setFilter(c.option)} className="accent-current"/>
                {t(`measurement.condition.${c.condition}`)}
              </label>
            ))}
          </div>
          <p className="text-tiny text-text-faint">{t('external_measure.prepare.condition_hint')}</p>
        </div>
      )}
      {sessionCondition && <Row label={t('external_measure.prepare.condition_title')}>{t(`measurement.condition.${sessionCondition}`)}</Row>}
      <div className="flex items-start gap-2 text-xs2 text-text-strong bg-sunken rounded-md px-3 py-2.5">
        <Info size={14} className="mt-0.5 text-accent flex-shrink-0"/>
        <span>{t('external_measure.prepare.how', { instrument })}</span>
      </div>
    </div>
  );
}

function CalibrateStep({ t, snap, instrument }) {
  const pv = promptView(snap);
  const s = snap?.state;
  return (
    <div className="space-y-3" data-step="calibrate">
      {pv?.type === 'calibration' ? (
        <div className="rounded-md border border-accent bg-accent-soft px-4 py-3 space-y-1.5" data-calibration={pv.calibration}>
          <div className="text-sm font-semibold text-text-strong">
            {t(pv.retry ? 'external_measure.calibrate.retry_title' : 'external_measure.calibrate.title')}
          </div>
          <p className="text-xs2 text-text-strong leading-relaxed">
            {t(`external_measure.calibrate.${pv.calibration}`, { instrument, text: pv.instruction })}
          </p>
          {pv.instruction && <p className="text-tiny text-text-faint font-mono">chartread : « {pv.instruction} »</p>}
          <p className="text-tiny text-text-muted">{t('external_measure.calibrate.explicit')}</p>
        </div>
      ) : (
        <div className="flex items-center gap-2 text-xs2 text-text-strong">
          <Loader2 size={14} className="animate-spin text-accent"/>
          {t(`external_measure.calibrate.wait.${s}`, { defaultValue: t('external_measure.calibrate.wait.starting') })}
        </div>
      )}
    </div>
  );
}

function Bar({ label, value, total }) {
  const pct = total ? Math.min(100, (100 * value) / total) : 0;
  return (
    <div>
      <div className="flex justify-between text-tiny text-text-muted mb-0.5">
        <span>{label}</span><span className="font-mono text-text-strong">{value} / {total}</span>
      </div>
      <div className="h-[7px] rounded-full bg-sunken overflow-hidden">
        <div className="h-full bg-success transition-[width] duration-300" style={{ width: `${pct}%` }}/>
      </div>
    </div>
  );
}

function DurabilityLine({ t, snap }) {
  const d = durability(snap);
  if (!d) return <p className="text-tiny text-text-faint">{t('external_measure.durability.none')}</p>;
  const Icon = d.tone === 'success' ? CheckCircle2 : d.tone === 'danger' ? XCircle : d.tone === 'warn' ? AlertTriangle : Info;
  const color = d.tone === 'success' ? 'text-success' : d.tone === 'danger' ? 'text-danger' : d.tone === 'warn' ? 'text-icc-warn-deep' : 'text-accent';
  return (
    <div className="space-y-1" data-durability={d.kind}>
      <div className={`flex items-center gap-1.5 text-xs2 font-medium ${color}`}>
        <Icon size={13}/>{t(`external_measure.durability.${d.kind}`)}
      </div>
      {snap?.session?.durability_warning && d.kind !== 'reduced' && (
        <p className="text-tiny text-icc-warn-deep">{t('external_measure.durability.reduced_lasting', { count: snap.session.durability_warning.count })}</p>
      )}
    </div>
  );
}

function ReadStep({ t, snap, session, can, confirm, setConfirm, correctPick, setCorrectPick, instrument, presence }) {
  const pv = promptView(snap);
  const p = progress(snap);
  const s = snap?.state;
  const saved = snap?.session?.passes || [];
  return (
    <div className="space-y-3" data-step="read">
      {snap?.running && pv?.type === 'ready' && (
        <div className="rounded-md border border-accent bg-accent-soft px-4 py-3" data-expected-pass={pv.pass}>
          <div className="text-tiny uppercase tracking-[0.06em] text-text-muted">{t('external_measure.read.expected')}</div>
          <div className="text-[28px] font-semibold font-mono text-accent leading-tight">{pv.pass}</div>
          {pv.readBefore && <p className="text-tiny text-icc-warn-deep">{t('external_measure.read.reread')}</p>}
          <p className="text-xs2 text-text-strong mt-1">{t('external_measure.read.how')}</p>
          <div className="flex gap-2 mt-2">
            {['prev', 'next', 'next_unread'].filter((a) => allowed(snap, a)).map((a) => (
              <button key={a} type="button" disabled={!can(a)} onClick={() => session.command(a)} data-nav={a}
                      className="text-tiny px-2 py-1 rounded-md border border-border-soft hover:bg-sunken disabled:opacity-50">
                {t(`external_measure.read.${a}`)}
              </button>
            ))}
          </div>
        </div>
      )}
      {snap?.running && (s === 'saving' || s === 'saved') && (
        <div className="flex items-center gap-2 text-xs2 text-text-strong">
          <Loader2 size={14} className="animate-spin text-accent"/>
          {t('external_measure.read.saving', { pass: snap.acquired_not_saved || snap.pass || '' })}
        </div>
      )}
      {snap?.running && s === 'stopping' && (
        <div className="flex items-center gap-2 text-xs2 text-text-strong"><Loader2 size={14} className="animate-spin"/>{t('external_measure.read.stopping')}</div>
      )}
      {snap?.running && pv?.type === 'warning' && (
        <WarningBox t={t} pv={pv} snap={snap} session={session} can={can} confirm={confirm} setConfirm={setConfirm}/>
      )}
      {!snap?.running && s === 'idle' && !problem(snap) && (
        <Notice kind="info" title={t('external_measure.read.resume_title')}>{t('external_measure.read.resume_body')}</Notice>
      )}
      {!snap?.running && allowed(snap, 'start') && <PresenceRow t={t} instrument={instrument} presence={presence}/>}
      <div className="grid grid-cols-2 gap-3">
        <Bar label={t('external_measure.read.passes')} value={p.passesSaved} total={p.passesTotal}/>
        <Bar label={t('external_measure.read.patches')} value={p.patchesSaved} total={p.patchesTotal}/>
      </div>
      <div className="flex items-start gap-3">
        <span className="text-tiny text-text-muted w-[150px] flex-shrink-0">{t('external_measure.read.last_checkpoint')}</span>
        <DurabilityLine t={t} snap={snap}/>
      </div>
      <StripMap snap={snap}/>
      {!snap?.running && allowed(snap, 'correct') && saved.length > 0 && (
        <CorrectBox t={t} saved={saved} session={session} pick={correctPick} setPick={setCorrectPick}
                    confirm={confirm} setConfirm={setConfirm} can={can}/>
      )}
      <Events t={t} snap={snap}/>
    </div>
  );
}

function WarningBox({ t, pv, snap, session, can, confirm, setConfirm }) {
  const d = pv.data || {};
  return (
    <div className="rounded-md border border-warn px-4 py-3 space-y-2" style={tint('--warn', 14)} data-warning={pv.kind}>
      <div className="flex items-center gap-1.5 text-xs2 font-semibold text-text-strong">
        <AlertTriangle size={14} className="text-icc-warn-deep"/>
        {t(`external_measure.warning.${pv.kind}`, { reason: d.reason || '', read: d.read || '', expected: d.expected || '',
                                                    de: d.delta_e != null ? Number(d.delta_e).toFixed(1) : '', message: d.message || '' })}
      </div>
      <p className="text-tiny text-text-muted">{t(`external_measure.warning_hint.${pv.kind}`, { defaultValue: t('external_measure.warning_hint.default') })}</p>
      <div className="flex gap-2">
        {allowed(snap, 'retry') && (
          <button type="button" disabled={!can('retry')} onClick={() => session.command('retry')}
                  className="text-xs2 font-semibold px-3 py-1.5 rounded-md bg-accent text-on-accent disabled:opacity-50">{t('external_measure.actions.retry')}</button>
        )}
        {allowed(snap, 'accept') && (
          <button type="button" disabled={!can('accept')} onClick={() => setConfirm('accept')}
                  className="text-xs2 px-3 py-1.5 rounded-md border border-border-soft hover:bg-sunken disabled:opacity-50">{t('external_measure.actions.accept')}</button>
        )}
        {allowed(snap, 'give_up') && (
          <button type="button" disabled={!can('give_up')} onClick={() => session.command('give_up')}
                  className="text-xs2 px-3 py-1.5 rounded-md border border-border-soft hover:bg-sunken disabled:opacity-50">{t('external_measure.actions.give_up')}</button>
        )}
      </div>
      {confirm === 'accept' && (
        <ConfirmStrip t={t} text={t('external_measure.actions.accept_confirm')}
                      onYes={() => { setConfirm(null); session.command('accept'); }} onNo={() => setConfirm(null)}/>
      )}
    </div>
  );
}

function CorrectBox({ t, saved, session, pick, setPick, confirm, setConfirm, can }) {
  return (
    <div className="space-y-1.5 border-t border-border-soft pt-2" data-correct="1">
      <div className="flex items-center gap-2 text-xs2">
        <span className="text-text-muted">{t('external_measure.read.correct_title')}</span>
        <select value={pick} onChange={(e) => setPick(e.target.value)}
                className="text-xs2 bg-bg border border-border-soft rounded-md px-2 py-1">
          <option value="">—</option>
          {saved.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
        <button type="button" disabled={!pick || !can('correct')} onClick={() => setConfirm({ correct: pick })}
                className="text-xs2 px-2.5 py-1 rounded-md border border-border-soft hover:bg-sunken disabled:opacity-50">
          {t('external_measure.read.correct')}
        </button>
      </div>
      <p className="text-tiny text-text-faint">{t('external_measure.read.correct_hint')}</p>
      {confirm?.correct && (
        <ConfirmStrip t={t} text={t('external_measure.read.correct_confirm', { pass: confirm.correct })}
                      onYes={() => { const l = confirm.correct; setConfirm(null); setPick(''); session.correct(l); }}
                      onNo={() => setConfirm(null)}/>
      )}
    </div>
  );
}

function Events({ t, snap }) {
  const ev = recentEvents(snap);
  if (!ev.length) return null;
  return (
    <ul className="text-tiny text-text-muted space-y-0.5 border-t border-border-soft pt-2" data-events={ev.length}>
      {ev.map((n, i) => (   // several notices may share a seq (one bump)
        <li key={`${n.seq}:${n.kind}:${i}`}>· {t(`external_measure.event.${n.kind}`, { pass: n.pass || '', patches: n.patches_saved ?? '',
                                                                   reason: n.reason ? t(`external_measure.problem.${n.reason}.title`, { defaultValue: n.reason }) : '',
                                                                   error: n.error ? t(`external_measure.problem.${n.error}.title`, { defaultValue: n.error }) : '',
                                                                   defaultValue: n.kind })}</li>
      ))}
    </ul>
  );
}

function PublishStep({ t, snap, session, can, correctPick, setCorrectPick, confirm, setConfirm }) {
  const s = snap?.session || snap?.last_session || {};
  const published = snap?.state === 'published' ? (s.published || snap?.last_session?.published) : null;
  const checks = publishChecks(snap);
  return (
    <div className="space-y-3" data-step="publish">
      {published && (
        <Notice kind="success" title={t('external_measure.publish.published_title')}>
          {t('external_measure.publish.published', { stem: published.stem, n: published.n_readings })}
        </Notice>
      )}
      {!published && (
        <ul className="space-y-1.5" data-checks={checks.filter((c) => c.ok).length}>
          {checks.map((c) => (
            <li key={c.key} className="flex items-start gap-2 text-xs2" data-check={c.key} data-ok={c.ok ? '1' : '0'}>
              {c.ok ? <CheckCircle2 size={14} className="text-success mt-px"/> : <XCircle size={14} className="text-danger mt-px"/>}
              <span className="text-text-strong">{t(`external_measure.publish.check.${c.key}`, {
                passes: c.passes, total: c.total, n: c.n, start: c.start, end: c.end, value: c.value || '—',
                origin: c.origin ? t(`external_measure.origin.${c.origin}`, { defaultValue: c.origin }) : '—' })}</span>
            </li>
          ))}
        </ul>
      )}
      {s.durability_warning && <Notice kind="warn">{t('external_measure.durability.reduced_lasting', { count: s.durability_warning.count })}</Notice>}
      {s.accepted_warnings > 0 && <Notice kind="warn">{t('external_measure.publish.accepted', { count: s.accepted_warnings })}</Notice>}
      {(s.corrections || []).length > 0 && <p className="text-tiny text-text-muted">{t('external_measure.publish.corrections', { list: s.corrections.join(', ') })}</p>}
      {s.engine_origin && s.engine_origin !== 'approved' && <Notice kind="warn">{t('external_measure.engine.development')}</Notice>}
      {!published && allowed(snap, 'correct') && (s.passes || []).length > 0 && (
        <CorrectBox t={t} saved={s.passes} session={session} pick={correctPick} setPick={setCorrectPick}
                    confirm={confirm} setConfirm={setConfirm} can={can}/>
      )}
    </div>
  );
}
