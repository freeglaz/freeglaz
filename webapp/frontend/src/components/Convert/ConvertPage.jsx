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
  relative_bpc: 'collink -s -ir -p <abstract BPC>',
};

// The two freeglaz custom entries insert an abstract via -s -ir -p (NOT -G): the
// τ-controlled luminance-priority one, and the parameter-less Relative + BPC one.
const FREEGLAZ_CUSTOM = new Set(['luminance_priority', 'relative_bpc']);

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

  const isLumPriority = gamutIntent === 'luminance_priority';   // τ cursor only here
  const isFreeglazCustom = FREEGLAZ_CUSTOM.has(gamutIntent);    // -s -ir -p abstract (badge, no ia/vc)

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
    <>
      {/* LEFT — drop zone that becomes the preview. Mirrors the Print viewer:
          one flex-1 column, drop handlers on the whole surface, dropzone and
          preview share the same slot so there is no layout jump. */}
      <div
        className="flex-1 flex flex-col min-w-0"
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}>
        {!file ? (
          <div className={`flex-1 m-4 mb-2 rounded-xl bg-sunken border-[1.5px] border-dashed
                           relative flex flex-col items-center justify-center gap-4 text-text-muted
                           transition-colors
                           ${dragging ? 'border-accent bg-accent/5' : 'border-border-strong'}
                           ${loading ? 'opacity-90' : ''}`}>
            <UploadCloud size={48} strokeWidth={1.3} aria-hidden="true"/>
            <div className="text-[17px] font-medium text-text-strong tracking-tight text-center">
              {t('convert.drop_hint')}
            </div>
            {loading && (
              <div
                role="status"
                aria-live="polite"
                className="absolute inset-0 rounded-xl bg-surface/85 backdrop-blur-[1px] flex flex-col items-center justify-center gap-3">
                <Loader2 size={32} strokeWidth={1.8} className="animate-spin text-accent" aria-hidden="true"/>
                <div className="text-sm font-medium text-text-strong">{t('convert.loading')}</div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex-1 flex flex-col min-h-0 m-4 mb-2">
            {/* Backend PNG preview (bounded, purely informative) */}
            <div className="flex-1 min-h-0 rounded-xl bg-sunken border border-border-soft flex items-center justify-center overflow-hidden p-4">
              <img
                src={getFilePreviewUrl(fileId)}
                alt={file.info.filename}
                className="max-h-full max-w-full object-contain rounded-md"/>
            </div>
            <div className="mt-2 flex items-center justify-center gap-2 text-text-muted min-w-0">
              <FileImage size={14} className="text-accent flex-shrink-0" aria-hidden="true"/>
              <span className="text-xs2 font-medium truncate">{file.info.filename}</span>
            </div>
          </div>
        )}
        {loadError && (
          <p className="px-4 pb-2 text-xs2 text-danger text-center">{loadError}</p>
        )}
      </div>

      {/* RIGHT — fixed params panel. Mirrors the Print sidebar: 380px wide,
          flex-col with a scrolling body and a pinned footer holding the actions. */}
      <aside className="w-[380px] bg-bg border-l border-border-soft flex flex-col">
        <header className="px-5 pt-4 pb-3.5">
          <h1 className="text-[13.5px] font-semibold text-text-strong tracking-tight">{t('convert.title')}</h1>
          <p className="text-xs2 text-text-muted mt-0.5">{t('convert.subtitle')}</p>
        </header>

        <div className="flex-1 px-5 overflow-y-auto no-scrollbar">
          {/* 1. Detected source space + TRC (info about the loaded file, in head
              of the panel — analogous to PaperCard in the Print sidebar). */}
          {file && (
            <>
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
            </>
          )}

          {file && sourceInfo?.has_profile && (
            <>
              {/* 2. collink gamut intent (+ command + desc, always visible) — the
                  primary choice. Real term, real command; each carries its
                  trade-off; no hidden "best". */}
              <div className="h-5"/>
              <Label>{t('convert.gamut_label')}</Label>
              <div className="flex items-center gap-2">
                <div className="flex-1 min-w-0">
                  <Select
                    value={gamutIntent}
                    onChange={setGamutIntent}
                    options={[
                      { value: 'relative', label: t('convert.gamut_relative') },
                      { value: 'relative_bpc', label: t('convert.gamut_relative_bpc') },
                      { value: 'luminance_matched', label: t('convert.gamut_luminance_matched') },
                      { value: 'perceptual', label: t('convert.gamut_perceptual') },
                      { value: 'luminance_preserving', label: t('convert.gamut_luminance_preserving') },
                      { value: 'luminance_priority', label: t('convert.gamut_luminance_priority') },
                    ]}/>
                </div>
                {isFreeglazCustom && (
                  <Badge kind="info">{t('convert.gamut_freeglaz')}</Badge>
                )}
              </div>
              {/* Real command driven (short form). Full command with -v -qh + profile
                  paths + effective τ goes to the job trace (backend log). */}
              <p className="text-xs2 font-mono text-text-muted mt-1.5">{GAMUT_COMMAND[gamutIntent]}</p>
              <p className="text-xs2 text-text-muted mt-1">{t(`convert.gamut_desc_${gamutIntent}`)}</p>

              {/* τ cursor — ONLY for luminance_priority. Orientation (verified):
                  LEFT = low τ (0.5) = luminance protected ; RIGHT = high τ (2.0) =
                  chroma preserved. The numeric value is shown (traceability). */}
              {isLumPriority && (
                <div className="mt-3">
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
                  <p className="text-xs2 text-text-muted mt-1.5">{t('convert.tau_help')}</p>
                </div>
              )}

              {/* 3. Quality + gloss enhancer (stacked in the narrow panel) */}
              <div className="h-5"/>
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

              {/* 4. Image-aware + dest viewing conditions — native -G strategies
                  only. Long descriptions folded behind the Field "?" tooltip so
                  the panel stays compact (no text removed, help = native title). */}
              {!isFreeglazCustom && (
                <>
                  <Field label={t('convert.image_aware_label')} help={t('convert.image_aware_help')}>
                    <Toggle on={imageAware} onChange={setImageAware}/>
                  </Field>
                  <Field
                    label={t('convert.viewcond_label')}
                    help={`${t(`convert.viewcond_desc_${destViewcond}`)}\n\n${t('convert.viewcond_note')}`}>
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
                  </Field>
                </>
              )}

              {/* 5. Destination (informational — not a selector), just above the
                  pinned action footer. */}
              <div className="h-5"/>
              <p className="text-xs2 text-text-muted flex items-center gap-1.5">
                <ArrowRight size={13} className="flex-shrink-0" aria-hidden="true"/>
                {noPaper
                  ? t('convert.dest_no_paper')
                  : t('convert.dest_loaded', { paper: paper.name || paper.mediaid || '—' })}
              </p>
            </>
          )}

          <div className="h-6"/>
        </div>

        {/* Pinned footer — the two actions, anchored at the bottom, equal weight
            (no primary/secondary hierarchy). Save to disk, or hand off to the
            Print tab (device passthrough). */}
        <div className="px-5 pt-3.5 pb-4 border-t border-border-soft">
          <div className="flex gap-3">
            <button
              type="button"
              disabled={!canConvert}
              onClick={() => runConvert(false)}
              className={`flex-1 inline-flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-sm font-semibold tracking-tight transition-colors
                ${canConvert
                  ? 'bg-accent hover:bg-accent-press text-on-accent shadow-sm cursor-pointer'
                  : 'bg-sunken-deep text-text-faint cursor-not-allowed'}`}>
              {converting && <Loader2 size={15} className="animate-spin" aria-hidden="true"/>}
              {t('convert.run_button')}
            </button>
            <button
              type="button"
              disabled={!canConvert}
              onClick={() => runConvert(true)}
              title={t('convert.run_and_print_help')}
              className={`flex-1 inline-flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-sm font-semibold tracking-tight transition-colors
                ${canConvert
                  ? 'bg-accent hover:bg-accent-press text-on-accent shadow-sm cursor-pointer'
                  : 'bg-sunken-deep text-text-faint cursor-not-allowed'}`}>
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
      </aside>
    </>
  );
}
