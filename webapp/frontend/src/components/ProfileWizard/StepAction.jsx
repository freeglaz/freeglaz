import { useTranslation } from 'react-i18next';

const WORKFLOWS = [
  { value: 'PRINT_AND_SCAN', labelKey: 'wizard_profile_paper.action_print_and_scan', descKey: 'wizard_profile_paper.action_print_and_scan_desc' },
  { value: 'PRINT_ONLY',     labelKey: 'wizard_profile_paper.action_print_only',     descKey: 'wizard_profile_paper.action_print_only_desc' },
  { value: 'SCAN_ONLY',      labelKey: 'wizard_profile_paper.action_scan_only',      descKey: 'wizard_profile_paper.action_scan_only_desc' },
];

// Argyll/custom options. NO symmetry with HP: measurement is OFFLINE (Path A —
// print → dry → reload → scan), so NO "print and scan auto" mode. Only 2 options
// (a shorter column than HP = NORMAL, we don't pad it with a fake 3rd mode).
const ARGYLL = [
  { value: 'argyll:print', labelKey: 'chart_create.argyll_mode_print', descKey: 'chart_create.argyll_mode_print_desc' },
  { value: 'argyll:scan',  labelKey: 'chart_create.argyll_mode_scan',  descKey: 'chart_create.argyll_mode_scan_desc' },
];

/**
 * StepAction — choice of profiling session type. A SINGLE paradigm: radios +
 * "Continue" (HP/Argyll routing is done by ProfileWizard based on the selection).
 * Without showArgyll: legacy HP rendering (one column). With: 2 columns of equal
 * width, top-aligned, the SAME element type (radio) on both sides.
 */
export default function StepAction({ selected, onSelect, showArgyll }) {
  const { t } = useTranslation();

  if (!showArgyll) {
    return (
      <div className="space-y-4">
        <p className="text-xs2 text-text-muted">{t('wizard_profile_paper.action_title')}</p>
        <div className="space-y-2">
          {WORKFLOWS.map((o) => (
            <RadioCard key={o.value} option={o} selected={selected === o.value}
                       onClick={() => onSelect(o.value)} t={t}/>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs2 text-text-muted">{t('wizard_profile_paper.action_title')}</p>
      <div className="grid grid-cols-2 gap-4 items-start">
        <div className="space-y-2">
          <h4 className="text-[11px] font-bold uppercase tracking-wide text-text-muted">{t('chart_create.col_hp')}</h4>
          {WORKFLOWS.map((o) => (
            <RadioCard key={o.value} option={o} selected={selected === o.value}
                       onClick={() => onSelect(o.value)} t={t}/>
          ))}
        </div>
        <div className="space-y-2">
          <h4 className="text-[11px] font-bold uppercase tracking-wide text-accent">{t('chart_create.col_argyll')}</h4>
          {ARGYLL.map((o) => (
            <RadioCard key={o.value} option={o} selected={selected === o.value}
                       onClick={() => onSelect(o.value)} t={t} accent/>
          ))}
          <p className="text-tiny text-text-faint italic px-1 pt-0.5">{t('chart_create.argyll_offline_note')}</p>
        </div>
      </div>
    </div>
  );
}

function RadioCard({ option, selected, onClick, t, accent }) {
  return (
    <button type="button" onClick={onClick}
            className={`w-full text-left rounded-lg p-4 border transition-colors ${
              selected
                ? (accent ? 'bg-accent/[0.05] border-accent/40' : 'bg-accent/[0.04] border-accent/35')
                : 'bg-surface border-border-soft hover:border-border-strong'}`}>
      <div className="flex items-center gap-3">
        <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center flex-shrink-0 ${
          selected ? 'border-accent' : 'border-border-strong'}`}>
          {selected && <div className="w-2 h-2 rounded-full bg-accent"/>}
        </div>
        <span className={`text-sm font-medium ${selected ? 'text-text-strong' : 'text-text-muted'}`}>{t(option.labelKey)}</span>
      </div>
      <p className="text-xs2 text-text-faint mt-1.5 ml-7">{t(option.descKey)}</p>
    </button>
  );
}
