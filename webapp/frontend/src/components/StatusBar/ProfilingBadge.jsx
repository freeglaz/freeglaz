import { useTranslation } from 'react-i18next';

const PROCESS_LABELS = {
  PRINTING:  'wizard_profile_paper.phase_printing',
  DRYING:    'wizard_profile_paper.phase_drying',
  SCANNING:  'wizard_profile_paper.phase_scanning',
  COMPUTING: 'wizard_profile_paper.phase_computing',
};

export default function ProfilingBadge({ job, onClick }) {
  const { t } = useTranslation();
  if (!job) return null;

  const phasePart = job.process && PROCESS_LABELS[job.process]
    ? ` · ${t(PROCESS_LABELS[job.process])}`
    : '';
  const pctPart = typeof job.progress === 'number' && job.progress >= 0
    ? ` ${job.progress}%`
    : '';
  const badgeKey = job.workflow === 'PRINT_ONLY'
    ? 'wizard_profile_paper.badge_label_print_only'
    : job.workflow === 'SCAN_ONLY'
      ? 'wizard_profile_paper.badge_label_scan_only'
      : 'wizard_profile_paper.badge_label';
  const label = t(badgeKey) + phasePart + pctPart;

  return (
    <button
      type="button"
      onClick={() => onClick?.(job)}
      aria-label={label}
      title={label}
      role="status"
      className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-accent/10 text-accent hover:bg-accent/15 transition-colors text-tiny font-medium">
      <span className="relative flex h-2 w-2 flex-shrink-0">
        <span className="absolute inline-flex h-full w-full rounded-full bg-accent opacity-50 animate-ping"/>
        <span className="relative inline-flex h-2 w-2 rounded-full bg-accent"/>
      </span>
      <span>{label}</span>
    </button>
  );
}
