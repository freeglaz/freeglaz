import { useCallback, useEffect, useState } from 'react';
import { RotateCcw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import * as api from '../../api/client.js';
import Modal from '../wizards/shared/Modal.jsx';
import ModalHeader from '../wizards/shared/ModalHeader.jsx';
import Stepper from '../wizards/shared/Stepper.jsx';
import WizButtonBar from '../wizards/shared/ButtonBar.jsx';
import Banner from '../wizards/shared/Banner.jsx';
import StepHero from '../wizards/shared/StepHero.jsx';
import { Picto } from '../wizards/shared/Pictograms.jsx';
import Segmented from '../wizards/shared/controls/Segmented.jsx';
import Field from '../wizards/shared/controls/Field.jsx';

const STEPS = [
  { id: 'edit', label: 'Modifier' },
  { id: 'confirm', label: 'Confirmer' },
  { id: 'done', label: 'Appliqué' },
];

const PEN_TO_RIB_OPTS = [
  { value: 'LOW', label: 'Normale' },
  { value: 'HIGH', label: 'Haute' },
];
const DRY_OPTS = [
  { value: 0, label: 'Aucun' },
  { value: 50, label: 'Réduit' },
  { value: 100, label: 'Auto' },
  { value: 150, label: 'Étendu' },
];
const STARWHEEL_OPTS = [
  { value: 'LOW', label: 'Bas' },
  { value: 'INTERMEDIATE', label: 'Centre' },
  { value: 'HIGH', label: 'Haut' },
];
const CUTTER_OPTS = [
  { value: true, label: 'Activé' },
  { value: false, label: 'Désactivé' },
];

export default function ModifyMechanicalWizard({ open, paper, onClose, onCalibrate, onProfile, onChanged }) {
  const { t } = useTranslation();

  const [step, setStep] = useState('edit');
  const [penToRib, setPenToRib] = useState('LOW');
  const [dryTimeFactor, setDryTimeFactor] = useState(100);
  const [starWheels, setStarWheels] = useState('INTERMEDIATE');
  const [cutter, setCutter] = useState(true);
  const [original, setOriginal] = useState(null);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open || !paper?.mediaid) return;
    setStep('edit');
    setApplying(false);
    setError(null);
    api.getMechanicalProperties(paper.mediaid).then((data) => {
      setPenToRib(data.pen_to_rib || 'LOW');
      setDryTimeFactor(data.dry_time_factor ?? 100);
      setStarWheels(data.star_wheel_pos || 'INTERMEDIATE');
      setCutter(data.cutter ?? true);
      setOriginal(data);
    }).catch(() => {});
  }, [open, paper?.mediaid]);

  const handleApply = useCallback(async () => {
    setApplying(true);
    setError(null);
    try {
      await api.setMechanicalProperties(paper.mediaid, {
        pen_to_rib: penToRib,
        dry_time_factor: dryTimeFactor,
        star_wheels_position: starWheels,
        cutter_enabled: cutter,
      });
      onChanged?.();
      setStep('done');
    } catch (e) {
      setError(e.message);
    } finally {
      setApplying(false);
    }
  }, [paper, penToRib, dryTimeFactor, starWheels, cutter, onChanged]);

  const handleRestore = useCallback(async () => {
    try {
      const data = await api.restoreDefaultPreset(paper.mediaid);
      setPenToRib(data.pen_to_rib || 'LOW');
      setDryTimeFactor(data.dry_time_factor ?? 100);
      setStarWheels(data.star_wheel_pos || 'INTERMEDIATE');
      setCutter(data.cutter ?? true);
      onChanged?.();
    } catch (e) {
      setError(e.message);
    }
  }, [paper, onChanged]);

  const localizedSteps = STEPS.map((s) => ({ ...s, label: t(`modify_mechanical.step_${s.id}`) }));

  const buttonBar = step === 'edit'
    ? { left: { label: t('common.cancel'), onClick: onClose }, primary: { label: t('modify_mechanical.next'), onClick: () => setStep('confirm') } }
    : step === 'confirm'
      ? { left: { label: t('modify_mechanical.back'), onClick: () => setStep('edit') }, primary: { label: applying ? '…' : t('modify_mechanical.apply'), onClick: handleApply, disabled: applying } }
      : { primary: { label: t('modify_mechanical.close'), onClick: () => { onClose?.(); } } };

  return (
    <Modal open={open} onClose={onClose} ariaLabel={t('modify_mechanical.title')}>
      <ModalHeader eyebrow={t('modify_mechanical.title')} title={paper?.name || ''} subtitle={t(`modify_mechanical.step_${step}`)} onClose={onClose}/>
      <Stepper steps={localizedSteps} currentId={step}/>

      <div className="flex-1 overflow-y-auto px-[22px] py-5">
        {step === 'edit' && (
          <>
            <StepHero picto={<Picto.settings/>}>
              <p className="text-[12.5px] text-text-muted">{t('modify_mechanical.edit_hint')}</p>
            </StepHero>

            <div className="flex items-center justify-end mb-4">
              <button type="button" onClick={handleRestore}
                className="flex items-center gap-1.5 text-[11.5px] text-text-muted hover:text-text-strong font-medium border border-dashed border-border-soft rounded-md px-2.5 py-1.5 hover:bg-sunken transition-colors">
                <RotateCcw size={11}/> {t('modify_mechanical.restore_button')}
              </button>
            </div>

            <div className="space-y-5">
              <Field label={t('modify_mechanical.pen_to_rib_label')}>
                <Segmented options={PEN_TO_RIB_OPTS.map((o) => ({ ...o, label: t(`modify_mechanical.pen_to_rib_${o.value}`) }))} value={penToRib} onChange={setPenToRib}/>
              </Field>
              <Field label={t('modify_mechanical.dry_time_label')}>
                <Segmented options={DRY_OPTS.map((o) => ({ ...o, label: t(`modify_mechanical.dry_time_${o.value}`) }))} value={dryTimeFactor} onChange={setDryTimeFactor}/>
              </Field>
              <Field label={t('modify_mechanical.star_wheels_label')}>
                <Segmented options={STARWHEEL_OPTS.map((o) => ({ ...o, label: t(`modify_mechanical.star_wheels_${o.value}`) }))} value={starWheels} onChange={setStarWheels}/>
              </Field>
              <Field label={t('modify_mechanical.cutter_label')}>
                <Segmented options={CUTTER_OPTS.map((o) => ({ ...o, label: t(`modify_mechanical.cutter_${o.value}`) }))} value={cutter} onChange={setCutter}/>
              </Field>
            </div>

          </>
        )}

        {step === 'confirm' && (
          <>
            <StepHero picto={<Picto.warn/>}>
              <p className="text-base font-semibold text-text-strong">{t('modify_mechanical.confirm_title')}</p>
            </StepHero>
            <Banner kind="info">{t('modify_mechanical.confirm_banner', { name: paper?.name })}</Banner>
            {error && <div className="mt-3"><Banner kind="danger">{error}</Banner></div>}
          </>
        )}

        {step === 'done' && (
          <>
            <StepHero picto={<Picto.success/>}>
              <p className="text-base font-semibold text-text-strong">{t('modify_mechanical.done_title')}</p>
            </StepHero>
            <Banner kind="success">{t('modify_mechanical.done_banner')}</Banner>
            <div className="flex gap-2 mt-5 justify-center">
              {onCalibrate && (
                <button type="button" onClick={() => { onClose?.(); onCalibrate?.(paper); }}
                  className="px-4 py-2 rounded-[7px] text-[12.5px] font-semibold bg-accent hover:bg-accent-press text-white transition-colors">
                  {t('modify_mechanical.action_clc')}
                </button>
              )}
              {onProfile && (
                <button type="button" onClick={() => { onClose?.(); onProfile?.(paper); }}
                  className="px-[14px] py-2 rounded-[7px] text-[12.5px] font-medium border border-border-soft text-text-strong hover:bg-sunken transition-colors">
                  {t('modify_mechanical.action_profile')}
                </button>
              )}
            </div>
          </>
        )}
      </div>

      <WizButtonBar {...buttonBar}/>
    </Modal>
  );
}
