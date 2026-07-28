import { useTranslation } from 'react-i18next';
import { UIState } from '../../lib/state-machine.js';
import { fmtPct } from '../../lib/format.js';
import { useCurrentCalibration } from '../../hooks/useCurrentCalibration.js';
import { useActiveProfile } from '../../hooks/useActiveProfile.js';
import InkChannels from './InkChannels.jsx';
import WakeButton from './WakeButton.jsx';
import CalibrationBadge from './CalibrationBadge.jsx';
import ProfilingBadge from './ProfilingBadge.jsx';
import JobQueueButton from '../JobQueue/JobQueueButton.jsx';

/**
 * Bottom bar, fixed height 36px. Left: Z9 state. Center: 8 ink
 * channels. Right: paper metadata + queue button.
 *
 * @param {object} p
 * @param {object} p.queueSnapshot /api/jobs snapshot for the counter
 * @param {boolean} p.queueOpen    True if the JobQueuePanel is open
 * @param {() => void} p.onToggleQueue Toggle panel open
 */
export default function StatusBar({
  state, paper, inks, job, progress = 0,
  z9Activity,     // status.z9_activity — physical printer state (Processing/Drying…)
  queueSnapshot, queueOpen, queueButtonRef, onToggleQueue,
  // Z9 wake
  z9State,        // status.z9_state — 'error' enables the wake button
  waking,         // true while the wake operation is in progress
  onWake,         // callback invoked on Wake click
  // global "CLC in progress" badge
  onOpenCalibrationPaper,
  // global "Profiling in progress" badge
  onOpenProfilingWizard,
}) {
  const { t } = useTranslation();
  const calibrationJob = useCurrentCalibration();
  const profilingJob = useActiveProfile();
  const printing = state === UIState.E_PRINTING;
  const label =
    state === UIState.D_NOPAPER ? t('status_bar.no_paper') :
    printing                    ? t('status_bar.printing') :
                                  t('status_bar.ready');
  const dotColor =
    state === UIState.D_NOPAPER ? 'bg-danger' :
    printing                    ? 'bg-accent' :
                                  'bg-success';

  // Physical Z9 activity (from the existing status stream): shown whenever the
  // printer is doing something — including after the webapp job finished but the
  // Z9 is still Processing/Drying (the desync the backend Z9Activity documents).
  const act = (z9Activity && z9Activity.name && z9Activity.name !== 'NoActivity')
    ? z9Activity : null;
  const activityLabel = act
    ? t(`status_bar.activity.${act.name}`, { defaultValue: act.name })
      + (act.progress_pct != null ? ` ${Math.round(act.progress_pct)}%` : '')
    : null;

  return (
    <div className="h-9 bg-surface border-t border-border-soft flex items-center px-4 gap-4 text-xs2 text-text-muted">
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full ${dotColor}`}/>
        <span className="text-xs font-medium text-text-strong">HP DesignJet Z9</span>
        <span>· {label}</span>
        {act && (
          <span className="text-accent font-medium">· {activityLabel}</span>
        )}
      </div>
      <WakeButton
        visible={z9State === 'error'}
        busy={!!waking}
        onWake={onWake}/>
      <CalibrationBadge
        job={calibrationJob}
        onClick={(j) => onOpenCalibrationPaper?.(j.mediaid)}/>
      <ProfilingBadge
        job={profilingJob}
        onClick={(j) => onOpenProfilingWizard?.(j)}/>
      <div className="w-px h-4 bg-border-soft"/>
      <InkChannels inks={inks}/>
      <div className="flex-1"/>
      {printing && job ? (
        <div className="flex items-center gap-2.5">
          <span className="font-mono text-text-strong tabular-nums">{job.id?.slice(0, 23) || 'job'} · {fmtPct(progress * 100)}</span>
          <div className="w-[120px] h-1 bg-sunken rounded-full overflow-hidden">
            <div className="h-full bg-accent" style={{ width: `${progress * 100}%` }}/>
          </div>
        </div>
      ) : (
        <span className="font-mono text-text-faint text-tiny">
          {state === UIState.D_NOPAPER
            ? '— · — · —'
            : paper ? _fmtPaperInfo(paper, t) : ''}
        </span>
      )}
      {onToggleQueue && (
        <>
          <div className="w-px h-4 bg-border-soft ml-2"/>
          <JobQueueButton
            ref={queueButtonRef}
            total={queueSnapshot?.number_of_jobs ?? 0}
            activeCount={_countActiveJobs(queueSnapshot)}
            open={!!queueOpen}
            onToggle={onToggleQueue}/>
        </>
      )}
    </div>
  );
}

/**
 * Counts the "alive" jobs in the snapshot for the dot color.
 * Alive = not in a terminal state (Completed/Cancelled/Deleted).
 */
function _fmtMm(v) { return v != null ? Number(v.toFixed(1)) : null; }

function _fmtPaperInfo(paper, t) {
  const w = _fmtMm(paper.width_mm);
  const h = _fmtMm(paper.height_mm);
  const isRoll = paper.kind === 'roll' || h == null;
  const suffix = paper.name?.split(' ').slice(-1)[0] || '';
  if (isRoll) return t('status_bar.paper_roll', { width: w, name: suffix });
  return t('status_bar.paper_sheet', { width: w, height: h, name: suffix });
}

function _countActiveJobs(snapshot) {
  const jobs = snapshot?.jobs || [];
  return jobs.filter((j) => {
    const s = j?.status || '';
    return s !== 'Completed' && s !== 'Cancelled' && s !== 'Deleted';
  }).length;
}
