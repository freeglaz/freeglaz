import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, CheckCircle2, Loader2, Ruler } from 'lucide-react';
import ExternalMeasureWizard from './ExternalMeasureWizard.jsx';
import { useExternalSession } from '../../hooks/useExternalSession.js';
import { durability, problem, progress, stepOf } from '../../lib/externalSession.js';
import { instrumentLabel } from '../../lib/measurementMethod.js';

/**
 * External measurement entry point, for a printtarg chart (Measurements drawer, ArgyllCMS
 * wizard, after printing). Shows what the backend says about the chart's session (saved
 * strips and patches, durability, a live run, a problem) and opens the wizard. Re-attaches to
 * an existing session by itself: the session lives in the backend.
 *
 * onPublished: called once when the chart's session becomes published (from this window or
 * another), so the page reloads the chart's measurements.
 */
export default function ExternalMeasurePanel({ chartId, instrument, nScans = 0, inLibrary = false, onPublished }) {
  const { t } = useTranslation();
  const session = useExternalSession(chartId);
  const [open, setOpen] = useState(false);
  const { snap } = session;
  const p = progress(snap);
  const d = durability(snap);
  const pb = problem(snap);
  const step = stepOf(snap);
  const prevState = useRef(null);
  const state = snap?.state;
  useEffect(() => {
    const prev = prevState.current;
    prevState.current = state ?? null;
    if (state === 'published' && prev && prev !== 'published') onPublished?.();
  }, [state, onPublished]);
  const label = !snap ? t('common.loading')
    : snap.running ? t('external_measure.panel.follow')
      : step === 'publish' && snap.state === 'complete' ? t('external_measure.panel.verify')
        : snap.state === 'idle' && p.passesSaved > 0 ? t('external_measure.panel.resume')
          : snap.state === 'idle' ? t('external_measure.panel.continue')
            : snap.state === 'published' ? t('external_measure.panel.measure_again', { instrument: instrumentLabel(t, instrument) })
              : t('external_measure.panel.measure', { instrument: instrumentLabel(t, instrument) });
  const showCounts = snap && (snap.session || (snap.state === 'published' && snap.last_session));
  return (
    <div className="rounded-md border border-border-soft bg-accent-soft px-3 py-2.5 text-xs2 space-y-1.5"
         data-external-panel={snap?.state || 'loading'}>
      <div className="flex items-center gap-1.5 font-semibold text-text-strong">
        <Ruler size={14} className="text-accent flex-shrink-0"/>
        {t('external_measure.panel.title', { instrument: instrumentLabel(t, instrument) })}
        {snap?.running && <span className="ml-auto inline-flex items-center gap-1 text-accent font-normal"><Loader2 size={12} className="animate-spin"/>{t('external_measure.running')}</span>}
      </div>
      {showCounts && (
        <p className="text-text-strong" data-saved={`${p.passesSaved}/${p.passesTotal}`}>
          {t('external_measure.panel.saved', { passes: p.passesSaved, total: p.passesTotal,
                                               patches: p.patchesSaved, patches_total: p.patchesTotal })}
          {d && d.kind !== 'full' && <span className={`ml-1.5 ${d.tone === 'danger' ? 'text-danger' : 'text-icc-warn-deep'}`}>· {t(`external_measure.durability_short.${d.kind}`)}</span>}
        </p>
      )}
      {snap?.state === 'published' && (
        <p className="text-success flex items-center gap-1" data-published="1">
          <CheckCircle2 size={12}/>{t('external_measure.state.published')}
          {!inLibrary && <span className="text-text-muted ml-1">· {t('external_measure.panel.published_next')}</span>}
        </p>
      )}
      {pb && (
        <p className={`flex items-start gap-1 ${pb.tone === 'warn' ? 'text-icc-warn-deep' : 'text-danger'}`} data-problem={pb.code}>
          <AlertTriangle size={12} className="mt-px flex-shrink-0"/>{t(`external_measure.problem.${pb.code}.title`)}
        </p>
      )}
      {nScans > 0 && <p className="text-text-muted">{t(inLibrary ? 'external_measure.panel.has_scans_here' : 'external_measure.panel.has_scans', { count: nScans })}</p>}
      <button type="button" onClick={() => setOpen(true)} disabled={!snap} data-action="open-external-measure"
              className="inline-flex items-center gap-1.5 text-xs2 font-semibold bg-accent text-on-accent px-3 py-1.5 rounded-md hover:bg-accent-press disabled:opacity-50">
        <Ruler size={13}/>{label}
      </button>
      <ExternalMeasureWizard open={open} onClose={() => setOpen(false)} chartId={chartId} session={session}/>
    </div>
  );
}
