// HP Ingenium brand blue = mirror of the --hp-origin token. Stays a hex
// literal (and not var(--hp-origin)) because it is used in SVG ATTRIBUTES
// (stroke/fill) and concatenated with a hex alpha (`${HP}40`) — contexts
// where var() is not resolved.
const HP = '#0096D6';

export const Picto = {
  create: () => (
    <svg width="144" height="96" viewBox="0 0 144 96" fill="none" strokeLinecap="round" strokeLinejoin="round" className="opacity-80 text-text-muted">
      <rect x="22" y="20" width="36" height="56" rx="2" stroke="currentColor" strokeWidth="1.5" fill={`${HP}40`}/>
      <line x1="30" y1="32" x2="50" y2="32" stroke="currentColor" strokeWidth="1.2"/>
      <line x1="30" y1="40" x2="50" y2="40" stroke="currentColor" strokeWidth="1.2"/>
      <line x1="30" y1="48" x2="46" y2="48" stroke="currentColor" strokeWidth="1.2"/>
      <path d="M64 48h16m0 0l-5-5m5 5l-5 5" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="86" y="14" width="36" height="56" rx="2" stroke={HP} strokeWidth="1.6" fill={`${HP}44`}/>
      <line x1="94" y1="26" x2="114" y2="26" stroke={HP} strokeWidth="1.2"/>
      <line x1="94" y1="34" x2="114" y2="34" stroke={HP} strokeWidth="1.2"/>
      <line x1="94" y1="42" x2="110" y2="42" stroke={HP} strokeWidth="1.2"/>
      <circle cx="116" cy="78" r="8" fill={HP}/>
      <path d="M116 75v6M113 78h6" stroke="#fff" strokeWidth="1.8"/>
    </svg>
  ),
  profile: () => (
    <svg width="144" height="96" viewBox="0 0 144 96" fill="none" strokeLinecap="round" strokeLinejoin="round" className="opacity-80 text-text-muted">
      <rect x="14" y="32" width="46" height="30" rx="3" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="22" y="40" width="30" height="20" fill={`${HP}40`} stroke="none"/>
      <path d="M22 24h30v8H22z" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="52" cy="46" r="1.4" fill="currentColor"/>
      <rect x="28" y="66" width="18" height="22" rx="1" fill={`${HP}30`} stroke="currentColor" strokeWidth="1.3"/>
      <line x1="32" y1="72" x2="42" y2="72" stroke="currentColor" strokeWidth="1.1"/>
      <line x1="32" y1="76" x2="42" y2="76" stroke="currentColor" strokeWidth="1.1"/>
      <line x1="32" y1="80" x2="38" y2="80" stroke="currentColor" strokeWidth="1.1"/>
      <path d="M64 50h10m0 0l-3.5-3.5M74 50l-3.5 3.5" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="78" y="38" width="40" height="22" rx="3" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="84" y="44" width="28" height="10" fill={`${HP}40`} stroke="none"/>
      <line x1="84" y1="49" x2="112" y2="49" stroke="currentColor" strokeWidth="1.1" strokeDasharray="2 2"/>
      <path d="M78 64h40v6H78z" stroke="currentColor" strokeWidth="1.5"/>
    </svg>
  ),
  profilePrint: () => (
    <svg width="144" height="96" viewBox="0 0 144 96" fill="none" strokeLinecap="round" strokeLinejoin="round" className="opacity-80 text-text-muted">
      <rect x="46" y="24" width="52" height="34" rx="3" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="55" y="32" width="34" height="22" fill={`${HP}40`} stroke="none"/>
      <path d="M56 14h32v10H56z" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="88" cy="40" r="1.5" fill="currentColor"/>
      <rect x="62" y="62" width="20" height="26" rx="1" fill={`${HP}40`} stroke="currentColor" strokeWidth="1.3"/>
      <line x1="66" y1="68" x2="78" y2="68" stroke="currentColor" strokeWidth="1.1"/>
      <line x1="66" y1="73" x2="78" y2="73" stroke="currentColor" strokeWidth="1.1"/>
      <line x1="66" y1="78" x2="74" y2="78" stroke="currentColor" strokeWidth="1.1"/>
      <line x1="66" y1="83" x2="78" y2="83" stroke="currentColor" strokeWidth="1.1"/>
    </svg>
  ),
  profileScan: () => (
    <svg width="144" height="96" viewBox="0 0 144 96" fill="none" strokeLinecap="round" strokeLinejoin="round" className="opacity-80 text-text-muted">
      <rect x="56" y="12" width="32" height="40" rx="1.5" fill={`${HP}40`} stroke="currentColor" strokeWidth="1.4"/>
      <line x1="62" y1="22" x2="82" y2="22" stroke="currentColor" strokeWidth="1.1"/>
      <line x1="62" y1="28" x2="82" y2="28" stroke="currentColor" strokeWidth="1.1"/>
      <line x1="62" y1="34" x2="82" y2="34" stroke="currentColor" strokeWidth="1.1"/>
      <line x1="62" y1="40" x2="78" y2="40" stroke="currentColor" strokeWidth="1.1"/>
      <path d="M72 56v10m0 0l-4-4m4 4l4-4" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="36" y="70" width="72" height="18" rx="3" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="44" y="75" width="56" height="8" fill={`${HP}40`} stroke="none"/>
      <line x1="44" y1="79" x2="100" y2="79" stroke="currentColor" strokeWidth="1.1" strokeDasharray="2 2"/>
    </svg>
  ),
  settings: () => (
    <svg width="144" height="96" viewBox="0 0 144 96" fill="none" strokeLinecap="round" strokeLinejoin="round" className="opacity-80 text-text-muted">
      <circle cx="72" cy="48" r="14" stroke={HP} strokeWidth="1.6" fill={`${HP}44`}/>
      <circle cx="72" cy="48" r="5" stroke={HP} strokeWidth="1.5"/>
      {Array.from({length:8}).map((_,i) => {
        const a = (i/8) * Math.PI * 2;
        const x1 = 72 + Math.cos(a)*14, y1 = 48 + Math.sin(a)*14;
        const x2 = 72 + Math.cos(a)*20, y2 = 48 + Math.sin(a)*20;
        return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={HP} strokeWidth="2.4" strokeLinecap="round"/>;
      })}
      <line x1="20" y1="22" x2="40" y2="22" stroke="currentColor" strokeWidth="1.4"/>
      <circle cx="34" cy="22" r="2.5" fill="currentColor"/>
      <line x1="104" y1="22" x2="124" y2="22" stroke="currentColor" strokeWidth="1.4"/>
      <circle cx="110" cy="22" r="2.5" fill="currentColor"/>
      <line x1="20" y1="74" x2="40" y2="74" stroke="currentColor" strokeWidth="1.4"/>
      <circle cx="28" cy="74" r="2.5" fill="currentColor"/>
      <line x1="104" y1="74" x2="124" y2="74" stroke="currentColor" strokeWidth="1.4"/>
      <circle cx="118" cy="74" r="2.5" fill="currentColor"/>
    </svg>
  ),
  computing: () => (
    <svg width="144" height="96" viewBox="0 0 144 96" fill="none" strokeLinecap="round" strokeLinejoin="round" className="opacity-80 text-text-muted">
      <circle cx="72" cy="48" r="32" stroke={HP} strokeWidth="1.6"/>
      {Array.from({length:12}).map((_,i) => {
        const a = (i/12) * Math.PI * 2 - Math.PI/2;
        const x1 = 72 + Math.cos(a)*22, y1 = 48 + Math.sin(a)*22;
        const x2 = 72 + Math.cos(a)*32, y2 = 48 + Math.sin(a)*32;
        return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={HP} strokeWidth="1.4"/>;
      })}
      <circle cx="72" cy="48" r="14" fill={`${HP}44`} stroke={HP} strokeWidth="1.4"/>
      <text x="72" y="52" textAnchor="middle" fontSize="10" fill={HP} stroke="none" fontFamily="monospace" letterSpacing="0.5">ICC</text>
    </svg>
  ),
  success: () => (
    <svg width="144" height="96" viewBox="0 0 144 96" fill="none" strokeLinecap="round" strokeLinejoin="round" className="opacity-75 text-text-muted">
      <rect x="38" y="14" width="50" height="62" rx="3" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M74 14v14h14" stroke="currentColor" strokeWidth="1.5"/>
      <line x1="48" y1="42" x2="78" y2="42" stroke="currentColor" strokeWidth="1.2"/>
      <line x1="48" y1="50" x2="78" y2="50" stroke="currentColor" strokeWidth="1.2"/>
      <line x1="48" y1="58" x2="68" y2="58" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="96" cy="78" r="14" fill="rgba(47,138,76,0.12)" stroke="var(--success)" strokeWidth="1.8"/>
      <path d="M89 78l5 5 9-9" stroke="var(--success)" strokeWidth="2.2"/>
    </svg>
  ),
  failure: () => (
    <svg width="144" height="96" viewBox="0 0 144 96" fill="none" strokeLinecap="round" strokeLinejoin="round" className="opacity-75 text-text-muted">
      <rect x="38" y="14" width="50" height="62" rx="3" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M74 14v14h14" stroke="currentColor" strokeWidth="1.5"/>
      <line x1="48" y1="42" x2="78" y2="42" stroke="currentColor" strokeWidth="1.2"/>
      <line x1="48" y1="50" x2="68" y2="50" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="96" cy="78" r="14" fill="rgba(200,54,43,0.10)" stroke="var(--danger)" strokeWidth="1.8"/>
      <path d="M90 78L96 84M90 84l6-6M96 72v6M96 80v.5" stroke="var(--danger)" strokeWidth="2.2"/>
    </svg>
  ),
  warn: () => (
    <svg width="144" height="96" viewBox="0 0 144 96" fill="none" strokeLinecap="round" strokeLinejoin="round" className="opacity-80 text-text-muted">
      <path d="M72 16L116 80H28z" stroke="var(--icc-warn)" strokeWidth="2" fill="rgba(201,122,30,0.06)"/>
      <line x1="72" y1="40" x2="72" y2="60" stroke="var(--icc-warn)" strokeWidth="2.4"/>
      <circle cx="72" cy="68" r="1.8" fill="var(--icc-warn)"/>
    </svg>
  ),
};
