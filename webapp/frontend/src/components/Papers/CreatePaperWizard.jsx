import { useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, AlertTriangle, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import * as api from '../../api/client.js';
import { useCalibration } from '../../hooks/useCalibration.js';
import ConfirmModal from '../ui/ConfirmModal.jsx';
import Modal from '../wizards/shared/Modal.jsx';
import ModalHeader from '../wizards/shared/ModalHeader.jsx';
import Stepper from '../wizards/shared/Stepper.jsx';
import WizButtonBar from '../wizards/shared/ButtonBar.jsx';
import Banner from '../wizards/shared/Banner.jsx';
import StepHero from '../wizards/shared/StepHero.jsx';
import { Picto } from '../wizards/shared/Pictograms.jsx';
import { useLoadedPaper } from '../../hooks/useLoadedPaper.js';

const STEPS = [
  { id: 'identity', label: 'Identité' },
  { id: 'calibration', label: 'Calibration' },
  { id: 'done', label: 'Créé' },
];

export default function CreatePaperWizard({
  open, initialDonor, allPapers = [], loadedPaperMediaid = null,
  onClose, onCreated, onNotice,
}) {
  const { t } = useTranslation();
  const [step, setStep] = useState('identity');
  const [name, setName] = useState('');
  const [donorId, setDonorId] = useState('');
  const [createdPaper, setCreatedPaper] = useState(null);
  const [creating, setCreating] = useState(false);
  const [pendingForceCreate, setPendingForceCreate] = useState(false);
  const [conflictMediaid, setConflictMediaid] = useState(null);
  const nameInputRef = useRef(null);

  useEffect(() => {
    if (open) {
      setStep('identity');
      setName('');
      setDonorId(initialDonor?.mediaid || '');
      setCreatedPaper(null);
      setCreating(false);
      setPendingForceCreate(false);
      setConflictMediaid(null);
      setTimeout(() => nameInputRef.current?.focus(), 100);
    }
  }, [open, initialDonor]);

  const handleCreate = async () => {
    setCreating(true);
    try {
      const res = await api.createPaper({ name: name.trim(), donor_id: donorId });
      setCreatedPaper(res.paper || { mediaid: res.mediaid, name: name.trim(), custom: true, factory: false, donor_id: res.donor_id, donor_name: res.donor_name });
      onCreated?.(res.paper);
      onNotice?.('success', t('wizard_create_paper.create_success', { name: name.trim() }));
      setStep('calibration');
    } catch (e) {
      if (e.code === 'name_conflict') { setConflictMediaid(e.existing_mediaid); setPendingForceCreate(true); }
      else onNotice?.('error', t('wizard_create_paper.create_error', { message: e.message }));
    } finally { setCreating(false); }
  };

  const loadedPaper = useLoadedPaper();
  const isCreatedPaperLoaded = createdPaper && loadedPaper?.mediaid === createdPaper.mediaid;

  const localizedSteps = STEPS.map((s) => ({ ...s, label: t(`wizard_create_paper.step_${s.id}`) }));

  const canValidate = _isStep1Valid(name, donorId);
  const buttonBar = step === 'identity'
    ? { left: { label: t('common.cancel'), onClick: onClose }, primary: { label: creating ? t('wizard_create_paper.creating') : t('wizard_create_paper.action_create_and_calibrate'), onClick: handleCreate, disabled: !canValidate || creating } }
    : step === 'calibration'
      ? { primary: { label: t('common.close'), onClick: onClose } }
      // KISS: creation does NOT chain into profiling — profiling is a separate
      // action from the Papers screen (paper detail). Done step just closes.
      : { primary: { label: t('common.close'), onClick: onClose } };

  return (
    <>
      <Modal open={open} onClose={creating ? undefined : onClose} ariaLabel={t('wizard_create_paper.panel_aria')}>
        <ModalHeader
          eyebrow={t('wizard_create_paper.title')}
          title={createdPaper?.name || name || t('wizard_create_paper.title')}
          subtitle={t(`wizard_create_paper.step_${step}`)}
          onClose={creating ? undefined : onClose}/>
        <Stepper steps={localizedSteps} currentId={step}/>

        <div className="flex-1 overflow-y-auto px-[22px] py-5">
          {step === 'identity' && (
            <>
              <StepHero picto={<Picto.create/>}>
                <p className="text-xs2 text-text-muted">{t('wizard_create_paper.step_identity_hint')}</p>
              </StepHero>
              <Step1Identity name={name} setName={setName} donorId={donorId} setDonorId={setDonorId}
                allPapers={allPapers} nameInputRef={nameInputRef}/>
            </>
          )}
          {step === 'calibration' && createdPaper && (
            <Step2Calibration paper={createdPaper} loadedPaperMediaid={loadedPaperMediaid}
              onDone={() => setStep('done')} onSkip={() => setStep('done')} onNotice={onNotice}/>
          )}
          {step === 'done' && createdPaper && (
            <>
              <StepHero picto={<Picto.success/>}>
                <p className="text-base font-medium text-text-strong">{t('wizard_create_paper.recap_title')}</p>
              </StepHero>
              <Step3Recap paper={createdPaper} donorName={initialDonor?.name}/>
              {!isCreatedPaperLoaded && (
                <div className="mt-4">
                  <Banner kind="info" title={t('wizard_create_paper.done_load_title')}>
                    {t('wizard_create_paper.done_load_message')}
                  </Banner>
                </div>
              )}
              {isCreatedPaperLoaded && (
                <div className="mt-4">
                  <Banner kind="success">{t('wizard_create_paper.done_paper_detected')}</Banner>
                </div>
              )}
            </>
          )}
        </div>

        <WizButtonBar {...buttonBar}/>
      </Modal>

      <ConfirmModal
        open={pendingForceCreate}
        title={t('wizard_create_paper.modal_conflict_title')}
        message={conflictMediaid
          ? t('wizard_create_paper.modal_conflict_message_with_id', { mediaid: conflictMediaid })
          : t('wizard_create_paper.modal_conflict_message_no_id')}
        confirmLabel={t('wizard_create_paper.modal_conflict_confirm')}
        cancelLabel={t('common.cancel')}
        confirmKind="primary"
        onConfirm={async () => {
          setPendingForceCreate(false);
          setCreating(true);
          try {
            const res = await api.createPaper({ name: name.trim(), donor_id: donorId, force: true });
            setCreatedPaper(res.paper || { mediaid: res.mediaid, name: name.trim(), custom: true, factory: false });
            onCreated?.(res.paper);
            onNotice?.('success', t('wizard_create_paper.create_success', { name: name.trim() }));
            setStep('calibration');
          } catch (e) { onNotice?.('error', t('wizard_create_paper.create_error', { message: e.message })); }
          finally { setCreating(false); }
        }}
        onCancel={() => { setPendingForceCreate(false); setConflictMediaid(null); }}/>
    </>
  );
}


function _isStep1Valid(name, donorId) {
  const trimmed = (name || '').trim();
  return trimmed.length > 0 && trimmed.length <= 64 && !!donorId;
}


function Step1Identity({ name, setName, donorId, setDonorId, allPapers, nameInputRef }) {
  const { t } = useTranslation();
  const grouped = useMemo(() => {
    const customs = (allPapers || []).filter((p) => p.custom).sort((a, b) => (a.name || '').localeCompare(b.name || '', 'fr'));
    const factoryByCategory = {};
    for (const p of allPapers || []) {
      if (!p.factory) continue;
      const cat = p.category || 'Autres';
      (factoryByCategory[cat] = factoryByCategory[cat] || []).push(p);
    }
    for (const cat of Object.keys(factoryByCategory)) factoryByCategory[cat].sort((a, b) => (a.name || '').localeCompare(b.name || '', 'fr'));
    return { customs, factoryCategories: Object.keys(factoryByCategory).sort(), factoryByCategory };
  }, [allPapers]);

  return (
    <div className="space-y-5">
      <div>
        <label htmlFor="wizard-name" className="block text-[11.5px] font-semibold text-text-strong mb-1.5">{t('wizard_create_paper.field_name')}</label>
        <input ref={nameInputRef} id="wizard-name" type="text" value={name} onChange={(e) => setName(e.target.value)} maxLength={64}
          placeholder={t('wizard_create_paper.field_name_placeholder')}
          className="w-full px-3 py-[9px] bg-surface border border-border-strong rounded-md text-[12.5px] text-text-strong placeholder:text-text-faint focus:outline-none focus:border-accent transition-colors"/>
        <p className="text-[11px] text-text-faint mt-1.5">{t('wizard_create_paper.field_name_hint')}</p>
      </div>
      <div>
        <label htmlFor="wizard-donor" className="block text-[11.5px] font-semibold text-text-strong mb-1.5">{t('wizard_create_paper.field_donor')}</label>
        <select id="wizard-donor" value={donorId} onChange={(e) => setDonorId(e.target.value)}
          className="w-full px-3 py-[9px] bg-surface border border-border-strong rounded-md text-[12.5px] text-text-strong focus:outline-none focus:border-accent transition-colors">
          <option value="">{t('wizard_create_paper.field_donor_placeholder')}</option>
          {grouped.customs.length > 0 && (
            <optgroup label={t('wizard_create_paper.donor_group_custom')}>
              {grouped.customs.map((p) => <option key={p.mediaid} value={p.mediaid}>{p.name}</option>)}
            </optgroup>
          )}
          {grouped.factoryCategories.map((cat) => (
            <optgroup key={cat} label={cat}>
              {grouped.factoryByCategory[cat].map((p) => <option key={p.mediaid} value={p.mediaid}>{p.name}</option>)}
            </optgroup>
          ))}
        </select>
        <p className="text-[11px] text-text-faint mt-1.5">{t('wizard_create_paper.field_donor_hint')}</p>
      </div>
    </div>
  );
}


function Step2Calibration({ paper, loadedPaperMediaid, onDone, onSkip, onNotice }) {
  const { t } = useTranslation();
  const { job, start, busy, startError } = useCalibration(paper.mediaid);
  const isLoaded = loadedPaperMediaid != null && loadedPaperMediaid === paper.mediaid;
  const [confirmClc, setConfirmClc] = useState(false);
  // NO silent auto-start: a CLC is a long machine op (~5-10 min, ties up the
  // printer) → it starts ONLY on explicit user confirmation (KISS).
  const running = !!job && job.state !== 'done' && job.state !== 'error';

  useEffect(() => {
    if (job?.state === 'done' || job?.state === 'error') onDone();
  }, [job?.state, onDone]);

  if (!isLoaded) {
    // Physical sequence: the paper is CREATED (software) but must now be LOADED in
    // the printer (declare its type at the panel) before a CLC. No dead-end "not
    // loaded" message: invite to load (this view flips to "launch" automatically
    // once loadedPaperMediaid — live from status — matches), or defer.
    return (
      <div className="space-y-4">
        <div className="flex items-start gap-3 bg-accent/5 border border-accent/25 rounded-[7px] p-[11px_13px]">
          <AlertTriangle size={14} className="text-accent mt-0.5 flex-shrink-0" aria-hidden="true"/>
          <div className="flex-1">
            <p className="text-[12.5px] font-semibold text-text-strong">{t('wizard_create_paper.calibration_load_title')}</p>
            <p className="text-[11.5px] text-text-muted mt-1 leading-relaxed">{t('wizard_create_paper.calibration_load_body', { name: paper.name })}</p>
          </div>
        </div>
        <div className="flex justify-center">
          <button type="button" onClick={onSkip}
            className="px-[14px] py-[9px] rounded-[7px] text-[12.5px] font-medium border border-border-soft text-text-strong hover:bg-sunken transition-colors">
            {t('wizard_create_paper.calibration_later')}
          </button>
        </div>
      </div>
    );
  }

  if (running) {
    const pct = typeof job?.progress === 'number' && job.progress >= 0 ? job.progress : null;
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <Loader2 size={14} strokeWidth={2.5} className="text-accent animate-spin" aria-hidden="true"/>
          <p className="text-[12.5px] font-medium text-text-strong">{t('wizard_create_paper.calibration_in_progress', { name: paper.name })}</p>
        </div>
        <div className="w-full h-2 bg-sunken rounded-full overflow-hidden">
          <div className="h-full bg-accent transition-all duration-500" style={{ width: pct != null ? `${Math.max(2, pct)}%` : '5%' }}
            role="progressbar" aria-valuenow={pct ?? 0} aria-valuemin={0} aria-valuemax={100}/>
        </div>
        <div className="text-[11.5px] text-text-muted">
          {pct != null ? `${pct}%` : t('papers.calibration.button_starting')}
        </div>
        <p className="text-[11px] text-text-faint italic">{t('wizard_create_paper.calibration_disclaimer')}</p>
      </div>
    );
  }

  // Loaded, not yet started → EXPLICIT launch (validation before the machine op).
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 bg-accent/5 border border-accent/25 rounded-[7px] p-[11px_13px]">
        <AlertTriangle size={14} className="text-accent mt-0.5 flex-shrink-0" aria-hidden="true"/>
        <div className="flex-1">
          <p className="text-[12.5px] font-semibold text-text-strong">{t('wizard_create_paper.calibration_ready_title')}</p>
          <p className="text-[11.5px] text-text-muted mt-1 leading-relaxed">{t('wizard_create_paper.calibration_ready_body')}</p>
        </div>
      </div>
      {startError && <p className="text-[11.5px] text-danger">{startError}</p>}
      <div className="flex justify-center gap-2.5">
        <button type="button" onClick={onSkip}
          className="px-[14px] py-[9px] rounded-[7px] text-[12.5px] font-medium border border-border-soft text-text-strong hover:bg-sunken transition-colors">
          {t('wizard_create_paper.calibration_skip')}
        </button>
        <button type="button" onClick={() => setConfirmClc(true)} disabled={busy}
          className="px-[14px] py-[9px] rounded-[7px] text-[12.5px] font-semibold bg-accent text-on-accent hover:bg-accent-press disabled:opacity-50 transition-colors">
          {t('wizard_create_paper.calibration_launch')}
        </button>
      </div>
      <ConfirmModal
        open={confirmClc}
        title={t('papers.calibration.modal_confirm_title', { name: paper.name })}
        message={t('papers.calibration.modal_confirm_message', { name: paper.name })}
        confirmLabel={t('papers.calibration.modal_confirm_button')}
        cancelLabel={t('common.cancel')}
        confirmKind="primary"
        onConfirm={() => { setConfirmClc(false); start(); }}
        onCancel={() => setConfirmClc(false)}/>
    </div>
  );
}


function Step3Recap({ paper, donorName }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-4">
      <dl className="space-y-2.5 text-[12.5px]">
        <Row label={t('wizard_create_paper.recap_field_name')} value={paper.name}/>
        <Row label={t('wizard_create_paper.recap_field_mediaid')} value={<span className="font-mono text-[11.5px]">{paper.mediaid}</span>}/>
        {donorName && <Row label={t('wizard_create_paper.recap_field_donor')} value={donorName}/>}
        <Row label={t('wizard_create_paper.recap_field_icc')} value={<span className="text-[11.5px] text-text-muted">{t('wizard_create_paper.recap_icc_inherited')}</span>}/>
      </dl>
      <div className="border-t border-border-soft pt-3 mt-3">
        <p className="text-[11.5px] text-text-muted">{t('wizard_create_paper.recap_footer')}</p>
      </div>
    </div>
  );
}


function Row({ label, value }) {
  return (
    <div className="flex items-start">
      <dt className="text-[11px] text-text-muted w-[140px] flex-shrink-0 pt-0.5">{label}</dt>
      <dd className="flex-1 text-text-strong min-w-0">{value}</dd>
    </div>
  );
}
