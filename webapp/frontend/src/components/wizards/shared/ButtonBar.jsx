export default function ButtonBar({ left, secondary, primary, danger = false }) {
  return (
    <div className="px-[18px] py-[13px] bg-sunken/50 border-t border-border-soft flex items-center gap-[9px]">
      {left && <SecondaryBtn {...left}/>}
      <div className="flex-1"/>
      {secondary && <SecondaryBtn {...secondary}/>}
      {primary && <PrimaryBtn {...primary} isDanger={danger}/>}
    </div>
  );
}

function PrimaryBtn({ label, disabled, onClick, isDanger }) {
  return (
    <button
      type="button" onClick={onClick} disabled={disabled}
      className={`py-[9px] px-[18px] rounded-[7px] text-[12.5px] font-semibold tracking-[-0.005em] border-none transition-colors ${
        disabled
          ? 'bg-sunken-deep text-text-faint cursor-not-allowed shadow-none'
          : isDanger
            ? 'bg-danger hover:bg-danger/90 text-white shadow-sm cursor-pointer'
            : 'bg-accent hover:bg-accent-press text-on-accent shadow-sm cursor-pointer'
      }`}>
      {label}
    </button>
  );
}

function SecondaryBtn({ label, disabled, onClick }) {
  return (
    <button
      type="button" onClick={onClick} disabled={disabled}
      className={`py-[9px] px-[14px] rounded-[7px] text-[12.5px] font-medium border border-border-soft bg-transparent transition-colors ${
        disabled ? 'text-text-faint opacity-55 cursor-not-allowed' : 'text-text-strong hover:bg-sunken cursor-pointer'
      }`}>
      {label}
    </button>
  );
}
