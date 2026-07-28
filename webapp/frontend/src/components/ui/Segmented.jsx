// Segmented control — for Gloss enhancer, Quality (HIGH/NORMAL/FAST), etc.
// The active one is a raised white tab, NOT the accent color. The HP Blue
// accent is reserved for primaries (Print button, toggles).
//
// `options` accepts two forms:
//   - string[]                        → label = value (e.g. ['HIGH','NORMAL'])
//   - {value, label}[]                → displayed label ≠ internal value
//                                       (e.g. Gloss enhancer "On the image" → 'image')
export default function Segmented({ options, value, onChange, disabled = false }) {
  const normalized = options.map((o) =>
    typeof o === 'string' ? { value: o, label: o } : o,
  );
  return (
    <div className={`flex bg-sunken rounded-md p-[3px] gap-0.5 ${disabled ? 'opacity-40 pointer-events-none' : ''}`}>
      {normalized.map(({ value: v, label }) => {
        const sel = v === value;
        return (
          <button
            key={v}
            type="button"
            onClick={() => onChange?.(v)}
            className={`flex-1 text-center py-1.5 rounded text-xs font-medium tracking-wide transition-colors
              ${sel
                ? 'bg-surface text-text-strong font-semibold shadow-segtab'
                : 'text-text-muted hover:text-text-strong'}`}>
            {label}
          </button>
        );
      })}
    </div>
  );
}
