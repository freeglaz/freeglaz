import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as api from '../../api/client.js';
import { useGamutMesh, useReferenceMesh } from '../../hooks/useGamut.js';
import { sliceMeshAtL } from '../../lib/gamutSlice.js';
import GamutSlice2D from './GamutSlice2D.jsx';
import EngineExplainer from './EngineExplainer.jsx';

// Categorical palette per intent. Mode-aware coloring:
// in overlay, the actual hue is useless (identical from one intent
// to another at equal hue), so we switch to categorical.
const INTENT_COLORS = {
  perceptual: '#7C3AED',  // violet — compression metaphor
  relative:   '#0891B2',  // cyan — colorimetric fidelity
  saturation: '#EA580C',  // orange — vivid saturation
};
const REFERENCE_COLOR = '#6B7280';   // neutral gray (Mapping mode)

const REFERENCES = ['sRGB', 'AdobeRGB', 'Rec.2020', 'ProPhoto', 'Rec.709'];
const L_SNAPS = [10, 30, 50, 70, 90];


/**
 * "Mapping" view — overlay of intents on the same
 * a*b* slice at constant L.
 *
 * Perceptual + relative shown by default. Saturation toggle off
 * by default. Reference off by default (can be enabled occasionally).
 *
 * At a given L, perceptual visibly tighter than relative
 * (compression vs clipping). That is the editorial point of the view.
 */
