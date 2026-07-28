/** @type {import('tailwindcss').Config} */
// Palette freeglaZ — design system glaz. Toutes les couleurs sémantiques
// passent par CSS vars (3 thèmes CLAIRS définis dans index.css, via data-theme
// sur <html>) — on n'écrit jamais d'oklch/hex en dur dans les composants.
// Le thème sombre 'deep' a été retiré ; plus aucune classe `.dark` n'est posée
// ni de variante `dark:` utilisée. darkMode:'class' conservé (inerte : sans
// `.dark`, rien ne s'active — évite l'auto-dark par media-query). Réversible.
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // ── Tokens DS canoniques (glaz) ──
        'accent':        'var(--accent)',
        'accent-press':  'var(--accent-press)',
        'accent-soft':   'var(--accent-soft)',
        'on-accent':     'var(--on-accent)',
        'line':          'var(--line)',
        'line-strong':   'var(--line-strong)',
        'text':          'var(--text)',
        'surface-2':     'var(--surface-2)',
        'ok':            'var(--ok)',
        'warn':          'var(--warn)',
        // Semantic
        'danger':        'var(--danger)',
        // Surfaces & text
        'bg':            'var(--bg)',
        'surface':       'var(--surface)',
        'sunken':        'var(--sunken)',
        'text-muted':    'var(--text-muted)',
        'text-faint':    'var(--text-faint)',
        // Viewer
        'viewer-bg':     'var(--viewer-bg)',
        'paper':         '#FAF9F5',
        // Origine HP Ingenium (donnée sémantique, pas du chrome)
        'hp-origin':     'var(--hp-origin)',
        // ── Alias legacy (migration douce ; suivent le thème via les vars) ──
        'icc-ok':        'var(--ok)',
        'icc-warn':      'var(--warn)',
        'icc-warn-deep': 'var(--warn)',
        'success':       'var(--ok)',
        'sunken-deep':   'var(--sunken)',
        'border-soft':   'var(--line)',
        'border-strong': 'var(--line-strong)',
        'text-strong':   'var(--text)',
      },
      fontFamily: {
        serif: ['"Bricolage Grotesque"', 'system-ui', 'sans-serif'],
        sans: ['"Hanken Grotesk"', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['"Spline Sans Mono"', 'ui-monospace', '"SF Mono"', 'Menlo', 'monospace'],
      },
      fontSize: {
        'micro': ['10.5px', { lineHeight: '1.3', letterSpacing: '0.02em' }],
        'tiny':  ['11px',   { lineHeight: '1.4' }],
        'xs2':   ['11.5px', { lineHeight: '1.45' }],
        'sm2':   ['12.5px', { lineHeight: '1.5' }],
      },
      boxShadow: {
        'paper-light': '0 28px 64px rgba(0,0,0,0.18), 0 6px 18px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.25)',
        'paper-dark':  '0 28px 64px rgba(0,0,0,0.65), 0 6px 18px rgba(0,0,0,0.35), 0 1px 2px rgba(0,0,0,0.4)',
        'segtab':      '0 1px 2px rgba(0,0,0,0.06), 0 0 0 0.5px rgba(0,0,0,0.04)',
      },
    },
  },
  plugins: [],
};
