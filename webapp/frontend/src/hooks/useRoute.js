import { useCallback, useEffect, useState } from 'react';

/**
 * Minimal routing hook — no external dependency (react-router is
 * overkill for 2 routes: /print and /papers).
 *
 * P1.B (25/05/2026). Pattern:
 *  - Initial: reads ``window.location.pathname``
 *  - ``navigate(path)`` : ``history.pushState`` + state update
 *  - Browser Back/Forward button: ``popstate`` listener that
 *    re-syncs the React state
 *  - If the initial path is ``/``, we reset to ``/print`` (canonical) without
 *    pushState so as not to pollute the history.
 */
export function useRoute() {
  const [path, setPath] = useState(() => {
    const initial = window.location.pathname || '/print';
    if (initial === '/' || initial === '') {
      // Redirect / → /print via replaceState (not pushState so as not to
      // create a spurious history entry)
      window.history.replaceState(null, '', '/print');
      return '/print';
    }
    return initial;
  });

  // popstate listener: browser back/forward syncs the state
  useEffect(() => {
    const onPop = () => {
      setPath(window.location.pathname || '/print');
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((newPath, { replace = false } = {}) => {
    if (newPath === path) return;  // no no-op history pollution
    if (replace) {
      window.history.replaceState(null, '', newPath);
    } else {
      window.history.pushState(null, '', newPath);
    }
    setPath(newPath);
  }, [path]);

  return { path, navigate };
}
