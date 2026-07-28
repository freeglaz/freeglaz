import { Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * Global "CLC in progress" badge in the status bar.
 * Visible only if a calibration is running in the background.
 */
export default function CalibrationBadge({ job, onClick }) {
  const { t } = useTranslation();
  if (!job) return null;
  const pct = typeof job.progress === 'number' && job.progress >= 0
    ? `${job.progress}%`
    : t('papers.calibration.badge_global_init');
  return (
    <button
      type="button"
      onClick={() => onClick?.(job)}
      aria-label={t('papers.calibration.badge_global_aria', { mediaid: job.mediaid, progress: pct })}
      title={t('papers.calibration.badge_global_tooltip', { progress: pct })}
      className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-accent/10 text-accent hover:bg-accent/15 transition-colors text-tiny font-medium">
      <Loader2 size={11} strokeWidth={2.5} className="animate-spin" aria-hidden="true"/>
      <span>{t('papers.calibration.badge_global', { progress: pct })}</span>
    </button>
  );
}
