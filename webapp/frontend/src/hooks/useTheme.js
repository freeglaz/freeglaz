import { useEffect, useState } from 'react';
import { safeLocalGet, safeLocalSet } from '../lib/safeLocalStorage.js';

/**
 * glaz theme: 'mist' (default) | 'tide' | 'neutral'. All LIGHT (neutral =
 * achromatic grey for photo judgment, NOT a dark theme).
 * A single tokens API — each theme redefines the same CSS variables
 * (cf. index.css). Applied via the `data-theme` attribute on <html>.
 * The 'deep' dark theme was removed (dense UI = source of contrast bugs,
 * cf. native <select> with forced white background). Reversible: the token architecture remains.
 */
export const THEMES = ['mist', 'tide', 'neutral'];
const STORAGE_KEY = 'freeglaz.theme';
const DEFAULT_THEME = 'mist';

export function getStoredTheme() {
  // localStorage may throw / be absent (WebKitGTK webview); read DURING the render
  // (useState initializer) → PROTECTED access mandatory (otherwise 1st-render crash).
  const v = safeLocalGet(STORAGE_KEY);
  // Soft migration: any value outside {mist,tide,neutral} (including the old
  // 'deep'/'dark') falls back to mist (rewritten by applyTheme).
  return THEMES.includes(v) ? v : DEFAULT_THEME;
}

export function applyTheme(name) {
  const theme = THEMES.includes(name) ? name : DEFAULT_THEME;
  document.documentElement.setAttribute('data-theme', theme);
  safeLocalSet(STORAGE_KEY, theme);
}

export function useTheme() {
  const [theme, setThemeState] = useState(getStoredTheme);
  useEffect(() => { applyTheme(theme); }, [theme]);
  const setTheme = (name) => setThemeState(THEMES.includes(name) ? name : DEFAULT_THEME);
  return { theme, setTheme, themes: THEMES };
}
