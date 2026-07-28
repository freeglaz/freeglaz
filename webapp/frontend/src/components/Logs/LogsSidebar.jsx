import { useTranslation } from 'react-i18next';

const SEV_COLORS = {
  Fatal: 'bg-danger', Error: 'bg-danger', Warning: 'bg-icc-warn', Info: 'bg-accent', Debug: 'bg-text-muted',
};

export default function LogsSidebar({
  severities, onToggleSeverity,
  hideRoutine, onToggleHideRoutine,
  liveTail, onToggleLiveTail,
  counts,
}) {
  const { t } = useTranslation();

  return (
    <aside className="w-[260px] bg-surface border-r border-border-soft flex flex-col">
      <div className="px-[18px] pt-[18px] flex-1 overflow-auto">
        {/* Live tail toggle */}
        <Toggle on={liveTail} onToggle={onToggleLiveTail} label="Live tail" accent>
          {liveTail && (
            <span className="flex items-center gap-[5px] text-[10.5px] text-accent font-mono">
              <span className="w-[5px] h-[5px] rounded-full bg-accent animate-ping"/> 5 s
            </span>
          )}
        </Toggle>

        {/* Hide routine toggle */}
        <Toggle on={hideRoutine} onToggle={onToggleHideRoutine} label={t('logs.hide_routine')}/>

        {/* Severity filters */}
        <div className="text-[10.5px] font-medium tracking-[0.08em] uppercase text-text-faint mt-[18px] mb-2.5">
          {t('logs.filter_severity')}
        </div>
        {['Fatal', 'Error', 'Warning', 'Info', 'Debug'].map((sev) => (
          <SevCheck
            key={sev}
            on={severities.includes(sev)}
            label={sev}
            color={SEV_COLORS[sev]}
            count={counts[sev.toLowerCase()] || 0}
            onToggle={() => onToggleSeverity(sev)}/>
        ))}
      </div>
    </aside>
  );
}


function Toggle({ on, onToggle, label, accent, children }) {
  return (
    <div className={`mt-2.5 p-[9px_11px] rounded-[7px] border flex items-center gap-2.5 ${
      on && accent ? 'bg-accent/10 border-accent/[0.27]' : 'bg-surface border-border-soft'
    }`}>
      <button type="button" onClick={onToggle} className="relative w-[18px] h-[10px] flex-shrink-0">
        <span className={`absolute inset-0 rounded-full transition-colors ${on ? 'bg-accent' : 'bg-sunken-deep'}`}/>
        <span className={`absolute top-px w-2 h-2 rounded-full bg-white transition-[left] ${on ? 'left-[9px]' : 'left-px'}`}/>
      </button>
      <span className="flex-1 text-xs text-text-strong font-medium">{label}</span>
      {children}
    </div>
  );
}


function SevCheck({ on, label, color, count, onToggle }) {
  return (
    <label className="flex items-center gap-2.5 py-[5px] px-0.5 cursor-pointer text-[12.5px] text-text-strong">
      <input type="checkbox" checked={on} onChange={onToggle} className="sr-only"/>
      <span className={`w-3.5 h-3.5 rounded-[3px] flex items-center justify-center flex-shrink-0 ${
        on ? 'bg-accent text-white' : 'border-[1.5px] border-border-strong'
      }`}>
        {on && <svg width="9" height="9" viewBox="0 0 9 9" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 4.5L4 6.5 7.5 2.5"/></svg>}
      </span>
      <span className={`w-2 h-2 rounded-full ${color} flex-shrink-0`}/>
      <span className="flex-1">{label}</span>
      <span className="text-[11px] text-text-faint font-mono tabular-nums">{count}</span>
    </label>
  );
}
