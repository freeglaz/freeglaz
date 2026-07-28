import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as api from '../../api/client.js';
import { useCurrentCalibration } from '../../hooks/useCurrentCalibration.js';
import { useProfileJob } from '../../hooks/useProfileJob.js';
import ConfirmModal from '../ui/ConfirmModal.jsx';
import Modal from '../wizards/shared/Modal.jsx';
import ModalHeader from '../wizards/shared/ModalHeader.jsx';
import Stepper from '../wizards/shared/Stepper.jsx';
import WizButtonBar from '../wizards/shared/ButtonBar.jsx';
import StepHero from '../wizards/shared/StepHero.jsx';
import { Picto } from '../wizards/shared/Pictograms.jsx';
import StepAction from './StepAction.jsx';
import StepParams from './StepParams.jsx';
import StepProfiling from './StepProfiling.jsx';
import StepEnd from './StepEnd.jsx';

const STEPS = [
  { id: 'action', label: 'Action' },
  { id: 'params', label: 'Paramètres' },
  { id: 'profiling', label: 'Profilage' },
  { id: 'end', label: 'Fin' },
];

export default function ProfileWizard({
  open, paper, initialGlossEnhancer, loadedPaperMediaid,
  onClose, onRequestCalibration, onNavigateToPaper, onArgyll,
}) {
  const { t } = useTranslation();
  const [step, setStep] = useState('action');
  const [workflow, setWorkflow] = useState('PRINT_AND_SCAN');
  // Unified selection for the action screen: null = an HP workflow is selected;
  // 'print'|'scan' = an Argyll option is selected (same radio group).
  const [argyllChoice, setArgyllChoice] = useState(null);
  const [profileName, setProfileName] = useState('');
  const [glossEnhancer, setGlossEnhancer] = useState(paper?.capabilities?.ge ?? false);
  const [nameManuallyEdited, setNameManuallyEdited] = useState(false);
  const [validating, setValidating] = useState(false);
  const [modal, setModal] = useState(null);

  const activeCalibration = useCurrentCalibration();
  const profileJob = useProfileJob();

  useEffect(() => {
    if (open) {
      setWorkflow('PRINT_AND_SCAN');
      setArgyllChoice(null);
      setProfileName('');
      setGlossEnhancer(initialGlossEnhancer !== undefined ? initialGlossEnhancer : (paper?.capabilities?.ge ?? false));
      setNameManuallyEdited(false);
      setValidating(false);
      setModal(null);
      setStep(profileJob.isActive ? 'profiling' : 'action');
    }
  }, [open, paper]);

  useEffect(() => {
    if (step === 'profiling' && profileJob.job) {
      const s = profileJob.job.state;
      if (s === 'done' || s === 'error') setStep('end');
    }
  }, [step, profileJob.job?.state]);

  const handleClose = useCallback(() => onClose?.(), [onClose]);
  const canContinue = _canContinue(profileName);

  // Unified radio group (3 HP + 2 Argyll). Value = HP workflow, or 'argyll:print'
  // / 'argyll:scan'. Only one selectable.
  const selected = argyllChoice ? `argyll:${argyllChoice}` : workflow;
  const onSelect = useCallback((val) => {
    if (val.startsWith('argyll:')) setArgyllChoice(val.slice('argyll:'.length));
    else { setWorkflow(val); setArgyllChoice(null); }
  }, []);
  // "Continue" routes based on the selection: Argyll → opens the Argyll flow;
  // HP → advances to the params step (HP behavior unchanged).
  const handleActionNext = useCallback(() => {
    // GE end-to-end: hand the CHOSEN gloss enhancer (from the slot / this wizard)
    // to the Argyll flow — not a stale value — so the resident, chart GE and header
    // all match the slot the user is profiling.
    if (argyllChoice && onArgyll) onArgyll(argyllChoice, glossEnhancer);
    else setStep('params');
  }, [argyllChoice, onArgyll, glossEnhancer]);

  const _launchProfile = useCallback(() => {
    profileJob.start(paper.mediaid, { workflow, gloss_enhancer: glossEnhancer, profile_name: profileName.trim() });
    setStep('profiling');
  }, [paper, workflow, glossEnhancer, profileName, profileJob]);

  const handleContinue = useCallback(async () => {
    if (!canContinue) return;
    setValidating(true);
    try {
      if (loadedPaperMediaid !== paper?.mediaid) {
        setModal({ kind: 'error', title: t('wizard_profile_paper.validation_paper_not_loaded_title'), message: t('wizard_profile_paper.validation_paper_not_loaded_message') });
        return;
      }
      const [profileRes] = await Promise.all([api.getActiveProfile().catch(() => ({ job: null }))]);
      if (profileRes?.job && (profileRes.job.state === 'starting' || profileRes.job.state === 'running')) {
        setModal({ kind: 'error', title: t('wizard_profile_paper.validation_busy_title'), message: t('wizard_profile_paper.validation_busy_message', { operation: t('wizard_profile_paper.badge_label') }) });
        return;
      }
      if (activeCalibration != null) {
        setModal({ kind: 'error', title: t('wizard_profile_paper.validation_busy_title'), message: t('wizard_profile_paper.validation_busy_message', { operation: t('papers.calibration.in_progress_title') }) });
        return;
      }
      const clcStatus = paper?.clc?.status;
      if (clcStatus === 'never' || clcStatus === 'stale' || clcStatus === 'pending') {
        // pending = RECOMMENDED calibration (created, not run) → dedicated honest wording,
        // not "none found" (reserved for never/stale). Same modal + same delegation.
        const pending = clcStatus === 'pending';
        setModal({ kind: 'clc_warning',
          title: t(pending ? 'wizard_profile_paper.validation_clc_pending_title' : 'wizard_profile_paper.validation_no_clc_title'),
          message: t(pending ? 'wizard_profile_paper.validation_clc_pending_message' : 'wizard_profile_paper.validation_no_clc_message') });
        return;
      }
      _launchProfile();
    } finally { setValidating(false); }
  }, [canContinue, loadedPaperMediaid, paper, activeCalibration, _launchProfile, t]);

  const handleReset = useCallback(() => { profileJob.reset(); setStep('params'); }, [profileJob]);
  const handleRetry = useCallback(() => { profileJob.reset(); _launchProfile(); }, [profileJob, _launchProfile]);
  const handleViewInDetailPanel = useCallback(() => { handleClose(); onNavigateToPaper?.(paper?.mediaid); }, [handleClose, onNavigateToPaper, paper]);

  const endVariant = profileJob.job?.state === 'done' ? 'success' : 'error';

  const localizedSteps = STEPS.map((s) => ({
    ...s,
    label: t(`wizard_profile_paper.step_${s.id}`),
  }));

  const buttonBar = _buttonBarProps(step, endVariant, {
    t, handleClose, handleContinue, canContinue, validating,
    handleReset, handleRetry, onBack: () => setStep('action'), onNext: handleActionNext,
  });

  return (
    <>
      <Modal open={open} onClose={modal ? undefined : handleClose} ariaLabel={t('wizard_profile_paper.title')}
             widthClass="w-[min(960px,92vw)]">
        <ModalHeader
          eyebrow={t('wizard_profile_paper.title')}
          title={paper?.name || ''}
          subtitle={t(`wizard_profile_paper.step_${step}`)}
          onClose={handleClose}/>
        <Stepper steps={localizedSteps} currentId={step}/>

        <div className="flex-1 overflow-y-auto px-[22px] py-5">
          {step === 'action' && (
            onArgyll ? (
              // 2-column mode (HP + Argyll): full modal width, no cramped
              // centered container (StepHero caps at 440px — reserved for HP alone).
              <div className="pt-5 pb-2">
                <StepAction selected={selected} onSelect={onSelect} showArgyll/>
              </div>
            ) : (
              <StepHero picto={<Picto.profile/>}>
                <StepAction selected={selected} onSelect={onSelect} showArgyll={false}/>
              </StepHero>
            )
          )}
          {/* HP steps (always simpler): centered in a bounded column
              so they don't look stretched in the widened modal. */}
          {step === 'params' && (
            <div className="max-w-[560px] mx-auto">
              <StepParams paper={paper} workflow={workflow} profileName={profileName} setProfileName={setProfileName}
                glossEnhancer={glossEnhancer} setGlossEnhancer={setGlossEnhancer}
                nameManuallyEdited={nameManuallyEdited} setNameManuallyEdited={setNameManuallyEdited}/>
            </div>
          )}
          {step === 'profiling' && (
            <div className="max-w-[560px] mx-auto"><StepProfiling job={profileJob.job} workflow={workflow}/></div>
          )}
          {step === 'end' && (
            <div className="max-w-[560px] mx-auto">
              <StepEnd variant={endVariant} result={profileJob.job?.result} error={profileJob.job?.error}
                job={profileJob.job} paper={paper} glossEnhancer={glossEnhancer} workflow={workflow}
                onViewInDetailPanel={handleViewInDetailPanel}/>
            </div>
          )}
        </div>

        <WizButtonBar {...buttonBar}/>
      </Modal>

      <ConfirmModal open={modal?.kind === 'error'} title={modal?.title || ''} message={modal?.message || ''}
        confirmLabel="OK" cancelLabel="" confirmKind="primary"
        onConfirm={() => setModal(null)} onCancel={() => setModal(null)}/>
      <ConfirmModal open={modal?.kind === 'clc_warning'} title={modal?.title || ''} message={modal?.message || ''}
        cancelLabel={t('wizard_profile_paper.button_cancel')}
        thirdLabel={t('wizard_profile_paper.validation_no_clc_run_clc_first')} thirdKind="primary"
        onThird={() => { setModal(null); handleClose(); onRequestCalibration?.(paper); }}
        confirmLabel={t('wizard_profile_paper.validation_no_clc_continue_anyway')} confirmKind="primary"
        onConfirm={() => { setModal(null); _launchProfile(); }} onCancel={() => setModal(null)}/>
    </>
  );
}

