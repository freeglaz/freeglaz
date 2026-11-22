// Deep link to a chart in the Measurements tab: /mesures#chart=<chart_id>. The wizard is
// mounted outside the router's owner (Papers, Profiles), so it navigates the History API and
// tells useRoute through a popstate event; MesuresPage selects the chart from the hash.

export function libraryHref(chartId) {
  return `/mesures#chart=${encodeURIComponent(chartId)}`;
}

/** chart_id named by a location hash (`#chart=…`, possibly among other `&` parameters). */
export function chartIdFromHash(hash) {
  const m = /(?:^#|&)chart=([^&]+)/.exec(hash || '');
  if (!m) return null;
  try { return decodeURIComponent(m[1]); } catch { return null; }
}

export function openInLibrary(chartId) {
  window.history.pushState(null, '', libraryHref(chartId));
  window.dispatchEvent(new PopStateEvent('popstate'));
}
