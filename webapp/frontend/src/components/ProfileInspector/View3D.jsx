import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RotateCcw } from 'lucide-react';
import * as api from '../../api/client.js';
import Segmented from '../ui/Segmented.jsx';
import {
  useGamutMesh, useReferenceMesh, useLutScatter, useGamtMesh,
} from '../../hooks/useGamut.js';
import Gamut3DRenderer from './Gamut3DRenderer.jsx';

const REFERENCES = ['none', 'sRGB', 'AdobeRGB', 'Rec.2020', 'ProPhoto', 'Rec.709'];

// Definition of the 10 selector entries. Ordered groups. The
// ``mode`` field determines which renderer (marching_cubes or scatter).
const ENTRIES = [
  // Effective gamut (3 entries)
  { key: 'eff_perceptual',  group: 'eff', mode: 'marching_cubes', intent: 'perceptual' },
  { key: 'eff_relative',    group: 'eff', mode: 'marching_cubes', intent: 'relative' },
  { key: 'eff_saturation',  group: 'eff', mode: 'marching_cubes', intent: 'saturation' },
  // LUTs (6 entries)
  { key: 'A2B0', group: 'lut', mode: 'scatter', tag: 'A2B0' },
  { key: 'A2B1', group: 'lut', mode: 'scatter', tag: 'A2B1' },
  { key: 'A2B2', group: 'lut', mode: 'scatter', tag: 'A2B2' },
  { key: 'B2A0', group: 'lut', mode: 'scatter', tag: 'B2A0' },
  { key: 'B2A1', group: 'lut', mode: 'scatter', tag: 'B2A1' },
  { key: 'B2A2', group: 'lut', mode: 'scatter', tag: 'B2A2' },
  // Profiler indicator
  { key: 'gamt', group: 'gamt', mode: 'marching_cubes' },
];


/**
 * Centralized 3D view. A single 3D screen with a
 * structured selector (3 optgroups, 10 entries). The render mode and the intent/tag
 * are derived from the selected entry.
 */
