import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Loader2, ImageOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getJobPreviewUrl } from '../../api/client.js';

// Minimum margin between the popover and the viewport edges (top & bottom)
const VIEWPORT_MARGIN_PX = 16;

/**
 * Popover preview thumbnail of a job (14 P1.E / P3.H click).
 *
 * Lazy load via <img>, 3 sub-states: loading, ready, error. No error
 * toast — an unavailable preview is expected for Deleted jobs, we don't
 * break the UI.
 *
 * **Positioning (P3.I collision detection)**:
 * - Horizontal: fixed at 480 px from the right edge (panel width 460 + 20)
 * - Vertical: aligned on the top of the selected job by default. If the
 *   popover would overflow the viewport at the bottom → flip / shift up to
 *   stay fully visible with a 16 px margin. If too large for the viewport
 *   → pinned to the top with the margin.
 * - The computation accounts for the REAL height of the popover (measured
 *   after render via useLayoutEffect + ResizeObserver). The height
 *   varies depending on whether the image is still loading (fixed
 *   placeholder) or ready (composite ratio, can reach 370+ px in A4 portrait).
 *
 * @param {object} p
 * @param {string} [p.id] — DOM id for aria-controls from JobItem
 * @param {object} p.job — the job (uuid + meta for the meta line)
 * @param {{top: number}} p.position — vertical coordinate of the job at click time
 */
export default function JobPreviewPopover({ id, job, position }) {
  const { t } = useTranslation();
  const [state, setState] = useState('loading'); // 'loading' | 'ready' | 'error'
  const popoverRef = useRef(null);
  // topPx = effective vertical position after collision detection.
  // Initialized to the job position (basic clamp) — will be refined
  // by useLayoutEffect as soon as the popover is in the DOM.
  const [topPx, setTopPx] = useState(() =>
    Math.max(VIEWPORT_MARGIN_PX, position?.top ?? VIEWPORT_MARGIN_PX),
  );

  // Reset state when the job changes (the user clicks on another job)
  useEffect(() => { setState('loading'); }, [job.uuid]);

  // ─── Collision detection (P3.I) ───────────────────────────────
  // Measures the real height of the popover and adjusts topPx so it
  // stays fully within the viewport. Re-run:
  //  - when position.top changes (another job selected)
  //  - when the popover size changes (loading → ready, the composite
  //    image can be taller than the loading placeholder)
  //  - when the window is resized
  useLayoutEffect(() => {
    const el = popoverRef.current;
    if (!el) return;

    const recompute = () => {
      const rect = el.getBoundingClientRect();
      const vh = window.innerHeight;
      const h = rect.height;
      const desired = position?.top ?? VIEWPORT_MARGIN_PX;

      let next;
      if (h + 2 * VIEWPORT_MARGIN_PX >= vh) {
        // Popover too large for the viewport → we accept the overflow
        // at the bottom but pin to the top to show the top of the
        // preview (where the image is, the meta line is less critical).
        next = VIEWPORT_MARGIN_PX;
      } else if (desired + h + VIEWPORT_MARGIN_PX > vh) {
        // Overflow at the bottom → shift up to the bottom margin
        next = vh - h - VIEWPORT_MARGIN_PX;
      } else if (desired < VIEWPORT_MARGIN_PX) {
        // Overflow at the top → shift down
        next = VIEWPORT_MARGIN_PX;
      } else {
        next = desired;
      }
      setTopPx(next);
    };

    recompute();

    // ResizeObserver: recompute if the popover size changes (image
    // load → composite ratio). Silent fallback if not supported.
    let observer = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(recompute);
      observer.observe(el);
    }
    window.addEventListener('resize', recompute);
    return () => {
      if (observer) observer.disconnect();
      window.removeEventListener('resize', recompute);
    };
  }, [position?.top, job.uuid]);

  const previewUrl = getJobPreviewUrl(job.uuid);

  return (
    <div
      ref={popoverRef}
      id={id}
      role="region"
      aria-label={t('queue.preview_aria', { name: job.name || t('queue.preview_default_name') })}
      className="fixed z-50 w-[240px] bg-surface border border-border-soft rounded-lg shadow-xl overflow-hidden pointer-events-none"
      style={{
        right: 480,  // panel width 460 + margin 20
        top: topPx,
      }}>
      {/* Image zone, paper ratio 195×275 by default */}
      <div className="relative bg-viewer-bg" style={{ aspectRatio: '195 / 275' }}>
        {state === 'loading' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-text-faint">
            <Loader2 size={20} className="animate-spin" aria-hidden="true"/>
            <span className="text-tiny">{t('queue.preview_loading')}</span>
          </div>
        )}
        {state === 'error' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-text-faint">
            <ImageOff size={20} aria-hidden="true"/>
            <span className="text-tiny">{t('queue.preview_unavailable')}</span>
            {/* Only claim "deleted" when the job REALLY is — an image load
                failure can also be a firmware preview needing admin auth, not
                a purge. Don't mislabel those as deleted. */}
            {job.status === 'Deleted' && (
              <span className="font-mono text-[10px] text-text-faint opacity-60">{t('queue.preview_job_deleted')}</span>
            )}
          </div>
        )}
        {/* The image is always rendered (to trigger onLoad/onError),
            but hidden during loading/error. */}
        <img
          src={previewUrl}
          alt=""
          onLoad={() => setState('ready')}
          onError={() => setState('error')}
          className={`absolute inset-0 w-full h-full object-contain transition-opacity ${state === 'ready' ? 'opacity-100' : 'opacity-0'}`}/>
      </div>
      {/* Meta line in mono — page · pdl · quality */}
      <div className="px-2.5 py-1.5 font-mono text-tiny text-text-faint border-t border-border-soft tabular-nums">
        {[
          `page ${1}/${job.number_of_pages || 1}`,
          job.pdl_name || '—',
          job.print_quality || '—',
        ].join(' · ')}
      </div>
    </div>
  );
}
