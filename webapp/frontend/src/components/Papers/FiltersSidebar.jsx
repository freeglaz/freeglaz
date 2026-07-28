import { Search, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { FINISH_VALUES, CLC_VALUES } from '../../hooks/usePaperFilters.js';

/**
 * Left sidebar 260px: search + filters + sort + reset (spec §3).
 *
 * Filters + sort apply to Favorites + Customs (not HP factory).
 * Application strategy: hide non-matching (empty sections
 * disappear).
 *
 * @param {object} p
 * @param {object} p.state — usePaperFilters reducer state
 * @param {Function} p.dispatch — reducer dispatch
 * @param {boolean} p.hasActiveFilters — drives the reset disabled state
 * @param {Array} p.papers — full list (for counters)
 */
export default function FiltersSidebar({
  state, dispatch, hasActiveFilters, papers = [],
}) {
  const { t } = useTranslation();
  // Labels translated dynamically (flat keys in the JSON)
  const finishLabels = {
    gloss:  t('papers.finish.gloss'),
    matte:  t('papers.finish.matte'),
    canvas: t('papers.finish.canvas'),
    film:   t('papers.finish.film'),
    other:  t('papers.finish.other'),
  };
  const clcLabels = {
    valid:   t('papers.clc_short.valid'),
    stale:   t('papers.clc_short.stale'),
    pending: t('papers.clc_short.pending'),
    never:   t('papers.clc_short.never'),
  };
  // Counters by finish / clc (custom + favorite papers only)
  const filterable = papers.filter((p) => p.custom || p.favorite);
  const finishCounts = _countBy(filterable, (p) => p.finish);
  const clcCounts = _countBy(filterable, (p) => p.clc?.status);

  return (
    <aside
      aria-label={t('papers.filter_section_finish')}
      className="w-[260px] flex-shrink-0 border-r border-border-soft bg-surface flex flex-col">
      <div className="flex-1 overflow-y-auto no-scrollbar px-4 py-4 space-y-5">

        {/* Search */}
        <div>
          <div className="relative">
            <Search
              size={12} strokeWidth={2}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-faint pointer-events-none"
              aria-hidden="true"/>
            <input
              type="text"
              value={state.search}
              onChange={(e) => dispatch({ type: 'SET_SEARCH', value: e.target.value })}
              placeholder={t('papers.search_placeholder')}
              aria-label={t('papers.search_placeholder')}
              className="w-full pl-7 pr-7 py-1.5 rounded-md bg-sunken border border-transparent focus:border-accent focus:bg-surface text-xs2 text-text-strong placeholder:text-text-faint outline-none transition-colors"/>
            {state.search && (
              <button
                type="button"
                onClick={() => dispatch({ type: 'REMOVE_FILTER', kind: 'search' })}
                aria-label={t('common.close')}
                className="absolute right-1 top-1/2 -translate-y-1/2 p-1 rounded text-text-faint hover:text-text-strong hover:bg-surface transition-colors">
                <X size={11} aria-hidden="true"/>
              </button>
            )}
          </div>
        </div>

        {/* Favorites only */}
        <div>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={state.favoritesOnly}
              onChange={() => dispatch({ type: 'TOGGLE_FAVORITES_ONLY' })}
              className="accent-accent"/>
            <span className="text-xs2 text-text-strong">
              {t('papers.filter_favorites_only')}
            </span>
          </label>
        </div>

        {/* Finish filter */}
        <FilterGroup
          title={t('papers.filter_section_finish')}
          values={FINISH_VALUES}
          labels={finishLabels}
          selected={state.finishes}
          counts={finishCounts}
          onToggle={(v) => dispatch({ type: 'TOGGLE_FINISH', value: v })}/>

        {/* CLC state filter */}
        <FilterGroup
          title={t('papers.filter_section_clc')}
          values={CLC_VALUES}
          labels={clcLabels}
          selected={state.clcStates}
          counts={clcCounts}
          onToggle={(v) => dispatch({ type: 'TOGGLE_CLC', value: v })}
          renderDot={_clcDotColor}/>

        {/* Sort */}
        <div>
          <label className="block text-[11px] font-semibold tracking-[0.08em] uppercase text-text-muted mb-1.5">
            {t('papers.filter_sort')}
          </label>
          <div className="space-y-1.5">
            <SortRadio value="name"      label={t('papers.filter_sort_alpha')}     current={state.sort} onChange={(v) => dispatch({ type: 'SET_SORT', value: v })}/>
            <SortRadio value="last_used" label={t('papers.filter_sort_recent')}    current={state.sort} onChange={(v) => dispatch({ type: 'SET_SORT', value: v })}/>
            <SortRadio value="last_clc"  label={t('papers.filter_sort_favorites')} current={state.sort} onChange={(v) => dispatch({ type: 'SET_SORT', value: v })}/>
          </div>
        </div>
      </div>

      {/* Reset at the bottom */}
      <div className="px-4 py-3 border-t border-border-soft">
        <button
          type="button"
          onClick={() => dispatch({ type: 'RESET' })}
          disabled={!hasActiveFilters}
          className="w-full px-3 py-1.5 rounded-md text-xs2 font-medium border border-border-strong text-text-strong hover:bg-sunken disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
          {t('papers.filter_reset')}
        </button>
      </div>
    </aside>
  );
}


// ─── Sub-components ───────────────────────────────────────────────────


function FilterGroup({ title, values, labels, selected, counts, onToggle, renderDot }) {
  return (
    <div>
      <label className="block text-[11px] font-semibold tracking-[0.08em] uppercase text-text-muted mb-1.5">
        {title}
      </label>
      <div className="space-y-1">
        {values.map((v) => (
          <label key={v} className="flex items-center gap-2 cursor-pointer group">
            <input
              type="checkbox"
              checked={selected.has(v)}
              onChange={() => onToggle(v)}
              className="accent-accent"/>
            {renderDot && (
              <span className={`w-1.5 h-1.5 rounded-full ${renderDot(v)}`}/>
            )}
            <span className="text-xs2 text-text-strong flex-1">
              {labels[v]}
            </span>
            <span className="text-tiny text-text-faint tabular-nums">
              {counts[v] || 0}
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}


function SortRadio({ value, label, current, onChange }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <input
        type="radio"
        name="sort"
        checked={current === value}
        onChange={() => onChange(value)}
        className="accent-accent"/>
      <span className="text-xs2 text-text-strong">{label}</span>
    </label>
  );
}


function _clcDotColor(v) {
  return {
    valid: 'bg-icc-ok',
    stale: 'bg-icc-warn',
    never: 'bg-sunken-deep',
  }[v] || 'bg-sunken-deep';
}


function _countBy(arr, keyFn) {
  const out = {};
  for (const item of arr) {
    const k = keyFn(item);
    if (k) out[k] = (out[k] || 0) + 1;
  }
  return out;
}