export default function MappingView({ source }) {
  const { t, i18n } = useTranslation();
  const [L0, setL0] = useState(50);
  // One toggle per intent. Defaults: perceptual ON,
  // relative ON, saturation OFF. Saving: a hidden intent is
  // not fetched (source=null on the hook → no network call).
  const [showPerceptual, setShowPerceptual] = useState(true);
  const [showRelative, setShowRelative] = useState(true);
  const [showSaturation, setShowSaturation] = useState(false);
  const [showReference, setShowReference] = useState(false);
  const [referenceName, setReferenceName] = useState('sRGB');

  // Initial fetch of the default reference from Settings (used
  // when the user enables the overlay).
  useEffect(() => {
    api.getSettings()
      .then((s) => setReferenceName(s?.inspection?.gamut_reference || 'sRGB'))
      .catch(() => {});
  }, []);

  const { data: perceptualMesh, loading: lPer, error: ePer } =
    useGamutMesh(showPerceptual ? source : null, 'perceptual');
  const { data: relativeMesh, loading: lRel, error: eRel } =
    useGamutMesh(showRelative ? source : null, 'relative');
  const { data: saturationMesh, loading: lSat, error: eSat } =
    useGamutMesh(showSaturation ? source : null, 'saturation');
  const { data: referenceMesh } =
    useReferenceMesh(showReference ? referenceName : null);

  const loading = (showPerceptual && lPer)
                  || (showRelative && lRel)
                  || (showSaturation && lSat);
  const error = (showPerceptual && ePer)
                || (showRelative && eRel)
                || (showSaturation && eSat);
  const noneSelected = !showPerceptual && !showRelative && !showSaturation;

  // Slices memoized by (mesh, L0). In Mapping mode we do NOT interpolate
  // the vertex sRGB colors (we want the categorical hue of the
  // intent, not the actual hue which would be indistinguishable from one
  // intent to another at equal hue).
  const perceptualSlice = useMemo(() => {
    if (!perceptualMesh?.vertices || !showPerceptual) return null;
    return sliceMeshAtL(perceptualMesh.vertices, perceptualMesh.indices, L0);
  }, [perceptualMesh, L0, showPerceptual]);

  const relativeSlice = useMemo(() => {
    if (!relativeMesh?.vertices || !showRelative) return null;
    return sliceMeshAtL(relativeMesh.vertices, relativeMesh.indices, L0);
  }, [relativeMesh, L0, showRelative]);

  const saturationSlice = useMemo(() => {
    if (!saturationMesh?.vertices || !showSaturation) return null;
    return sliceMeshAtL(saturationMesh.vertices, saturationMesh.indices, L0);
  }, [saturationMesh, L0, showSaturation]);

  const referenceSlice = useMemo(() => {
    if (!referenceMesh?.vertices || !showReference) return null;
    return sliceMeshAtL(referenceMesh.vertices, referenceMesh.indices, L0);
  }, [referenceMesh, L0, showReference]);

  // Composition for GamutSlice2D — deterministic z-order:
  // from least priority to most priority (last = on top).
  // Reference (background) → saturation → relative → PERCEPTUAL (always on top).
  // Without this order, saturation covered perceptual, and the eye could
  // no longer distinguish the perceptual compression.
  const sliceMeshes = useMemo(() => {
    const list = [];
    if (referenceSlice && referenceSlice.segments.length > 0) {
      list.push({
        name: 'reference',
        segments: referenceSlice.segments,
        color: REFERENCE_COLOR,
        dashed: true,
        opacity: 0.6,
        strokeWidth: 1.1,
      });
    }
    if (saturationSlice && saturationSlice.segments.length > 0) {
      list.push({
        name: 'saturation',
        segments: saturationSlice.segments,
        color: INTENT_COLORS.saturation,
        // Slightly dashed to reduce line-on-line occlusion
        // when saturation closely follows relative.
        dashed: true,
        opacity: 0.8,
        strokeWidth: 1.6,
      });
    }
    if (relativeSlice && relativeSlice.segments.length > 0) {
      list.push({
        name: 'relative',
        segments: relativeSlice.segments,
        color: INTENT_COLORS.relative,
        opacity: 0.85,
        strokeWidth: 1.8,
      });
    }
    if (perceptualSlice && perceptualSlice.segments.length > 0) {
      list.push({
        name: 'perceptual',
        segments: perceptualSlice.segments,
        color: INTENT_COLORS.perceptual,
        opacity: 0.95,
        strokeWidth: 1.9,
      });
    }
    return list;
  }, [perceptualSlice, relativeSlice, saturationSlice, referenceSlice]);

  const fmtArea = (v) => {
    if (v == null || !isFinite(v)) return null;
    return v.toLocaleString(i18n.language === 'fr' ? 'fr-FR' : 'en-US',
      { maximumFractionDigits: 0 });
  };

  return (
    <div className="px-6 py-5 flex flex-col gap-4 h-full">
      <Toolbar
        showPerceptual={showPerceptual} setShowPerceptual={setShowPerceptual}
        showRelative={showRelative} setShowRelative={setShowRelative}
        showSaturation={showSaturation} setShowSaturation={setShowSaturation}
        showReference={showReference} setShowReference={setShowReference}
        referenceName={referenceName} setReferenceName={setReferenceName}/>

      <EngineExplainer i18nPrefix="mapping"/>

      <LSlider L0={L0} setL0={setL0}/>

      <div className="flex-1 bg-bg border border-border-soft rounded-xl
                      overflow-hidden p-4 flex items-start justify-center
                      relative min-h-[420px]">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-bg/85 z-10">
            <span className="inline-block w-3.5 h-3.5 border-2 border-accent
                             border-t-transparent rounded-full animate-spin
                             mr-2" aria-hidden="true"/>
            <span className="text-text-muted text-xs2">{t('mapping.loading')}</span>
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center px-6">
            <div className="text-danger text-xs2 text-center max-w-md">
              <div className="font-semibold mb-1">{t('mapping.error_title')}</div>
              <div className="text-text-muted font-mono">{error}</div>
            </div>
          </div>
        )}
        {!loading && !error && noneSelected && (
          <div className="text-text-faint text-xs2 italic mt-20 text-center max-w-sm">
            {t('mapping.none_selected')}
          </div>
        )}
        {!loading && !error && !noneSelected && sliceMeshes.length > 0 && (
          <GamutSlice2D meshes={sliceMeshes} size={480}/>
        )}
        {!loading && !error && !noneSelected && sliceMeshes.length === 0 && (
          <div className="text-text-faint text-xs2 italic mt-20">
            {t('mapping.empty_slice', { L: L0 })}
          </div>
        )}
      </div>

      <InteractionsHint/>

      <Legend
        L0={L0}
        perceptual={showPerceptual ? perceptualSlice : null}
        relative={showRelative ? relativeSlice : null}
        saturation={showSaturation ? saturationSlice : null}
        reference={showReference ? { slice: referenceSlice, name: referenceName } : null}
        fmt={fmtArea}/>
    </div>
  );
}