export default function View3D({ inspection, source, initialEntry }) {
  const { t } = useTranslation();
  const [selectedKey, setSelectedKey] = useState(initialEntry || 'eff_perceptual');
  const [referenceName, setReferenceName] = useState(null);
  const [renderStyle, setRenderStyle] = useState('both');
  const [showLabAxes, setShowLabAxes] = useState(true);
  const [version, setVersion] = useState(0);

  // Camera persistence between contents.
  // Saved at the end of drag/wheel by Gamut3DRenderer. Kept as long as
  // the profile does not change. Reset when source.path/paperMediaid changes
  // or when the user clicks "Reset the view".
  const [cameraState, setCameraState] = useState(null);
  const profileKey = source?.path || source?.paperMediaid || 'unknown';

  // Reset cameraState when the profile changes
  useEffect(() => {
    setCameraState(null);
  }, [profileKey]);

  const handleCameraChange = useCallback((snapshot) => {
    setCameraState(snapshot);
  }, []);

  const handleResetView = useCallback(() => {
    setCameraState(null);
    setVersion((v) => v + 1);
  }, []);

  // Sync selectedKey if initialEntry changes (e.g. click "Visualize
  // in 3D" in another popover after opening)
  useEffect(() => {
    if (initialEntry) setSelectedKey(initialEntry);
  }, [initialEntry]);

  // Initial fetch of the default reference. No more
  // boundary_method (single device_surface_grid method internally).
  useEffect(() => {
    api.getSettings()
      .then((s) => setReferenceName(s?.inspection?.gamut_reference || 'sRGB'))
      .catch(() => setReferenceName('sRGB'));
  }, []);

  // Available tags → disabling of the corresponding entries
  const availableLutTags = useMemo(() => {
    const tagsDetails = inspection?.tags_details || [];
    return new Set(tagsDetails.map((td) => td.signature));
  }, [inspection]);

  const entry = ENTRIES.find((e) => e.key === selectedKey) || ENTRIES[0];

  // Reference (always device_surface_grid wireframe)
  const { data: referenceMesh } = useReferenceMesh(referenceName);

  // Primary mesh according to the entry type — only one of the three hooks is
  // active at a time (the others take source=null/tag=null/etc.).
  const isEff = entry.group === 'eff';
  const isLut = entry.group === 'lut';
  const isGamt = entry.group === 'gamt';

  const { data: effMesh, loading: effLoading, error: effError } =
    useGamutMesh(isEff ? source : null, entry.intent || 'relative');
  const { data: scatter, loading: scatterLoading, error: scatterError } =
    useLutScatter(isLut ? source : null, isLut ? entry.tag : null);
  const { data: gamtMesh, loading: gamtLoading, error: gamtError } =
    useGamtMesh(isGamt ? source : null);

  const loading = effLoading || scatterLoading || gamtLoading;
  const error = effError || scatterError || gamtError;

  const primaryMesh = isEff ? effMesh : isLut ? scatter : isGamt ? gamtMesh : null;
  const showReference = referenceName !== 'none' && (isEff || isGamt);

  const volumePrimary = (isEff || isGamt) ? primaryMesh?.volume_lab : null;
  const volumeRef = referenceMesh?.volume_lab;
  const ratio = (volumePrimary && volumeRef && volumeRef > 0)
    ? (volumePrimary / volumeRef * 100).toFixed(1)
    : null;

  return (
    <div className="px-6 py-5 flex flex-col gap-3 h-full">
      <Selector
        entries={ENTRIES}
        selectedKey={selectedKey}
        setSelectedKey={setSelectedKey}
        availableLutTags={availableLutTags}/>

      <Description entryKey={entry.key}/>

      <Toolbar
        referenceName={referenceName}
        setReferenceName={setReferenceName}
        renderStyle={renderStyle}
        setRenderStyle={setRenderStyle}
        showLabAxes={showLabAxes}
        setShowLabAxes={setShowLabAxes}
        onResetView={handleResetView}
        showRefDropdown={isEff || isGamt}/>

      <div className="flex-1 bg-bg border border-border-soft rounded-xl overflow-hidden min-h-[420px] relative">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-bg/85 z-10">
            <div className="text-text-muted text-xs2 flex items-center gap-2">
              <span className="inline-block w-3.5 h-3.5 border-2 border-accent
                               border-t-transparent rounded-full animate-spin"
                    aria-hidden="true"/>
              {t('view3d.loading')}
            </div>
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center px-6">
            <div className="text-danger text-xs2 text-center max-w-md">
              <div className="font-semibold mb-1">{t('view3d.error_title')}</div>
              <div className="text-text-muted font-mono">{error}</div>
            </div>
          </div>
        )}
        {!loading && !error && (
          <Gamut3DRenderer
            key={profileKey}
            mode={entry.mode}
            primaryMesh={primaryMesh}
            referenceMesh={referenceMesh}
            showReference={showReference}
            renderStyle={renderStyle}
            showLabAxes={showLabAxes}
            resetCounter={version}
            initialCameraState={cameraState}
            onCameraChange={handleCameraChange}/>
        )}
      </div>

      <InteractionsHint/>

      <Legend
        inspection={inspection}
        referenceName={referenceName}
        showRefVolume={isEff || isGamt}
        volumePrimary={volumePrimary}
        volumeRef={volumeRef}
        ratio={ratio}/>
    </div>
  );
}


function Selector({ entries, selectedKey, setSelectedKey, availableLutTags }) {
  const { t } = useTranslation();
  const isDisabled = (e) => {
    if (e.group === 'lut') return !availableLutTags.has(e.tag);
    if (e.group === 'gamt') return !availableLutTags.has('gamt');
    return false;
  };
  return (
    <div className="flex items-center gap-2">
      <label className="text-tiny text-text-muted uppercase tracking-wider shrink-0">
        {t('view3d.selector_label')}
      </label>
      <select
        value={selectedKey}
        onChange={(e) => setSelectedKey(e.target.value)}
        className="flex-1 max-w-md px-2 py-1 text-xs2 bg-sunken/60 border border-border-soft
                   rounded text-text-strong focus:outline-none focus:border-accent">
        <optgroup label={t('view3d.group_effective')}>
          {entries.filter((e) => e.group === 'eff').map((e) => (
            <option key={e.key} value={e.key}>
              {t(`view3d.entry_${e.key}_label`)}
            </option>
          ))}
        </optgroup>
        <optgroup label={t('view3d.group_luts')}>
          {entries.filter((e) => e.group === 'lut').map((e) => {
            const dis = isDisabled(e);
            return (
              <option key={e.key} value={e.key} disabled={dis}
                      title={dis ? t('view3d.entry_unavailable_tooltip') : undefined}>
                {t(`view3d.entry_${e.key}_label`)}{dis ? ' · —' : ''}
              </option>
            );
          })}
        </optgroup>
        <optgroup label={t('view3d.group_profiler')}>
          {entries.filter((e) => e.group === 'gamt').map((e) => {
            const dis = isDisabled(e);
            return (
              <option key={e.key} value={e.key} disabled={dis}
                      title={dis ? t('view3d.entry_unavailable_tooltip') : undefined}>
                {t(`view3d.entry_${e.key}_label`)}{dis ? ' · —' : ''}
              </option>
            );
          })}
        </optgroup>
      </select>
    </div>
  );
}


