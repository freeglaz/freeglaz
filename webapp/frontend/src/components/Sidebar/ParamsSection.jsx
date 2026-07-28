import { useTranslation } from 'react-i18next';
import Label from '../ui/Label.jsx';
import Field from '../ui/Field.jsx';
import Segmented from '../ui/Segmented.jsx';
import Stepper from '../ui/Stepper.jsx';

/**
 * 3 controls only. No "Dimensions" section — this is non-negotiable,
 * see DESIGN_INTENT.md §1.
 *
 * `glossSupported`: if false (paper that does not support GE — typically
 * matte paper), the Gloss toggle is grayed out independently of the global
 * `disabled` state. The state value is not forced to 'off' — the backend
 * ignores the GE param for these papers anyway.
 */
export default function ParamsSection({
  value, onChange, disabled = false, glossSupported = true,
}) {
  const { t } = useTranslation();
  const upd = (patch) => onChange?.({ ...value, ...patch });
  const glossOptions = [
    { value: 'image', label: t('print.gloss_enhancer_option_image') },
    { value: 'off',   label: t('print.gloss_enhancer_option_off') },
  ];
  const glossHelp = glossSupported
    ? t('print.gloss_enhancer_help_default')
    : t('print.gloss_enhancer_help_unsupported');
  return (
    <>
      <Label>{t('print.section_params')}</Label>
      <Field label={t('print.gloss_enhancer_label')} help={glossHelp} dim={disabled}>
        <Segmented
          options={glossOptions}
          value={value.gloss}
          onChange={(v) => upd({ gloss: v })}
          disabled={disabled || !glossSupported}/>
      </Field>
      <Field label={t('print.quality_label')} dim={disabled}>
        <Segmented options={['HIGH', 'NORMAL', 'FAST']} value={value.quality} onChange={(v) => upd({ quality: v })} disabled={disabled}/>
      </Field>
      <Field label={t('print.copies_label')} dim={disabled}>
        <Stepper value={value.copies} onChange={(v) => upd({ copies: v })} disabled={disabled}/>
      </Field>
    </>
  );
}