function Toolbar({
  showPerceptual, setShowPerceptual,
  showRelative, setShowRelative,
  showSaturation, setShowSaturation,
  showReference, setShowReference,
  referenceName, setReferenceName,
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-4 flex-wrap">
      <div className="flex items-center gap-3 px-3 py-1.5 bg-sunken/40 rounded-md">
        <span className="text-tiny text-text-muted uppercase tracking-wider">
          {t('mapping.toolbar_intents')}
        </span>
        <IntentToggle
          color={INTENT_COLORS.perceptual}
          label={t('mapping.intent_perceptual')}
          on={showPerceptual} onChange={setShowPerceptual}/>
        <IntentToggle
          color={INTENT_COLORS.relative}
          label={t('mapping.intent_relative')}
          on={showRelative} onChange={setShowRelative}/>
        <IntentToggle
          color={INTENT_COLORS.saturation}
          label={t('mapping.intent_saturation')}
          on={showSaturation} onChange={setShowSaturation}/>
      </div>

      <label className="inline-flex items-center gap-2 text-xs2 cursor-pointer">
        <input
          type="checkbox"
          checked={showReference}
          onChange={(e) => setShowReference(e.target.checked)}
          className="accent-accent"/>
        <span className="text-text-muted">{t('mapping.toolbar_reference')}</span>
      </label>

      {showReference && (
        <select
          value={referenceName}
          onChange={(e) => setReferenceName(e.target.value)}
          className="px-2 py-1 text-xs2 bg-sunken/60 border border-border-soft
                     rounded text-text-strong focus:outline-none focus:border-accent">
          {REFERENCES.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
      )}
    </div>
  );
}


function IntentToggle({ color, label, on, onChange }) {
  return (
    <label className="inline-flex items-center gap-1.5 text-xs2 cursor-pointer">
      <input
        type="checkbox"
        checked={on}
        onChange={(e) => onChange(e.target.checked)}
        className="accent-accent"/>
      <span className={on ? 'text-text-strong' : 'text-text-faint line-through'}>
        <span className="inline-block w-3 h-3 rounded-sm mr-1.5 align-middle"
              style={{ backgroundColor: on ? color : 'transparent',
                       border: on ? 'none' : `1px dashed ${color}`,
                       opacity: on ? 0.85 : 0.5 }}/>
        {label}
      </span>
    </label>
  );
}




function LSlider({ L0, setL0 }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-3">
      <label className="text-tiny text-text-muted uppercase tracking-wider shrink-0">
        {t('mapping.slider_label')}
      </label>
      <div className="flex-1 max-w-xl relative">
        <input
          type="range"
          min={0} max={100} step={1}
          value={L0}
          onChange={(e) => setL0(Number(e.target.value))}
          className="w-full accent-accent"
          aria-label={t('mapping.slider_label')}/>
        <div className="flex justify-between text-tiny text-text-faint font-mono mt-0.5 px-1">
          {L_SNAPS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setL0(s)}
              className={`px-1 hover:text-text-strong transition-colors ${
                L0 === s ? 'text-accent font-semibold' : ''
              }`}>
              {s}
            </button>
          ))}
        </div>
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
      {t('mapping.interactions_hint')}
    </p>
  );
}


function Legend({ L0, perceptual, relative, saturation, reference, fmt }) {
  const { t } = useTranslation();
  const items = [];
  items.push({ label: t('mapping.intent_perceptual'), color: '#7C3AED',
               area: perceptual?.area });
  items.push({ label: t('mapping.intent_relative'),   color: '#0891B2',
               area: relative?.area });
  if (saturation) {
    items.push({ label: t('mapping.intent_saturation'), color: '#EA580C',
                 area: saturation.area });
  }

  return (
    <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-tiny text-text-muted px-1">
      <span>
        <span className="text-text-faint">L*&nbsp;=&nbsp;</span>
        <span className="text-text-strong font-mono">{L0}</span>
      </span>
      {items.map((it) => {
        const f = fmt(it.area);
        return (
          <span key={it.label} className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-px"
                  style={{ backgroundColor: it.color, height: 2 }}/>
            <span className="text-text-faint">{it.label}&nbsp;:</span>
            {f !== null ? (
              <span className="text-text-strong font-mono">
                {f}<span className="text-text-faint"> Lab²</span>
              </span>
            ) : (
              <span className="text-text-faint italic">
                {t('slice2d.area_uncomputable')}
              </span>
            )}
          </span>
        );
      })}
      {reference?.slice && reference.slice.segments.length > 0 && (
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 border-t border-dashed"
                style={{ borderColor: '#6B7280' }}/>
          <span className="text-text-faint">
            {t('mapping.legend_reference', { name: reference.name })}
          </span>
          {fmt(reference.slice.area) !== null ? (
            <span className="text-text-strong font-mono">
              {fmt(reference.slice.area)}<span className="text-text-faint"> Lab²</span>
            </span>
          ) : (
            <span className="text-text-faint italic">
              {t('slice2d.area_uncomputable')}
            </span>
          )}
        </span>
      )}
    </div>
  );
}
