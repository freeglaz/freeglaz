import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as api from '../../api/client.js';
import Segmented from '../ui/Segmented.jsx';
import { useGamutMesh, useReferenceMesh } from '../../hooks/useGamut.js';
import { sliceMeshAtL } from '../../lib/gamutSlice.js';
import GamutSlice2D from './GamutSlice2D.jsx';
import EngineExplainer from './EngineExplainer.jsx';

const INTENTS = ['perceptual', 'relative', 'saturation'];
const REFERENCES = ['none', 'sRGB', 'AdobeRGB', 'Rec.2020', 'ProPhoto', 'Rec.709'];

const PRIMARY_COLOR = '#0096D6';
const REFERENCE_COLOR = '#4080FF';


/**
 * "2D Slices" view — a*b* slice at constant L.
 *
 * Slicing 100% client-side from the mesh already fetched by
 * useGamutMesh. The L slider recomputes the slice at each tick.
 *
 * For the Mapping mode, this view will pass multiple intents
 * to GamutSlice2D instead of a single one.
 */
export default function Slice2DView({ source }) {
  const { t, i18n } = useTranslation();
  const [L0, setL0] = useState(50);
  const [intent, setIntent] = useState('relative');
  const [referenceName, setReferenceName] = useState(null);

  // Initial fetch of the default reference from Settings
  useEffect(() => {
    api.getSettings()
      .then((s) => setReferenceName(s?.inspection?.gamut_reference || 'sRGB'))
      .catch(() => setReferenceName('sRGB'));
  }, []);

  const { data: primaryMesh, loading: primaryLoading, error: primaryError } =
    useGamutMesh(source, intent);
  const { data: referenceMesh } =
    useReferenceMesh(referenceName === 'none' ? null : referenceName);

  // Slices memoized by (vertices, indices, L0).
  // Passes the primary's colors_srgb for per-hue interpolation on the
  // contour. Reference rendered as a neutral dashed line, no hue.
  const primarySlice = useMemo(() => {
    if (!primaryMesh?.vertices) return null;
    return sliceMeshAtL(
      primaryMesh.vertices,
      primaryMesh.indices,
      L0,
      primaryMesh.colors_srgb,
    );
  }, [primaryMesh, L0]);

  const referenceSlice = useMemo(() => {
    if (!referenceMesh?.vertices) return null;
    return sliceMeshAtL(referenceMesh.vertices, referenceMesh.indices, L0);
  }, [referenceMesh, L0]);

  // Composition for GamutSlice2D
  const sliceMeshes = useMemo(() => {
    const list = [];
    if (referenceSlice && referenceSlice.segments.length > 0) {
      list.push({
        name: 'reference',
        segments: referenceSlice.segments,
        color: REFERENCE_COLOR,
        dashed: true,
        opacity: 0.65,
        strokeWidth: 1.2,
      });
    }
    if (primarySlice && primarySlice.segments.length > 0) {
      list.push({
        name: 'primary',
        segments: primarySlice.segments,
        colors: primarySlice.colors,    // null if mesh without colors_srgb
        color: PRIMARY_COLOR,            // fallback if colors absent
        strokeWidth: 2.2,
      });
    }
    return list;
  }, [primarySlice, referenceSlice]);

  const fmtArea = (v) => {
    if (v == null || !isFinite(v)) return null;
    return v.toLocaleString(i18n.language === 'fr' ? 'fr-FR' : 'en-US',
      { maximumFractionDigits: 0 });
  };

  // Ratio only when both areas are quantified (and > 0).
  // primary.area=null = non-chainable contour (non-physical profile at the
  // current L). We show the textual info in the legend, not a NaN%.
  const ratio = (primarySlice?.area != null
                 && referenceSlice?.area != null
                 && referenceSlice.area > 0)
    ? (primarySlice.area / referenceSlice.area * 100).toFixed(1)
    : null;

  return (
    <div className="px-6 py-5 flex flex-col gap-4 h-full">
      <Toolbar
        intent={intent} setIntent={setIntent}
        referenceName={referenceName} setReferenceName={setReferenceName}/>

      <EngineExplainer i18nPrefix="slice2d"/>

      <LSlider L0={L0} setL0={setL0}/>

      <div className="flex-1 bg-bg border border-border-soft rounded-xl
                      overflow-hidden p-4 flex items-start justify-center
                      relative min-h-[420px]">
        {primaryLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-bg/85 z-10">
            <span className="inline-block w-3.5 h-3.5 border-2 border-accent
                             border-t-transparent rounded-full animate-spin
                             mr-2" aria-hidden="true"/>
            <span className="text-text-muted text-xs2">{t('slice2d.loading')}</span>
          </div>
        )}
        {primaryError && (
          <div className="absolute inset-0 flex items-center justify-center px-6">
            <div className="text-danger text-xs2 text-center max-w-md">
              <div className="font-semibold mb-1">{t('slice2d.error_title')}</div>
              <div className="text-text-muted font-mono">{primaryError}</div>
            </div>
          </div>
        )}
        {!primaryLoading && !primaryError && sliceMeshes.length > 0 && (
          <GamutSlice2D meshes={sliceMeshes} size={480}/>
        )}
        {!primaryLoading && !primaryError && sliceMeshes.length === 0 && (
          <div className="text-text-faint text-xs2 italic mt-20">
            {t('slice2d.empty_slice', { L: L0 })}
          </div>
        )}
      </div>

      <InteractionsHint/>

      <Legend
        L0={L0}
        primary={{ area: primarySlice?.area, color: PRIMARY_COLOR }}
        reference={{
          area: referenceSlice?.area,
          color: REFERENCE_COLOR,
          name: referenceName,
        }}
        ratio={ratio}
        fmt={fmtArea}/>
    </div>
  );
}


