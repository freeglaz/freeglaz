export default function Check({ on, label, hint, disabled = false, onChange }) {
  return (
    <label className={`flex items-start gap-[11px] p-[12px_14px] rounded-[7px] mb-2.5 border transition-colors ${
      disabled
        ? 'bg-sunken border-border-soft cursor-not-allowed opacity-65'
        : on
          ? 'bg-accent/[0.05] border-accent/[0.34] cursor-pointer'
          : 'bg-surface border-border-soft cursor-pointer'
    }`}>
      <input type="checkbox" checked={on} disabled={disabled} onChange={onChange} className="sr-only"/>
      <span className={`w-4 h-4 rounded mt-px flex-shrink-0 flex items-center justify-center ${
        on && !disabled ? 'bg-accent' : 'border-[1.5px] border-border-strong'
      }`}>
        {on && !disabled && (
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="white" strokeWidth="2.2" strokeLinecap="round">
            <path d="M2 5l2 2L8 3"/>
          </svg>
        )}
      </span>
      <div className="flex-1">
        <div className="text-[12.5px] font-semibold text-text-strong">{label}</div>
        {hint && <div className="text-[11.5px] text-text-muted leading-[1.5] mt-0.5">{hint}</div>}
      </div>
    </label>
  );
}
