/**
 * Shared top nav (spec §1).
 *
 * 48px bar at the top of all pages:
 *   ● freeglaz  │  Print  Papers              ● HP DesignJet Z9 · Ready
 *
 * - Left: gradient dot + label + vertical separator
 * - Center: Print / Papers tabs — 2px HP Blue underline on the active one
 * - Right: compact Z9 state (color dot + text). Comes from the same
 *   ``status`` as the bottom StatusBar, but condensed into a single item.
 *
 * The queue remains accessible via the status bar — no dedicated tab in
 * the TopNav (spec §1 decision).
 */
import { useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { UIState } from '../../lib/state-machine.js';
import ZMonogram from '../ui/ZMonogram.jsx';
import EasterEgg from '../EasterEgg/EasterEgg.jsx';

export default function TopNav({ path, onNavigate, state, showConvert = false }) {
  const { t } = useTranslation();
  const [eggOpen, setEggOpen] = useState(false);   // easter egg — loaded on demand
  // Hidden trigger: 5 quick clicks on the logo within 1.5s (a single click does
  // nothing — no visible affordance). Keeps recent click timestamps in a ref.
  const eggClicks = useRef([]);
  const onLogoClick = () => {
    const now = Date.now();
    eggClicks.current = eggClicks.current.filter((t0) => now - t0 < 1500);
    eggClicks.current.push(now);
    if (eggClicks.current.length >= 5) {
      eggClicks.current = [];
      setEggOpen(true);
    }
  };
  const isConvert  = path === '/convert';
  const isPrint    = path !== '/convert' && path !== '/papers' && path !== '/logs' && path !== '/settings' && path !== '/profils' && path !== '/mesures';
  const isPapers   = path === '/papers';
  const isMesures  = path === '/mesures';
  const isProfils  = path === '/profils';
  const isLogs     = path === '/logs';

  // Compact Z9 state on the right — simplified mirror of the StatusBar
  const z9Label =
    state === UIState.D_NOPAPER ? t('status_bar.no_paper') :
    state === UIState.E_PRINTING ? t('status_bar.printing') :
    t('status_bar.ready');
  const z9Dot =
    state === UIState.D_NOPAPER ? 'bg-danger' :
    state === UIState.E_PRINTING ? 'bg-accent' :
    'bg-success';

  return (
    <>
    <header
      role="banner"
      className="h-12 bg-surface border-b border-border-soft flex items-center px-4 gap-3 flex-shrink-0 overflow-hidden">
      {/* PRIORITY logo (never shrinks): Z monogram + wordmark, splash typography.
          Hidden easter egg on 5 quick clicks (no visible affordance, never announced). */}
      <div
        className="flex items-center gap-2.5 flex-shrink-0 select-none"
        onClick={onLogoClick}>
        <ZMonogram size={24}/>
        <span className="font-serif text-[22px] leading-none tracking-[-0.03em] text-text-strong">
          <span style={{ fontWeight: 300 }}>free</span><span style={{ fontWeight: 600 }}>gla</span><span style={{ fontWeight: 700 }}>Z</span>
        </span>
      </div>
      <div className="w-px h-5 bg-border-soft flex-shrink-0" aria-hidden="true"/>

      {/* Tabs — adapt under constraint (horizontal scroll, never wrapping);
          the logo stays priority. */}
      <nav className="flex items-center gap-0.5 min-w-0 overflow-x-auto no-scrollbar">
        {showConvert && (
          <NavTab
            label={t('nav.convert')}
            active={isConvert}
            onClick={() => onNavigate('/convert')}/>
        )}
        <NavTab
          label={t('nav.print')}
          active={isPrint}
          onClick={() => onNavigate('/print')}/>
        <NavTab
          label={t('nav.papers')}
          active={isPapers}
          onClick={() => onNavigate('/papers')}/>
        <NavTab
          label={t('nav.mesures')}
          active={isMesures}
          onClick={() => onNavigate('/mesures')}/>
        <NavTab
          label={t('nav.profils')}
          active={isProfils}
          onClick={() => onNavigate('/profils')}/>
        <NavTab
          label={t('nav.logs')}
          active={isLogs}
          onClick={() => onNavigate('/logs')}/>
        <NavTab
          label={t('nav.settings')}
          active={path === '/settings'}
          onClick={() => onNavigate('/settings')}/>
      </nav>

      <div className="flex-1"/>

      {/* Compact Z9 state on the right */}
      <div
        role="status"
        aria-label={t('status_bar.z9_state_aria', { state: z9Label })}
        className="flex items-center gap-2 text-xs2 text-text-muted flex-shrink-0 ml-2">
        <span className={`w-1.5 h-1.5 rounded-full ${z9Dot}`} aria-hidden="true"/>
        <span className="font-medium text-text-strong">HP DesignJet Z9</span>
        <span>· {z9Label}</span>
      </div>
    </header>
    {eggOpen && <EasterEgg onClose={() => setEggOpen(false)} />}
    </>
  );
}


function NavTab({ label, active, onClick }) {
  // 2px HP Blue underline on the active one (spec §1). Padding bottom so
  // the border-bottom is visible without shifting the text.
  const baseCls =
    'relative px-3 py-1.5 text-sm font-medium transition-colors whitespace-nowrap shrink-0 ' +
    'focus-visible:outline-none focus-visible:bg-sunken rounded-md';
  const stateCls = active
    ? 'text-text-strong'
    : 'text-text-muted hover:text-text-strong';
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={`${baseCls} ${stateCls}`}>
      {label}
      {active && (
        <span
          aria-hidden="true"
          className="absolute left-3 right-3 bottom-0 h-0.5 bg-accent rounded-full"/>
      )}
    </button>
  );
}
