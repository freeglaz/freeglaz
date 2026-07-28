import { useTranslation } from 'react-i18next';
import { useTheme, THEMES } from '../../hooks/useTheme.js';

/**
 * glaz theme selector (4 themes — cf. useTheme/index.css). Switches without
 * a reload: applyTheme sets `data-theme` on <html>, the CSS tokens do the
 * rest. Persisted in localStorage (key shared with useTheme).
 */
export default function ThemeSwitcher() {
  const { t } = useTranslation();
  const { theme, setTheme } = useTheme();

  return (
    <div className="space-y-1.5">
      <label
        htmlFor="settings-theme"
        className="block text-xs2 font-medium text-text-strong">
        {t('settings.theme_label')}
      </label>
      <select
        id="settings-theme"
        value={theme}
        onChange={(e) => setTheme(e.target.value)}
        className="w-full max-w-xs px-3 py-2 bg-sunken/40 border border-border-soft rounded-md text-sm text-text-strong focus:outline-none focus:border-accent focus:bg-surface transition-colors">
        {THEMES.map((id) => (
          <option key={id} value={id}>{t(`settings.themes.${id}`)}</option>
        ))}
      </select>
      <p className="text-tiny text-text-faint">
        {t('settings.theme_hint')}
      </p>
    </div>
  );
}
