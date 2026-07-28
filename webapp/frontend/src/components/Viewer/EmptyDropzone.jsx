import { useRef } from 'react';
import { UploadCloud, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * Dropzone visible when no file is loaded (state A or D).
 * The drop-handler is actually on the whole Viewer — here we only have the visual
 * + the "Browse" button that opens a hidden file input.
 *
 * Patch 1 (25/05/2026): during the upload → info → preview pipeline
 * (loading=true), we show a loading overlay over the normal
 * dropzone. Also blocks the Browse click to avoid a
 * double-trigger.
 */
export default function EmptyDropzone({ onPick, dragging = false, loading = false }) {
  const { t } = useTranslation();
  const inputRef = useRef(null);

  // During loading, we keep the dropzone visual but overlay
  // an opaque overlay + spinner. The zone stays visible (no layout
  // jump) and re-drop is blocked on the Viewer side (acceptingFiles=false).
  return (
    <div className={`flex-1 m-4 mb-2 rounded-xl bg-sunken border-[1.5px] border-dashed
                     relative
                     flex flex-col items-center justify-center gap-4 text-text-muted
                     transition-colors
                     ${dragging ? 'border-accent bg-accent/5' : 'border-border-strong'}
                     ${loading ? 'opacity-90' : ''}`}>
      <UploadCloud size={48} strokeWidth={1.3} aria-hidden="true"/>
      <div className="text-center">
        <div className="text-[17px] font-medium text-text-strong mb-1.5 tracking-tight">
          {t('print.dropzone_title')}
        </div>
        <div className="text-sm2 text-text-faint">
          {t('print.dropzone_subtitle')}
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".tif,.tiff,image/tiff"
        className="hidden"
        disabled={loading}
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onPick?.(f); e.target.value = ''; }}/>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={loading}
        className="mt-2 px-4 py-1.5 rounded-md border border-border-strong text-sm2 font-medium text-text-muted hover:text-text-strong hover:bg-surface transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
        {t('print.dropzone_browse')}
      </button>

      {/* Loading overlay (Patch 1) — covers the dropzone to visually block
          accidental re-drop and signal that the backend is
          working (upload → info → preview, 2-5 s typical). */}
      {loading && (
        <div
          role="status"
          aria-live="polite"
          aria-label={t('print.dropzone_loading_aria')}
          className="absolute inset-0 rounded-xl bg-surface/85 backdrop-blur-[1px] flex flex-col items-center justify-center gap-3">
          <Loader2 size={32} strokeWidth={1.8} className="animate-spin text-accent" aria-hidden="true"/>
          <div className="text-center">
            <div className="text-sm font-medium text-text-strong">{t('print.dropzone_loading_title')}</div>
            <div className="text-xs2 text-text-muted mt-1 leading-snug">
              {t('print.dropzone_loading_subtitle')}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