function _canContinue(profileName) {
  const trimmed = (profileName || '').trim();
  if (trimmed.length === 0 || trimmed.length > 63) return false;
  return /^[a-zA-Z0-9 _-]+$/.test(trimmed);
}

function _buttonBarProps(step, endVariant, h) {
  if (step === 'action') return { left: { label: h.t('wizard_profile_paper.button_cancel'), onClick: h.handleClose }, primary: { label: h.t('wizard_profile_paper.button_continue'), onClick: h.onNext } };
  if (step === 'params') return { left: { label: h.t('wizard_profile_paper.button_cancel'), onClick: h.handleClose }, secondary: { label: h.t('wizard_profile_paper.button_back'), onClick: h.onBack }, primary: { label: h.validating ? '…' : h.t('wizard_profile_paper.button_start'), onClick: h.handleContinue, disabled: !h.canContinue || h.validating } };
  if (step === 'profiling') return { primary: { label: h.t('wizard_profile_paper.button_close'), onClick: h.handleClose } };
  if (endVariant === 'success') return { primary: { label: h.t('wizard_profile_paper.button_close'), onClick: h.handleClose } };
  return { left: { label: h.t('wizard_profile_paper.button_cancel'), onClick: h.handleClose }, secondary: { label: h.t('wizard_profile_paper.end_error_modify_config'), onClick: h.handleReset }, primary: { label: h.t('wizard_profile_paper.end_error_retry'), onClick: h.handleRetry } };
}
