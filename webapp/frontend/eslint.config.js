import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      // Garde-fou : INTERDIT l'accès localStorage/sessionStorage DIRECT. En webview
      // WebKitGTK il peut JETER pendant le render → arbre React démonté → blanc/crash
      // (a mordu 2× : thème, page Logs). Passer par lib/safeLocalStorage.js.
      'no-restricted-properties': [
        'error',
        { object: 'localStorage', message: 'localStorage direct interdit (peut jeter en webview) — utiliser safeLocalGet/safeLocalSet/safeLocalRemove de lib/safeLocalStorage.js.' },
        { object: 'sessionStorage', message: 'sessionStorage direct interdit — passer par lib/safeLocalStorage.js (à étendre si besoin).' },
      ],
    },
  },
  {
    // Seul endroit autorisé à toucher localStorage directement : le helper lui-même.
    files: ['src/lib/safeLocalStorage.js'],
    rules: { 'no-restricted-properties': 'off' },
  },
])
