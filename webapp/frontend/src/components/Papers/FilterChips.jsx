import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * Active results banner (spec §3) — above the list.
 * If no active filters → null. Otherwise: text + chips.
 */
export default function FilterChips({ chips, matchCount, onRemove, onClearAll }) {
  const { t } = useTranslation();
  if (!chips || chips.length === 0) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2 flex-wrap px-[22px] py-2 border-b border-border-soft bg-sunken/30">
      <span className="text-xs2 text-text-muted">
        {t('papers.filter_matches', { count: matchCount })}
      </span>
      <div className="flex items-center gap-1.5 flex-wrap flex-1">
        {chips.map((c) => (
          <button
            key={`${c.kind}-${c.value}`}
            type="button"
            onClick={() => onRemove(c)}
            aria-label={`${c.label}`}
            className="flex items-center gap-1 px-2 py-0.5 rounded-full text-tiny bg-surface border border-border-soft text-text-strong hover:border-accent/40 transition-colors">
            <span>{c.label}</span>
            <X size={9} strokeWidth={2.5} aria-hidden="true" className="text-text-muted"/>
          </button>
        ))}
      </div>
      <button
        type="button"
        onClick={onClearAll}
        className="text-tiny text-accent hover:text-accent-press font-medium">
        {t('papers.filter_clear_all')}
      </button>
    </div>
  );
}