function Description({ entryKey }) {
  const { t } = useTranslation();
  return (
    <p className="text-tiny text-text-muted leading-relaxed px-1 max-w-3xl">
      {t(`view3d.entry_${entryKey}_description`)}
    </p>
  );
}


function Toolbar({ referenceName, setReferenceName, renderStyle, setRenderStyle, showLabAxes, setShowLabAxes, onResetView, showRefDropdown }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-3 flex-wrap">
      {showRefDropdown && (
        <div className="flex items-center gap-2">
          <label className="text-tiny text-text-muted uppercase tracking-wider">
            {t('view3d.toolbar_reference')}
          </label>
          <select
            value={referenceName || 'sRGB'}
            onChange={(e) => setReferenceName(e.target.value)}
            className="px-2 py-1 text-xs2 bg-sunken/60 border border-border-soft
                       rounded text-text-strong focus:outline-none focus:border-accent">
            {REFERENCES.map((r) => (
              <option key={r} value={r}>
                {r === 'none' ? t('view3d.reference_none') : r}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="flex items-center gap-2">
        <label className="text-tiny text-text-muted uppercase tracking-wider">
          {t('view3d.toolbar_display')}
        </label>
        <Segmented
          value={renderStyle}
          onChange={setRenderStyle}
          options={['both', 'solid', 'wireframe'].map((s) => ({ value: s, label: t(`view3d.style_${s}`) }))}/>
      </div>

      <label className="inline-flex items-center gap-1.5 px-2 py-1 text-xs2
                        text-text-muted hover:text-text-strong cursor-pointer
                        border border-border-soft rounded">
        <input
          type="checkbox"
          checked={showLabAxes}
          onChange={(e) => setShowLabAxes(e.target.checked)}
          className="accent-accent"/>
        {t('view3d.toolbar_lab_axes')}
      </label>

      <button
        type="button"
        onClick={onResetView}
        className="inline-flex items-center gap-1.5 px-2 py-1 text-xs2
                   text-text-muted hover:text-text-strong
                   border border-border-soft rounded
                   hover:border-border-strong transition-colors">
        <RotateCcw size={11} strokeWidth={2}/>
        {t('view3d.toolbar_reset')}
      </button>
    </div>
  );
}


function InteractionsHint() {
  const { t } = useTranslation();
  return (
    <p className="text-tiny text-text-faint px-1 leading-snug">
      {t('view3d.interactions_hint')}
    </p>
  );
}


function Legend({ inspection, referenceName, showRefVolume, volumePrimary, volumeRef, ratio }) {
  const { t, i18n } = useTranslation();
  const fmt = (v) => v.toLocaleString(i18n.language === 'fr' ? 'fr-FR' : 'en-US', { maximumFractionDigits: 0 });
  return (
    <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-tiny text-text-muted px-1">
      <span>
        <span className="text-text-faint">{t('view3d.legend_profile')}</span>
        <span className="text-text-strong ml-1">{inspection?.description || '—'}</span>
      </span>
      {showRefVolume && volumePrimary > 0 && (
        <span>
          <span className="text-text-faint">{t('view3d.legend_volume')}</span>
          <span className="text-text-strong ml-1 font-mono">
            {fmt(volumePrimary)}<span className="text-text-faint"> Lab³</span>
          </span>
        </span>
      )}
      {showRefVolume && referenceName !== 'none' && volumeRef > 0 && (
        <span>
          <span className="text-text-faint">
            {t('view3d.legend_reference', { name: referenceName })}
          </span>
          <span className="text-text-strong ml-1 font-mono">
            {fmt(volumeRef)}<span className="text-text-faint"> Lab³</span>
          </span>
        </span>
      )}
      {ratio !== null && (
        <span>
          <span className="text-text-faint">{t('view3d.legend_ratio')}</span>
          <span className="text-text-strong ml-1 font-mono">{ratio}%</span>
        </span>
      )}
    </div>
  );
}
