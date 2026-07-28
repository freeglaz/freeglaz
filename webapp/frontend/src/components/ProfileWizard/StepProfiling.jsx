import { useEffect, useRef, useState } from 'react';
import { Info } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PhaseTracker, { processToPhase } from './PhaseTracker.jsx';

export default function StepProfiling({ job, workflow = 'PRINT_AND_SCAN' }) {
  const { t } = useTranslation();
  const elapsed = useElapsedTimer(job);
  const phase = processToPhase(job?.process);

  const mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
  const ss = String(Math.floor(elapsed % 60)).padStart(2, '0');

  return (
    <div className="flex flex-col items-center space-y-6">
      {/* Elapsed time counter */}
      <div className="text-center">
        <p className="text-[28px] font-mono font-semibold text-accent tabular-nums tracking-tight">
          {mm}:{ss}
        </p>
        <p className="text-[11px] tracking-[0.08em] uppercase text-text-faint mt-1">
          {t('wizard_profile_paper.progress_elapsed_label')}
        </p>
      </div>

      {/* Phase tracker */}
      <div className="w-full">
        <PhaseTracker currentPhase={phase} workflow={workflow}/>
      </div>

      {/* Note info */}
      <div className="flex items-start gap-3 bg-accent/[0.06] border border-accent/20 rounded-md p-3 w-full">
        <Info size={16} className="text-accent mt-0.5 flex-shrink-0" aria-hidden="true"/>
        <p className="text-sm text-text-muted leading-relaxed">
          {t('wizard_profile_paper.progress_can_close')}
        </p>
      </div>
    </div>
  );
}


function useElapsedTimer(job) {
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef(null);
  const startTimeRef = useRef(null);

  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);

    if (!job) { setElapsed(0); return; }

    const isActive = job.state === 'starting' || job.state === 'running';
    if (!isActive) {
      setElapsed(job.elapsed || 0);
      return;
    }

    // Use backend elapsed as baseline, then tick locally
    const backendElapsed = job.elapsed || 0;
    startTimeRef.current = Date.now() / 1000 - backendElapsed;
    setElapsed(backendElapsed);

    timerRef.current = setInterval(() => {
      setElapsed(Math.floor(Date.now() / 1000 - startTimeRef.current));
    }, 1000);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [job?.state, job?.elapsed]);

  return elapsed;
}
