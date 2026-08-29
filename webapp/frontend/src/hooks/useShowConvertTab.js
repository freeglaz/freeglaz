import { useEffect, useState } from 'react';
import { safeLocalGet, safeLocalSet } from '../lib/safeLocalStorage.js';

/**
 * Visibility of the (experimental) Convert tab.
 *
 * Convert is functional but RESEARCH-ONLY (proven to the PRN, one A4 print
 * validated, but not a production-hardened path). Its tab is therefore HIDDEN by
 * default and opt-in via Settings — so nobody assumes it is production-validated.
 *
 * Persisted client-side with the SAME mechanism as the theme
 * (safeLocalStorage, cf. useTheme). Absent value → hidden (default false).
 * Single source of truth: call this ONCE in App and pass the value/setter down
 * (TopNav gate + Settings control), so a toggle updates the nav live.
 */
const STORAGE_KEY = 'freeglaz.showConvertTab';

export function getStoredShowConvertTab() {
  // Read DURING render (useState initializer) → protected access mandatory
  // (localStorage may throw/be absent in the pywebview webview, cf. useTheme).
  return safeLocalGet(STORAGE_KEY) === '1';   // default false (hidden)
}

export function useShowConvertTab() {
  const [show, setShow] = useState(getStoredShowConvertTab);
  useEffect(() => { safeLocalSet(STORAGE_KEY, show ? '1' : '0'); }, [show]);
  return { showConvertTab: show, setShowConvertTab: setShow };
}
