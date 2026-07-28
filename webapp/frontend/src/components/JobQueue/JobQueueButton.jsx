import { forwardRef } from 'react';
import { Layers } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * "Queue" button in the status bar (14 P1.B).
 *
 * Displays:
 *  - stack icon
 *  - "Queue" label
 *  - counter (total jobs) with semantic color:
 *    · HP Blue pill if there are live jobs (Active/Pending/Printing)
 *    · sunken gray pill if only ghosts (Completed/Cancelled/Deleted)
 *    · no pill if 0 jobs
 *
 * `open` state: HP Blue outline + HP-Blue/10% background.
 *
 * forwardRef to let App.jsx restore focus on it when the panel closes
 * (a11y, focus restore — spec §8).
 *
 * @param {object} p
 * @param {number} p.total      Total jobs (live + ghosts)
 * @param {number} p.activeCount Count of live jobs (drives the pill color)
 * @param {boolean} p.open       True if the panel is open (toggle style)
 * @param {() => void} p.onToggle Toggle open / close
 */
const JobQueueButton = forwardRef(function JobQueueButton(
  { total = 0, activeCount = 0, open, onToggle },
  ref,
) {
  const { t } = useTranslation();
  const hasJobs = total > 0;
  const hasActive = activeCount > 0;

  // Counter pill color
  const badgeColor = !hasJobs
    ? ''
    : hasActive
      ? 'bg-accent text-white'
      : 'bg-sunken-deep text-text-muted';

  // Button style depending on the open state
  const baseCls = (
    'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs2 ' +
    'transition-colors '
  );
  const stateCls = open
    ? 'border border-accent/50 bg-accent/10 text-accent'
    : 'border border-transparent hover:bg-sunken text-text-muted hover:text-text-strong';

  return (
    <button
      ref={ref}
      type="button"
      onClick={onToggle}
      aria-label={`${t('queue.title')} (${t('queue.header_jobs', { count: total })})`}
      aria-expanded={open}
      title={`${t('queue.title')} (⌘J)`}
      className={baseCls + stateCls}>
      <Layers size={13} strokeWidth={2} aria-hidden="true"/>
      <span className="font-medium">{t('queue.title')}</span>
      {hasJobs && (
        <span
          className={`inline-flex items-center justify-center min-w-[18px] h-[18px] px-1.5 rounded-full text-tiny font-semibold tabular-nums ${badgeColor}`}>
          {total}
        </span>
      )}
    </button>
  );
});

export default JobQueueButton;