function Toolbar({ intent, setIntent, referenceName, setReferenceName }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-4 flex-wrap">
      <div className="flex items-center gap-2">
        <label className="text-tiny text-text-muted uppercase tracking-wider">
          {t('slice2d.toolbar_intent')}
        </label>
        <Segmented
          value={intent}
          onChange={setIntent}
          options={INTENTS.map((i) => ({ value: i, label: t(`slice2d.intent_${i}`) }))}/>
      </div>

      <div className="flex items-center gap-2">
        <label className="text-tiny text-text-muted uppercase tracking-wider">
          {t('slice2d.toolbar_reference')}
        </label>
        <select
          value={referenceName || 'sRGB'}
          onChange={(e) => setReferenceName(e.target.value)}
          className="px-2 py-1 text-xs2 bg-sunken/60 border border-border-soft
                     rounded text-text-strong focus:outline-none focus:border-accent">
          {REFERENCES.map((r) => (
            <option key={r} value={r}>
              {r === 'none' ? t('slice2d.reference_none') : r}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}


function LSlider({ L0, setL0 }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-3">
      <label className="text-tiny text-text-muted uppercase tracking-wider shrink-0">
        {t('slice2d.slider_label')}
      </label>
      <div className="flex-1 max-w-xl relative">
        <input
          type="range"
          min={0} max={100} step={1}
          value={L0}
          onChange={(e) => setL0(Number(e.target.value))}
          className="w-full accent-accent"
          aria-label={t('slice2d.slider_label')}/>
        {/* Graduations removed: the native thumb (accent-color, unstyled) has a
            variable UA width → no pure-CSS way to place markers aligned
            to the ends. The slider + the numeric L* display remain the source
            of truth (cursor at L0%, numeric = L0). */}
      </div>
      <div className="flex items-center gap-1">
        <span className="text-tiny text-text-faint font-mono">L*</span>
        <input
          type="number"
          min={0} max={100} step={1}
          value={L0}
          onChange={(e) => {
            const v = Number(e.target.value);
            if (Number.isFinite(v)) setL0(Math.max(0, Math.min(100, v)));
          }}
          className="w-14 px-1.5 py-0.5 text-xs2 font-mono bg-sunken/60
                     border border-border-soft rounded text-text-strong
                     focus:outline-none focus:border-accent"/>
      </div>
    </div>
  );
}


function InteractionsHint() {
  const { t } = useTranslation();
  return (
    <p className="text-tiny text-text-faint px-1 leading-snug">
      {t('slice2d.interactions_hint')}
    </p>
  );
}


function Legend({ L0, primary, reference, ratio, fmt }) {
  const { t } = useTranslation();
  const showRef = reference.name && reference.name !== 'none';
  const primaryFmt = fmt(primary.area);
  const refFmt = fmt(reference.area);
  return (
    <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-tiny text-text-muted px-1">
      <span>
        <span className="text-text-faint">L*&nbsp;=&nbsp;</span>
        <span className="text-text-strong font-mono">{L0}</span>
      </span>
      <span className="flex items-center gap-1.5">
        <span className="inline-block w-3 h-px"
              style={{ backgroundColor: primary.color, height: 2 }}/>
        <span className="text-text-faint">{t('slice2d.legend_primary_area')}</span>
        {primaryFmt !== null ? (
          <span className="text-text-strong font-mono">
            {primaryFmt}<span className="text-text-faint"> Lab²</span>
          </span>
        ) : (
          <span className="text-text-faint italic"
                title={t('slice2d.area_uncomputable_hint')}>
            {t('slice2d.area_uncomputable')}
          </span>
        )}
      </span>
      {showRef && (
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 border-t border-dashed"
                style={{ borderColor: reference.color }}/>
          <span className="text-text-faint">
            {t('slice2d.legend_reference_area', { name: reference.name })}
          </span>
          {refFmt !== null ? (
            <span className="text-text-strong font-mono">
              {refFmt}<span className="text-text-faint"> Lab²</span>
            </span>
          ) : (
            <span className="text-text-faint italic">
              {t('slice2d.area_uncomputable')}
            </span>
          )}
        </span>
      )}
      {ratio !== null && (
        <span>
          <span className="text-text-faint">{t('slice2d.legend_ratio')}</span>
          <span className="text-text-strong font-mono ml-1">{ratio}%</span>
        </span>
      )}
    </div>
  );
}
