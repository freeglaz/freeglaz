/**
 * freeglaz i18n setup.
 *
 * Stack: ``i18next`` + ``react-i18next`` + ``i18next-browser-languagedetector``.
 *
 * Cascading language detection:
 * 1. ``localStorage["freeglaz.language"]`` (user preference)
 * 2. ``navigator.language`` (``Accept-Language`` header)
 * 3. Hardcoded fallback ``fr``
 *
 * French remains the project's default language (aligned with
 * the maintainer's usage). English offers full key parity
 * to open the webapp to the international HP LFP community.
 *
 * Adding a new language: create ``locales/<code>.json`` (strict
 * key parity with ``fr.json``), register it in ``resources``
 * below, add an entry in the ``LanguageSwitcher``.
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import fr from './locales/fr.json';
import en from './locales/en.json';

export const LANGUAGE_STORAGE_KEY = 'freeglaz.language';

export const SUPPORTED_LANGUAGES = [
  { code: 'fr', label: 'Français' },
  { code: 'en', label: 'English' },
];

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      fr: { translation: fr },
      en: { translation: en },
    },
    fallbackLng: 'fr',
    supportedLngs: ['fr', 'en'],
    interpolation: {
      // React already escapes all rendered content — no double escaping.
      escapeValue: false,
    },
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: LANGUAGE_STORAGE_KEY,
      caches: ['localStorage'],
    },
    // In dev, log the missing keys instead of crashing. The component
    // displays the raw key as a fallback, which makes the gaps
    // immediately visible on screen.
    returnEmptyString: false,
    debug: false,
  });

export default i18n;
