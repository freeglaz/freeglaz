import { Component } from 'react';

/**
 * ROOT ErrorBoundary (around <App> in main.jsx).
 *
 * Turns the "fatal white" (an uncaught render error unmounts the whole
 * tree → empty #root → white background, with no recovery — fatal in a webview
 * that cannot F5) into a VISIBLE + RELOADABLE + diagnosable screen.
 *
 * HARDCODED styles (inline, NO theme CSS class / variable): the fallback
 * must display even if the CSS or theme has failed (a plausible cause of the white).
 * Class component required (getDerivedStateFromError / componentDidCatch).
 * Hardcoded FR+EN text (i18n could also have failed).
 */
export default class RootErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Visible in the console (webview devtools if debug=True) + captured by an
    // eventual window.onerror→backend (cf. diagnostics).
    console.error('RootErrorBoundary caught:', error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    const err = this.state.error;
    const detail = String(err?.stack || err?.message || err);
    return (
      <div
        role="alert"
        style={{
          position: 'fixed', inset: 0, zIndex: 2147483647,
          background: '#0b1417', color: '#e8eef0',
          display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center',
          gap: '16px', padding: '32px', textAlign: 'center',
          fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
        }}>
        <div style={{ fontSize: '18px', fontWeight: 600 }}>
          Une erreur est survenue au chargement
        </div>
        <div style={{ fontSize: '13px', color: '#9fb0b5' }}>
          A loading error occurred.
        </div>
        <pre style={{
          maxWidth: '760px', maxHeight: '40vh', overflow: 'auto', margin: 0,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          fontSize: '12px', color: '#9fb0b5', textAlign: 'left',
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          background: '#11201f', border: '1px solid #1e3330',
          borderRadius: '8px', padding: '12px',
        }}>
          {detail}
        </pre>
        <button
          type="button"
          onClick={() => window.location.reload()}
          style={{
            background: '#0096D6', color: '#ffffff', border: 'none',
            borderRadius: '8px', padding: '10px 22px',
            fontSize: '14px', fontWeight: 600, cursor: 'pointer',
          }}>
          Recharger / Reload
        </button>
      </div>
    );
  }
}
