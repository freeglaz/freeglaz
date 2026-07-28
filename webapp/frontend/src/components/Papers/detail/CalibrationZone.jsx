import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useCalibration } from '../../../hooks/useCalibration.js';
import { useCurrentCalibration } from '../../../hooks/useCurrentCalibration.js';
import ConfirmModal from '../../ui/ConfirmModal.jsx';

/**
 * Zone 3 — Color linear calibration (CLC). P1.D + P4.C.
 * 4 mutually exclusive states: idle / starting|running / done /
 * error. Contextual button label based on the paper's CLC status.
 */
export default function CalibrationZone({
  paper, loadedPaperMediaid = null, onCalibrationDone, onNotice, offline = false,
}) {
  const { t } = useTranslation();
  const { job, start, busy, startError } = useCalibration(paper.mediaid);
  const globalActive = useCurrentCalibration();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const isActive = job?.state === 'starting' || job?.state === 'running';
  const isLoadedHere = loadedPaperMediaid != null
    && loadedPaperMediaid === paper.mediaid;
  const otherCalibrationRunning = globalActive != null
    && globalActive.mediaid !== paper.mediaid;

  let disabledReason = null;
  if (offline && !isActive) {
    // #5 — offline: CLC requires the Z9 live.
    disabledReason = t('papers.calibration.disabled_offline');
  } else if (otherCalibrationRunning) {
    disabledReason = t('papers.calibration.disabled_busy');
  } else if (!isLoadedHere && !isActive) {
    disabledReason = t('papers.calibration.disabled_not_loaded');
  }

  useEffect(() => {
    if (job?.state === 'done') {
      onNotice?.('success', t('papers.calibration.toast_finished'));
      onCalibrationDone?.();
    } else if (job?.state === 'error') {
      onNotice?.('error',
        job.error
          ? t('papers.calibration.toast_failed', { message: job.error })
          : t('papers.calibration.toast_failed_unknown'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.state]);

  useEffect(() => {
    if (startError) onNotice?.('error', startError);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startError]);

  return (
    <section className="px-5 py-4 border-b border-border-soft">
      <h3 className="text-[11px] font-semibold tracking-[0.08em] uppercase text-text-muted mb-3">
        {t('papers.detail.section_calibration')}
      </h3>

      {isActive ? (
        <RunningCard job={job}/>
      ) : (
        <IdleCard
          paper={paper} job={job}
          onStart={() => setConfirmOpen(true)}
          busy={busy}
          disabledReason={disabledReason}/>
      )}

      <ConfirmModal
        open={confirmOpen}
        title={t('papers.calibration.modal_confirm_title', { name: paper.name })}
        message={t('papers.calibration.modal_confirm_message', { name: paper.name })}
        confirmLabel={t('papers.calibration.modal_confirm_button')}
        cancelLabel={t('common.cancel')}
        confirmKind="primary"
        onConfirm={() => { setConfirmOpen(false); start(); }}
        onCancel={() => setConfirmOpen(false)}/>
    </section>
  );
}


// ─── Sub-cards ─────────────────────────────────────────────────────────


function IdleCard({ paper, job, onStart, busy, disabledReason }) {
  const { t, i18n } = useTranslation();
  const justFinished = job?.state === 'done';
  const justErrored  = job?.state === 'error';

  const clc = paper.clc || {};
  const status = justFinished ? 'valid' : clc.status;
  const date   = justFinished ? job.result?.calibration_date : clc.date;

  const cfg = {
    valid:   { dotBg: 'bg-icc-ok',      dotInner: 'bg-icc-ok/40',
               labelKey: 'papers.calibration.status_valid', text: 'text-icc-ok' },
    stale:   { dotBg: 'bg-warn',        dotInner: 'bg-warn/40',
               labelKey: 'papers.calibration.status_stale', text: 'text-warn' },
    pending: { dotBg: 'bg-warn',        dotInner: 'bg-warn/40',
               labelKey: 'papers.calibration.status_pending', text: 'text-text-muted' },
    never:   { dotBg: 'bg-sunken-deep', dotInner: 'bg-sunken-deep/60',
               labelKey: 'papers.calibration.status_never', text: 'text-text-muted' },
  }[status] || {
    dotBg: 'bg-sunken-deep', dotInner: 'bg-sunken-deep/60',
    labelKey: 'papers.calibration.status_none_label', text: 'text-text-muted',
  };

  // Contextual button label based on the status
  let buttonLabel;
  if (busy) {
    buttonLabel = t('papers.calibration.button_starting');
  } else if (justErrored) {
    buttonLabel = t('papers.calibration.button_calibrate_again');
  } else if (status === 'valid') {
    buttonLabel = t('papers.calibration.button_recalibrate');
  } else if (status === 'pending') {
    buttonLabel = t('papers.calibration.button_launch');
  } else {
    buttonLabel = t('papers.calibration.button_calibrate');
  }

  return (
    <div className="card flex items-center gap-3 p-3.5">
      <div
        aria-hidden="true"
        className={`relative w-10 h-10 rounded-full ${cfg.dotBg} flex items-center justify-center flex-shrink-0`}>
        <div className={`w-5 h-5 rounded-full ${cfg.dotInner}`}/>
      </div>
      <div className="flex-1 min-w-0">
        {justErrored ? (
          <p className="text-[13px] font-medium text-danger flex items-center gap-1">
            <AlertTriangle size={12} strokeWidth={2.5} aria-hidden="true"/>
            {t('papers.calibration.finished_error')}
          </p>
        ) : justFinished ? (
          <p className="text-[13px] font-medium text-icc-ok flex items-center gap-1">
            <CheckCircle2 size={12} strokeWidth={2.5} aria-hidden="true"/>
            {t('papers.calibration.finished_success')}
          </p>
        ) : (
          <p className={`text-[13px] font-medium ${cfg.text}`}>
            {t(cfg.labelKey)}
          </p>
        )}
        {date ? (
          <p className="text-xs2 text-text-muted font-mono mt-0.5">
            {_fmtDate(date, i18n.language)}
          </p>
        ) : justErrored ? (
          <p className="text-xs2 text-danger mt-0.5 break-words">
            {job?.error}
          </p>
        ) : status === 'pending' ? (
          <p className="text-xs2 text-text-muted italic mt-0.5">
            {t('papers.calibration.pending_subtitle')}
          </p>
        ) : (
          <p className="text-xs2 text-text-faint italic mt-0.5">
            {t('papers.calibration.no_record')}
          </p>
        )}
      </div>
      <button
        type="button"
        onClick={onStart}
        disabled={busy || !!disabledReason}
        title={
          disabledReason
            || (status === 'valid' && date ? t('papers.calibration.button_tooltip_last', { date: _fmtDate(date, i18n.language) }) : null)
            || (status === 'stale' && date ? t('papers.calibration.button_tooltip_stale', { date: _fmtDate(date, i18n.language) }) : null)
            || (status === 'pending' ? t('papers.calibration.button_tooltip_pending') : null)
            || t('papers.calibration.button_tooltip_default')
        }
        aria-label={disabledReason
          ? t('papers.calibration.button_aria_disabled', { label: buttonLabel, reason: disabledReason })
          : t('papers.calibration.button_aria_default')}
        className={`px-3 py-1.5 rounded-md text-xs2 font-medium transition-colors ${
          busy
            ? 'bg-sunken text-text-faint cursor-wait'
            : disabledReason
              ? 'bg-sunken text-text-faint cursor-not-allowed'
              : 'bg-accent hover:bg-accent-press text-on-accent'
        }`}>
        {buttonLabel}
      </button>
    </div>
  );
}


function RunningCard({ job }) {
  const { t } = useTranslation();
  const pct = typeof job?.progress === 'number' && job.progress >= 0
    ? job.progress
    : null;
  const phase = _phaseLabel(job?.process, t);
  const eta = pct != null && pct > 5 ? _estimateEta(job?.elapsed, pct, t) : null;

  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-2.5">
        <Loader2 size={14} strokeWidth={2.5} className="text-accent animate-spin" aria-hidden="true"/>
        <p className="text-[13px] font-medium text-text-strong">
          {t('papers.calibration.in_progress_title')}
        </p>
      </div>

      <div className="w-full h-2 bg-sunken rounded-full overflow-hidden mb-2">
        <div
          className="h-full bg-accent transition-all duration-500"
          style={{ width: pct != null ? `${Math.max(2, pct)}%` : '5%' }}
          role="progressbar"
          aria-valuenow={pct ?? 0}
          aria-valuemin={0}
          aria-valuemax={100}/>
      </div>

      <div className="flex items-baseline justify-between gap-2 text-xs2">
        <span className="text-text-muted">
          {pct != null ? `${pct}%` : t('papers.calibration.in_progress_init')}
          {phase && <span className="text-text-faint"> · {phase}</span>}
        </span>
        {eta && (
          <span className="text-text-faint italic">
            {t('papers.calibration.eta_remaining', { eta })}
          </span>
        )}
      </div>

      <p className="text-tiny text-text-faint italic mt-2">
        {t('papers.calibration.in_progress_disclaimer')}
      </p>
    </div>
  );
}


// ─── Helpers ──────────────────────────────────────────────────────────


function _fmtDate(iso, lang) {
  if (!iso) return '';
  const locale = lang === 'en' ? 'en-US' : 'fr-FR';
  try {
    return new Intl.DateTimeFormat(locale, {
      year: 'numeric', month: 'long', day: 'numeric',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function _phaseLabel(process, t) {
  if (!process) return '';
  const key = {
    PRINTING:  'papers.calibration.phase_printing',
    DRYING:    'papers.calibration.phase_drying',
    SCANNING:  'papers.calibration.phase_scanning',
    COMPUTING: 'papers.calibration.phase_computing',
    DONE:      'papers.calibration.phase_done',
  }[process];
  return key ? t(key) : process;
}

function _estimateEta(elapsed, pct, t) {
  if (!elapsed || !pct) return null;
  const totalEst = elapsed / (pct / 100);
  const remaining = Math.max(0, totalEst - elapsed);
  const minutes = Math.ceil(remaining / 60);
  if (minutes < 1) return t('papers.calibration.eta_lt_minute');
  return t('papers.calibration.eta_minutes', { count: minutes });
}
