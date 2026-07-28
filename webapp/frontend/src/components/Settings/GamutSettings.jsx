import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as api from '../../api/client.js';

const REFERENCES = ['sRGB', 'AdobeRGB', 'ProPhoto', 'Rec.2020', 'Rec.709', 'none'];
const RESOLUTIONS = ['9', '17'];

/**
 * Settings block — 3D Gamut view (simplified). Default reference selector
 * + advanced section (CLUT scatter resolution). The gamut extraction method
 * is hardcoded to ``device_surface_grid`` internally (cf. CHANGELOG).
 * Persisted via /api/settings.
 */
export default function GamutSettings() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => setSettings({}));
  }, []);

  if (!settings) {
    return <p className="text-tiny text-text-faint italic">{t('inspector.loading')}</p>;
  }

  const refValue = settings.inspection?.gamut_reference || 'sRGB';
  const resValue = settings.gamut?.lut_scatter_resolution || '9';

  const update = async (patch) => {
    setBusy(true);
    try {
      const merged = await api.updateSettings(patch);
      setSettings(merged);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <label
          htmlFor="settings-gamut-ref"
          className="block text-xs2 font-medium text-text-strong">
          {t('settings.gamut.reference_label')}
        </label>
        <select
          id="settings-gamut-ref"
          value={refValue}
          disabled={busy}
          onChange={(e) => update({
            inspection: { gamut_reference: e.target.value },
          })}
          className="w-full max-w-xs px-3 py-2 bg-sunken/40 border border-border-soft
                     rounded-md text-sm text-text-strong focus:outline-none
                     focus:border-accent focus:bg-surface transition-colors">
          {REFERENCES.map((r) => (
            <option key={r} value={r}>
              {r === 'none' ? t('settings.gamut.reference_none') : r}
            </option>
          ))}
        </select>
        <p className="text-tiny text-text-faint">
          {t('settings.gamut.reference_hint')}
        </p>
      </div>

      <div>
        <button
          type="button"
          onClick={() => setAdvancedOpen(!advancedOpen)}
          className="text-xs2 text-accent hover:text-accent/80 transition-colors">
          {advancedOpen ? '▾ ' : '▸ '}
          {t('settings.gamut.advanced_toggle')}
        </button>
        {advancedOpen && (
          <div className="mt-4 space-y-5 pl-3 border-l-2 border-border-soft">
            <div className="space-y-1.5">
              <label
                htmlFor="settings-gamut-lutres"
                className="block text-xs2 font-medium text-text-strong">
                {t('settings.gamut.lut_scatter_label')}
              </label>
              <select
                id="settings-gamut-lutres"
                value={resValue}
                disabled={busy}
                onChange={(e) => update({
                  gamut: { lut_scatter_resolution: e.target.value },
                })}
                className="w-full max-w-xs px-3 py-2 bg-sunken/40 border border-border-soft
                           rounded-md text-sm text-text-strong focus:outline-none
                           focus:border-accent focus:bg-surface transition-colors">
                {RESOLUTIONS.map((r) => (
                  <option key={r} value={r}>
                    {t(`settings.gamut.lut_scatter_value_${r}`)}
                  </option>
                ))}
              </select>
              <p className="text-tiny text-text-faint">
                {t('settings.gamut.lut_scatter_hint')}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
