/**
 * Unit tests for the Convert-tab visibility persistence (default + read/write
 * contract). Pure logic only — the React wiring (nav gate, redirect) has no test
 * harness in this repo (no jsdom/testing-library; adding one = new dependency,
 * out of scope) and is covered by the build + the gate being a one-line guard.
 *
 * Run: node webapp/frontend/src/hooks/useShowConvertTab.test.mjs
 */
import { getStoredShowConvertTab } from './useShowConvertTab.js';
import { safeLocalSet } from '../lib/safeLocalStorage.js';

const KEY = 'freeglaz.showConvertTab';   // contract shared with the hook
let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log(`  ✓ ${msg}`); }
  else { fail++; console.error(`  ✗ ${msg}`); }
}
function makeLS() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

// Default: no localStorage at all (e.g. locked-down webview) → HIDDEN, no throw.
delete globalThis.localStorage;
assert(getStoredShowConvertTab() === false, 'default HIDDEN when localStorage is absent');

// Fresh browser (key never written) → HIDDEN by default.
globalThis.localStorage = makeLS();
assert(getStoredShowConvertTab() === false, 'default HIDDEN when key is unset');

// Round-trip the exact encoding the hook writes ('1' shown / '0' hidden).
safeLocalSet(KEY, '1');
assert(getStoredShowConvertTab() === true, "'1' → tab VISIBLE (persisted)");
safeLocalSet(KEY, '0');
assert(getStoredShowConvertTab() === false, "'0' → tab HIDDEN (persisted)");

// Any unexpected value falls back to HIDDEN (never accidentally shown).
globalThis.localStorage.setItem(KEY, 'true');
assert(getStoredShowConvertTab() === false, "unexpected value → HIDDEN (safe default)");

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
