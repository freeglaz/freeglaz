import { useMemo } from 'react';
import { Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';

const ALL_PHASES = {
  preparing:   { labelKey: 'wizard_profile_paper.phase_preparing',  est: '~10 s' },
  printing:    { labelKey: 'wizard_profile_paper.phase_printing',   est: '~1 min' },
  drying:      { labelKey: 'wizard_profile_paper.phase_drying',     est: '~8 min' },
  scanning:    { labelKey: 'wizard_profile_paper.phase_scanning',   est: '~30 s' },
  calculating: { labelKey: 'wizard_profile_paper.phase_computing',  est: '~10 s' },
};

const PHASES_BY_WORKFLOW = {
  PRINT_AND_SCAN: ['preparing', 'printing', 'drying', 'scanning', 'calculating'],
  PRINT_ONLY:     ['preparing', 'printing'],
  SCAN_ONLY:      ['preparing', 'scanning', 'calculating'],
};

const PROCESS_TO_PHASE = {
  PRINTING:  'printing',
  DRYING:    'drying',
  SCANNING:  'scanning',
  COMPUTING: 'calculating',
};

export function processToPhase(process) {
  return PROCESS_TO_PHASE[process] || 'preparing';
}

export default function PhaseTracker({ currentPhase, workflow = 'PRINT_AND_SCAN' }) {
  const { t } = useTranslation();
  const phases = useMemo(() => {
    const keys = PHASES_BY_WORKFLOW[workflow] || PHASES_BY_WORKFLOW.PRINT_AND_SCAN;
    return keys.map((k) => ({ key: k, ...ALL_PHASES[k] }));
  }, [workflow]);

  const currentIdx = phases.findIndex((p) => p.key === currentPhase);

  return (
    <div className="flex items-start justify-between w-full" role="progressbar"
         aria-valuenow={currentIdx >= 0 ? currentIdx + 1 : 0}
         aria-valuemin={0} aria-valuemax={phases.length}
         aria-valuetext={currentIdx >= 0 ? t(phases[currentIdx].labelKey) : ''}>
      {phases.map((phase, i) => {
        const done = i < currentIdx;
        const active = i === currentIdx;
        const isLast = i === phases.length - 1;

        return (
          <div key={phase.key} className={`flex items-center ${isLast ? '' : 'flex-1'}`}>
            <div className="flex flex-col items-center min-w-[64px]">
              <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                done
                  ? 'bg-success text-white'
                  : active
                    ? 'bg-accent text-on-accent animate-pulse'
                    : 'border-2 border-border-strong'
              }`}>
                {done ? (
                  <Check size={14} strokeWidth={3} aria-hidden="true"/>
                ) : (
                  <span className={`text-[10px] font-medium ${
                    active ? 'text-white' : 'text-text-faint'
                  }`}>{i + 1}</span>
                )}
              </div>
              <span className={`text-[11px] mt-1.5 text-center leading-tight ${
                active ? 'font-semibold text-accent' : done ? 'text-text-strong' : 'text-text-faint'
              }`}>
                {t(phase.labelKey)}
              </span>
              <span className="text-[9px] font-mono text-text-faint mt-0.5">
                {phase.est}
              </span>
            </div>

            {!isLast && (
              <div className={`flex-1 h-0.5 mx-1 mt-4 self-start ${
                done ? 'bg-success' : active ? 'bg-accent/40' : 'bg-sunken-deep'
              }`}/>
            )}
          </div>
        );
      })}
    </div>
  );
}
