import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { UploadCloud, FileImage, ArrowRight, Loader2, AlertTriangle } from 'lucide-react';
import { useFileLoader } from '../../hooks/useFileLoader.js';
import { getConvertSourceInfo, postConvert, getFilePreviewUrl } from '../../api/client.js';
import Field from '../ui/Field.jsx';
import Label from '../ui/Label.jsx';
import Segmented from '../ui/Segmented.jsx';
import Toggle from '../ui/Toggle.jsx';
import Badge from '../ui/Badge.jsx';

/**
 * "Convert" tab — SOCLE (JALON 1).
 *
 * Upstream, separate, bypassable stage (never touches the Print module): drop an
 * image, read its embedded space + TRC, and build a device TIFF via an Argyll
 * DeviceLink toward the LOADED paper's resident (collink -G + cctiff, backend).
 *
 * Deliberately minimal: no DEST selector (dest = loaded paper, resolved
 * backend-side), no preview geometry, no "Convert and print". The only output is
 * a device file saved to disk.
 */
export default function ConvertPage({ paper, offline }) {
  const { t } = useTranslation();
  const { file, error: loadError, loading, load } = useFileLoader();

  const [dragging, setDragging] = useState(false);
  const [sourceInfo, setSourceInfo] = useState(null);   // { has_profile, color_space, trc } | null
  const [sourceLoading, setSourceLoading] = useState(false);

  const [intent, setIntent] = useState('r');            // collink -i choice: r | p | lp
  const [quality, setQuality] = useState('h');          // l | m | h | u
  const [ge, setGe] = useState('OFF');                   // FULLPAGE | OFF (resident selection)
  const [imageAware, setImageAware] = useState(false);   // image-aware axis (orthogonal to intent)

  const [converting, setConverting] = useState(false);
  const [convertError, setConvertError] = useState(null);
  const [done, setDone] = useState(null);               // filename of the last saved file

  const fileId = file?.info?.id || null;

  // On a freshly dropped image → read its embedded space + TRC (detection).
  useEffect(() => {
    if (!fileId) { setSourceInfo(null); return; }
    let alive = true;
    setSourceLoading(true);
    setConvertError(null);
    setDone(null);
    getConvertSourceInfo(fileId)
      .then((info) => { if (alive) setSourceInfo(info); })
      .catch(() => { if (alive) setSourceInfo(null); })
      .finally(() => { if (alive) setSourceLoading(false); });
    return () => { alive = false; };
  }, [fileId]);

  const handleDragOver = (e) => { e.preventDefault(); if (!loading) setDragging(true); };
  const handleDragLeave = () => setDragging(false);
  const handleDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) load(f);
  };

  const runConvert = useCallback(async () => {
    if (!fileId) return;
    setConverting(true);
    setConvertError(null);
    setDone(null);
    try {
      const { blob, filename } = await postConvert({
        file_id: fileId, intent, quality, gloss_enhancer: ge, image_aware: imageAware,
      });
      // Trigger a browser download of the device TIFF.
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setDone(filename);
    } catch (e) {
      const d = e.detail;
      setConvertError(
        (d && typeof d === 'object' && d.message) ? d.message
          : (e.message || t('convert.error_generic')),
      );
    } finally {
      setConverting(false);
    }
  }, [fileId, intent, quality, ge, imageAware, t]);

  const noPaper = !paper;
  const noSourceProfile = sourceInfo && sourceInfo.has_profile === false;
  const canConvert = !!fileId && !!sourceInfo?.has_profile && !noPaper && !offline && !converting;

  return (
    <div className="flex-1 overflow-auto">
      <div className="max-w-3xl mx-auto px-6 py-8">
        <header className="mb-6">
          <h1 className="text-lg font-semibold text-text-strong">{t('convert.title')}</h1>
          <p className="text-sm text-text-muted mt-1">{t('convert.subtitle')}</p>
        </header>

        {/* Drop zone */}
        <div
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`rounded-lg border-2 border-dashed transition-colors p-8 text-center
            ${dragging ? 'border-accent bg-accent-soft' : 'border-border-soft bg-sunken'}`}>
          {loading ? (
            <div className="flex items-center justify-center gap-2 text-text-muted">
              <Loader2 size={18} className="animate-spin" aria-hidden="true"/>
              <span className="text-sm">{t('convert.loading')}</span>
            </div>
          ) : file ? (
            <div className="flex items-center justify-center gap-2 text-text-strong">
              <FileImage size={18} className="text-accent" aria-hidden="true"/>
              <span className="text-sm font-medium">{file.info.filename}</span>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2 text-text-muted">
              <UploadCloud size={28} strokeWidth={1.5} aria-hidden="true"/>
              <span className="text-sm">{t('convert.drop_hint')}</span>
            </div>
          )}
        </div>
        {loadError && (
          <p className="text-xs2 text-danger mt-2">{loadError}</p>
        )}

        {/* Preview of the dropped image (backend PNG, bounded) — visual feedback
            that the file loaded, mirroring the Print viewer. Purely informative. */}
        {file && fileId && (
          <div className="mt-4 flex justify-center">
            <img
              src={getFilePreviewUrl(fileId)}
              alt={file.info.filename}
              className="max-h-64 max-w-full rounded-md border border-border-soft object-contain bg-sunken"/>
          </div>
        )}

        {/* Detected source space + TRC */}
        {file && (
          <div className="mt-5">
            <Label>{t('convert.source_section')}</Label>
            {sourceLoading ? (
              <div className="flex items-center gap-2 text-text-muted text-sm">
                <Loader2 size={14} className="animate-spin" aria-hidden="true"/>
                <span>{t('convert.reading_source')}</span>
              </div>
            ) : noSourceProfile ? (
              <div className="flex items-start gap-2 rounded-md bg-danger/10 border border-danger/30 px-3 py-2.5">
                <AlertTriangle size={16} className="text-danger mt-0.5 flex-shrink-0" aria-hidden="true"/>
                <p className="text-xs2 text-danger">{t('convert.no_source_profile')}</p>
              </div>
            ) : sourceInfo ? (
              <div className="flex flex-wrap items-center gap-2">
                <Badge kind="info">{(sourceInfo.color_space || '—').trim()}</Badge>
                {sourceInfo.trc?.primary_family_label && (
                  <Badge kind="neutral">{sourceInfo.trc.primary_family_label}</Badge>
                )}
              </div>
            ) : null}
          </div>
        )}

        {/* Parameters + action — only meaningful with a valid source profile */}
        {file && sourceInfo?.has_profile && (
          <div className="mt-6 grid gap-4 sm:grid-cols-3">
            <Field label={t('convert.intent_label')} help={t('convert.intent_help')}>
              <Segmented
                options={[
                  { value: 'r', label: t('convert.intent_r') },
                  { value: 'p', label: t('convert.intent_p') },
                  { value: 'lp', label: t('convert.intent_lp') },
                ]}
                value={intent}
                onChange={setIntent}/>
            </Field>
            <Field label={t('convert.quality_label')} help={t('convert.quality_help')}>
              <Segmented
                options={[
                  { value: 'l', label: t('convert.quality_l') },
                  { value: 'm', label: t('convert.quality_m') },
                  { value: 'h', label: t('convert.quality_h') },
                  { value: 'u', label: t('convert.quality_u') },
                ]}
                value={quality}
                onChange={setQuality}/>
            </Field>
            <Field label={t('convert.ge_label')} help={t('convert.ge_help')}>
              <Segmented
                options={[
                  { value: 'OFF', label: t('convert.ge_off') },
                  { value: 'FULLPAGE', label: t('convert.ge_on') },
                ]}
                value={ge}
                onChange={setGe}/>
            </Field>
          </div>
        )}

        {/* Image-aware — orthogonal axis to the intent (independent toggle) */}
        {file && sourceInfo?.has_profile && (
          <div className="mt-4 flex items-start gap-3">
            <div className="pt-0.5">
              <Toggle on={imageAware} onChange={setImageAware}/>
            </div>
            <div>
              <div className="text-sm font-medium text-text-strong">{t('convert.image_aware_label')}</div>
              <div className="text-xs2 text-text-muted mt-0.5 max-w-xl">{t('convert.image_aware_help')}</div>
            </div>
          </div>
        )}

        {/* Destination (informational — not a selector) */}
        {file && sourceInfo?.has_profile && (
          <p className="mt-4 text-xs2 text-text-muted flex items-center gap-1.5">
            <ArrowRight size={13} aria-hidden="true"/>
            {noPaper
              ? t('convert.dest_no_paper')
              : t('convert.dest_loaded', { paper: paper.name || paper.mediaid || '—' })}
          </p>
        )}

        {/* Action */}
        {file && sourceInfo?.has_profile && (
          <div className="mt-6">
            <button
              type="button"
              disabled={!canConvert}
              onClick={runConvert}
              className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-md text-sm font-semibold transition-colors
                ${canConvert
                  ? 'bg-accent text-white hover:bg-accent/90'
                  : 'bg-sunken text-text-faint cursor-not-allowed'}`}>
              {converting && <Loader2 size={15} className="animate-spin" aria-hidden="true"/>}
              {t('convert.run_button')}
            </button>
            {offline && !noPaper && (
              <p className="text-xs2 text-text-muted mt-2">{t('convert.offline_hint')}</p>
            )}
            {convertError && (
              <p className="text-xs2 text-danger mt-2">{convertError}</p>
            )}
            {done && (
              <p className="text-xs2 text-icc-ok mt-2">{t('convert.saved', { filename: done })}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
