import { useCallback, useEffect, useRef, useState } from 'react';
import { safeLocalGet, safeLocalSet, safeLocalRemove } from '../lib/safeLocalStorage.js';

/**
 * localStorage persistence of the expanded/collapsed state of the
 * Papers Page sections (P1 — Bug 5).
 *
 * Key: ``freeglaz.papersPage.expandedSections``
 * Shape: ``{ favorites: bool, custom: bool, factory: bool }``
 *
 * Defaults: Favorites expanded (short content), Custom expanded (heart of
 * the product), HP factory collapsed (bulky).
 *
 * Unknown keys stored in localStorage are ignored; missing
 * keys take the default. Corrupt JSON → we fall back to
 * the defaults and clean up the key.
 */
const STORAGE_KEY = 'freeglaz.papersPage.expandedSections';
const DEFAULTS = Object.freeze({
  favorites: true,
  custom:    true,
  factory:   false,
});


function _readFromStorage() {
  const raw = safeLocalGet(STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    // Filter to only known keys, strict coercion to bool
    return {
      favorites: parsed.favorites !== undefined ? Boolean(parsed.favorites) : DEFAULTS.favorites,
      custom:    parsed.custom    !== undefined ? Boolean(parsed.custom)    : DEFAULTS.custom,
      factory:   parsed.factory   !== undefined ? Boolean(parsed.factory)   : DEFAULTS.factory,
    };
  } catch {
    // Malformed JSON: clean up
    safeLocalRemove(STORAGE_KEY);
    return null;
  }
}

function _writeToStorage(state) {
  // safeLocalSet no-op if localStorage unavailable/quota → state stays in memory.
  safeLocalSet(STORAGE_KEY, JSON.stringify(state));
}


export function useExpandedSections() {
  const [state, setState] = useState(() => _readFromStorage() || DEFAULTS);
  const firstRender = useRef(true);

  // Persist on every change (but not on init)
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    _writeToStorage(state);
  }, [state]);

  const toggle = useCallback((section) => {
    setState((curr) => ({ ...curr, [section]: !curr[section] }));
  }, []);

  const setExpanded = useCallback((section, value) => {
    setState((curr) => ({ ...curr, [section]: Boolean(value) }));
  }, []);

  return { expanded: state, toggle, setExpanded };
}
