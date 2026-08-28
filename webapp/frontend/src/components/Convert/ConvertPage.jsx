import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { UploadCloud, FileImage, ArrowRight, Loader2, AlertTriangle, Printer } from 'lucide-react';
import { useFileLoader } from '../../hooks/useFileLoader.js';
import { getConvertSourceInfo, postConvert, getFilePreviewUrl } from '../../api/client.js';
import Field from '../ui/Field.jsx';
import Label from '../ui/Label.jsx';
import Segmented from '../ui/Segmented.jsx';
import Toggle from '../ui/Toggle.jsx';
import Select from '../ui/Select.jsx';
import Slider from '../ui/Slider.jsx';
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
// Short collink command shown on screen per gamut intent (technical, language-
// independent). MUST match the argv the backend builds (non-regression). The last
// entry is NOT a -G intent — its command shows -s + abstract, and it is freeglaz-marked.
const GAMUT_COMMAND = {
  relative: 'collink -G -ir',
  luminance_matched: 'collink -G -ila',
  perceptual: 'collink -G -ip',
  luminance_preserving: 'collink -G -ilp',
  luminance_priority: 'collink -s -ir -p <abstract τ>',
};

export default function ConvertPage({ paper, offline, onSendToPrint }) {
  const { t } = useTranslation();
  const { file, error: loadError, loading, load } = useFileLoader();

  const [dragging, setDragging] = useState(false);
  const [sourceInfo, setSourceInfo] = useState(null);   // { has_profile, color_space, trc } | null
  const [sourceLoading, setSourceLoading] = useState(false);

  // collink gamut intent: relative | luminance_matched | perceptual |
  // luminance_preserving | luminance_priority. Default 'relative' = current behaviour.
  // (Value slugs are internal keys; the user sees the i18n labels + the command.)
  const [gamutIntent, setGamutIntent] = useState('relative');
  const [tau, setTau] = useState(1.0);                   // luminance_priority only: 0.5 → 2.0
  const [quality, setQuality] = useState('h');          // l | m | h | u
  const [ge, setGe] = useState('OFF');                   // FULLPAGE | OFF (resident selection)
  const [imageAware, setImageAware] = useState(false);   // image-aware axis (native -G intents only)
  const [destViewcond, setDestViewcond] = useState('default');  // collink -d preset (native -G only)

  const isLumPriority = gamutIntent === 'luminance_priority';

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

  // toPrint=false → download the device TIFF; toPrint=true → hand it off to the
  // Print tab (App uploads it + navigates; device passthrough, no auto-print).
  const runConvert = useCallback(async (toPrint = false) => {
    if (!fileId) return;
    setConverting(true);
    setConvertError(null);
    setDone(null);
    try {
      const { blob, filename } = await postConvert({
        file_id: fileId, gamut_intent: gamutIntent, tau, quality, gloss_enhancer: ge,
        image_aware: imageAware, dest_viewcond: destViewcond,
      });
      if (toPrint) {
        await onSendToPrint?.(blob, filename);
      } else {
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
      }
    } catch (e) {
      const d = e.detail;
      setConvertError(
        d?.code === 'unsupported_lut' ? t('convert.error_unsupported_lut')
          : (d && typeof d === 'object' && d.message) ? d.message
            : (e.message || t('convert.error_generic')),
      );
    } finally {
      setConverting(false);
    }
  }, [fileId, gamutIntent, tau, quality, ge, imageAware, destViewcond, onSendToPrint, t]);

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

        {/* collink gamut intent (+ its command + its cost, always visible) — the
            primary choice. Real term (collink gamut intent, not an ICC profile
            intent), real command shown; each carries its trade-off; no hidden "best". */}
        {file && sourceInfo?.has_profile && (
          <div className="mt-6">
            <div className="flex items-center gap-3">
              <Label>{t('convert.gamut_label')}</Label>
              <Select
                value={gamutIntent}
                onChange={setGamutIntent}
                options={[
                  { value: 'relative', label: t('convert.gamut_relative') },
                  { value: 'luminance_matched', label: t('convert.gamut_luminance_matched') },
                  { value: 'perceptual', label: t('convert.gamut_perceptual') },
                  { value: 'luminance_preserving', label: t('convert.gamut_luminance_preserving') },
                  { value: 'luminance_priority', label: t('convert.gamut_luminance_priority') },
                ]}/>
              {isLumPriority && (
                <Badge kind="info">{t('convert.gamut_freeglaz')}</Badge>
              )}
            </div>
            {/* Real command driven (short form). Full command with -v -qh + profile
                paths + effective τ goes to the job trace (backend log). */}
            <p className="text-xs2 font-mono text-text-muted mt-1.5">{GAMUT_COMMAND[gamutIntent]}</p>
            <p className="text-xs2 text-text-muted mt-1 max-w-xl">
              {t(`convert.gamut_desc_${gamutIntent}`)}
            </p>

            {/* τ cursor — ONLY for luminance_priority. Orientation (verified):
                LEFT = low τ (0.5) = luminance protected ; RIGHT = high τ (2.0) =
                chroma preserved. The numeric value is shown (traceability). */}
            {isLumPriority && (
              <div className="mt-3 max-w-md">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-text-strong">{t('convert.tau_label')}</span>
                  <span className="text-xs2 font-mono text-text-muted">τ = {tau.toFixed(2)}</span>
                </div>
                <div className="mt-1.5">
                  <Slider value={tau} min={0.5} max={2.0} step={0.05} onChange={setTau}/>
                </div>
                <div className="flex justify-between text-xs2 text-text-faint mt-1">
                  <span>{t('convert.tau_luminance_end')}</span>
                  <span>{t('convert.tau_chroma_end')}</span>
                </div>
                <p className="text-xs2 text-text-muted mt-1.5 max-w-xl">{t('convert.tau_help')}</p>
              </div>
            )}
          </div>
        )}

        {/* Quality + gloss enhancer */}
        {file && sourceInfo?.has_profile && (
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
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

        {/* Image-aware + dest viewing conditions apply to the native -G strategies
            only (they are gamut-mapping-mode features) → hidden for luminance_priority. */}
        {file && sourceInfo?.has_profile && !isLumPriority && (
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

        {file && sourceInfo?.has_profile && !isLumPriority && (
          <div className="mt-4">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-text-strong">{t('convert.viewcond_label')}</span>
              <Select
                value={destViewcond}
                onChange={setDestViewcond}
                options={[
                  { value: 'default', label: t('convert.viewcond_default_label') },
                  { value: 'pp', label: 'pp' },
                  { value: 'pc', label: 'pc' },
                  { value: 'pe', label: 'pe' },
                  { value: 'pm', label: 'pm' },
                ]}/>
            </div>
            <p className="text-xs2 text-text-muted mt-1.5 max-w-xl">
              {t(`convert.viewcond_desc_${destViewcond}`)}
            </p>
            <p className="text-xs2 text-text-faint italic mt-1 max-w-xl">
              {t('convert.viewcond_note')}
            </p>
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

        {/* Action — save to disk, or hand off to the Print tab (device passthrough) */}
        {file && sourceInfo?.has_profile && (
          <div className="mt-6">
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={!canConvert}
                onClick={() => runConvert(false)}
                className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-md text-sm font-semibold transition-colors
                  ${canConvert
                    ? 'bg-accent text-white hover:bg-accent/90'
                    : 'bg-sunken text-text-faint cursor-not-allowed'}`}>
                {converting && <Loader2 size={15} className="animate-spin" aria-hidden="true"/>}
                {t('convert.run_button')}
              </button>
              <button
                type="button"
                disabled={!canConvert}
                onClick={() => runConvert(true)}
                title={t('convert.run_and_print_help')}
                className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-md text-sm font-semibold border transition-colors
                  ${canConvert
                    ? 'border-accent text-accent hover:bg-accent/10'
                    : 'border-border text-text-faint cursor-not-allowed'}`}>
                <Printer size={15} aria-hidden="true"/>
                {t('convert.run_and_print_button')}
              </button>
            </div>
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
