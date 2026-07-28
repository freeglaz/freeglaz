import { useTranslation } from 'react-i18next';
import { SUPPORTED_LANGUAGES, LANGUAGE_STORAGE_KEY } from '../../i18n/index.js';
import { safeLocalSet } from '../../lib/safeLocalStorage.js';

/**
 * Language selector. localStorage persistence via the configured i18next
 * detector. Switches without a page reload — react-i18next re-renders all
 * components using ``useTranslation``.
 */
export default function LanguageSwitcher() {
  const { t, i18n } = useTranslation();

  const handleChange = (e) => {
    const code = e.target.value;
    i18n.changeLanguage(code);
    // Belt-and-braces: the detector caches=localStorage should already
    // persist, but we write it explicitly just in case.
    safeLocalSet(LANGUAGE_STORAGE_KEY, code);
    // Update the <html> lang attribute for screen readers
    document.documentElement.lang = code;
  };

  return (
    <div className="space-y-1.5">
      <label
        htmlFor="settings-language"
        className="block text-xs2 font-medium text-text-strong">
        {t('settings.language_label')}
      </label>
      <select
        id="settings-language"
        value={i18n.language}
        onChange={handleChange}
        className="w-full max-w-xs px-3 py-2 bg-sunken/40 border border-border-soft rounded-md text-sm text-text-strong focus:outline-none focus:border-accent focus:bg-surface transition-colors">
        {SUPPORTED_LANGUAGES.map((lang) => (
          <option key={lang.code} value={lang.code}>{lang.label}</option>
        ))}
      </select>
      <p className="text-tiny text-text-faint">
        {t('settings.language_hint')}
      </p>
    </div>
  );
}
