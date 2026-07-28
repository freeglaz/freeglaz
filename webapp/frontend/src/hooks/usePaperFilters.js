import { useCallback, useMemo, useReducer, useRef, useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';

/**
 * Filter + sort management hook for the papers list (P1.C — spec §3).
 *
 * State (useReducer to streamline the actions):
 *   {
 *     search: string,            // live input (before debounce)
 *     searchDebounced: string,   // what we use to filter (150ms)
 *     favoritesOnly: bool,
 *     finishes: Set<string>,     // 'gloss'|'matte'|'canvas'|'film'|'other'
 *     clcStates: Set<string>,    // 'valid'|'stale'|'never'
 *     sort: 'name'|'last_used'|'last_clc',
 *   }
 *
 * Strategy: if a set is EMPTY, we don't apply the filter (all
 * values accepted). If non-empty, we keep only the papers
 * whose corresponding value is in the set.
 *
 * "Last used" sort: if all last_used are null (P1 fallback,
 * cf. user brief), falls back to Name A→Z sort (spec § « Pitfalls »).
 */

const initialState = {
  search: '',
  searchDebounced: '',
  favoritesOnly: false,
  finishes: new Set(),
  clcStates: new Set(),
  sort: 'name',
};

function reducer(state, action) {
  switch (action.type) {
    case 'SET_SEARCH':
      return { ...state, search: action.value };
    case 'SET_SEARCH_DEBOUNCED':
      return { ...state, searchDebounced: action.value };
    case 'TOGGLE_FAVORITES_ONLY':
      return { ...state, favoritesOnly: !state.favoritesOnly };
    case 'TOGGLE_FINISH': {
      const next = new Set(state.finishes);
      next.has(action.value) ? next.delete(action.value) : next.add(action.value);
      return { ...state, finishes: next };
    }
    case 'TOGGLE_CLC': {
      const next = new Set(state.clcStates);
      next.has(action.value) ? next.delete(action.value) : next.add(action.value);
      return { ...state, clcStates: next };
    }
    case 'SET_SORT':
      return { ...state, sort: action.value };
    case 'REMOVE_FILTER':
      // Removes a specific filter via the chips (kind: 'finish'|'clc'|'favorite'|'search')
      if (action.kind === 'finish') {
        const next = new Set(state.finishes); next.delete(action.value);
        return { ...state, finishes: next };
      }
      if (action.kind === 'clc') {
        const next = new Set(state.clcStates); next.delete(action.value);
        return { ...state, clcStates: next };
      }
      if (action.kind === 'favorite') return { ...state, favoritesOnly: false };
      if (action.kind === 'search') return { ...state, search: '', searchDebounced: '' };
      return state;
    case 'RESET':
      return { ...initialState, finishes: new Set(), clcStates: new Set() };
    default:
      return state;
  }
}


/** Normalizes a string for case- + accent-insensitive search (spec §3). */
function _normalize(s) {
  if (!s) return '';
  return s.toString()
    .normalize('NFD').replace(/\p{Diacritic}/gu, '')
    .toLowerCase();
}


export function usePaperFilters() {
  const { t } = useTranslation();
  const [state, dispatch] = useReducer(reducer, initialState);

  // Debounce search 150ms (spec §10)
  useEffect(() => {
    if (state.search === state.searchDebounced) return;
    const t = setTimeout(() => {
      dispatch({ type: 'SET_SEARCH_DEBOUNCED', value: state.search });
    }, 150);
    return () => clearTimeout(t);
  }, [state.search, state.searchDebounced]);

  // ── Selectors ──────────────────────────────────────────────────

  /**
   * Filters + sorts a list of papers according to the current state.
   * Applies to Favorites + Customs (spec §3 decision revised for
   * consistency). The caller decides which papers to pass here.
   */
  const filterAndSort = useCallback((papers) => {
    const { searchDebounced, favoritesOnly, finishes, clcStates, sort } = state;
    const needle = _normalize(searchDebounced);

    let out = (papers || []).filter((p) => {
      if (favoritesOnly && !p.favorite) return false;
      if (needle) {
        const hay = _normalize(p.name);
        if (!hay.includes(needle)) return false;
      }
      if (finishes.size > 0 && !finishes.has(p.finish)) return false;
      if (clcStates.size > 0 && !clcStates.has(p.clc?.status)) return false;
      return true;
    });

    // Sort
    if (sort === 'last_used') {
      // If all null → Name fallback (cf. spec « Pitfalls »)
      const hasAny = out.some((p) => p.last_used);
      if (hasAny) {
        out = [...out].sort((a, b) => {
          const av = a.last_used || '';
          const bv = b.last_used || '';
          if (av === bv) return _cmpName(a, b);
          return bv.localeCompare(av);  // most recent first
        });
      } else {
        out = [...out].sort(_cmpName);
      }
    } else if (sort === 'last_clc') {
      out = [...out].sort((a, b) => {
        const av = a.clc?.date || '';
        const bv = b.clc?.date || '';
        if (av === bv) return _cmpName(a, b);
        return bv.localeCompare(av);
      });
    } else {
      // Default: name A→Z
      out = [...out].sort(_cmpName);
    }
    return out;
  }, [state]);

  /** List of active chips to display above the list (spec §3). */
  const chips = useMemo(() => {
    const list = [];
    if (state.searchDebounced) {
      list.push({
        kind: 'search', value: state.searchDebounced,
        label: `« ${state.searchDebounced} »`,
      });
    }
    if (state.favoritesOnly) {
      list.push({ kind: 'favorite', value: true, label: t('papers.filter_favorites_only') });
    }
    for (const f of state.finishes) {
      list.push({ kind: 'finish', value: f, label: _finishLabel(f, t) });
    }
    for (const c of state.clcStates) {
      list.push({ kind: 'clc', value: c, label: _clcLabel(c, t) });
    }
    return list;
  }, [state, t]);

  const hasActiveFilters = chips.length > 0;

  return { state, dispatch, filterAndSort, chips, hasActiveFilters };
}


function _cmpName(a, b) {
  return (a.name || '').localeCompare(b.name || '', 'fr', { sensitivity: 'base' });
}


// Chip labels resolved via i18n at render (translator passed in so the
// chips re-translate on language change through the `chips` useMemo).
function _finishLabel(f, t) {
  const tr = t || ((k) => i18n.t(k));
  return tr(`papers.finish.${f}`);
}


function _clcLabel(s, t) {
  const tr = t || ((k) => i18n.t(k));
  return tr(`papers.clc_short.${s}`);
}


// Export for external use (sidebar counters, etc.)
export const FINISH_VALUES = ['gloss', 'matte', 'canvas', 'film', 'other'];
// Getter-backed label maps: resolved through i18n.t at read time so they
// follow the active language rather than being frozen at import.
export const FINISH_LABELS = Object.fromEntries(
  FINISH_VALUES.map((v) => [v, i18n.t(`papers.finish.${v}`)]),
);
// P3.A3: 4 CLC statuses (was 3, added "pending" for freshly
// created custom papers awaiting their 1st CLC).
export const CLC_VALUES = ['valid', 'stale', 'pending', 'never'];
export const CLC_LABELS = Object.fromEntries(
  CLC_VALUES.map((v) => [v, i18n.t(`papers.clc_short.${v}`)]),
);
