export default function Segmented({ options, value, onChange }) {
  return (
    <div className="flex bg-sunken rounded-[7px] p-[3px] gap-0.5">
      {options.map((o) => {
        const sel = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange?.(o.value)}
            className={`flex-1 text-center py-[7px] px-1.5 rounded-[5px] text-[11.5px] tracking-[-0.005em] transition-all ${
              sel
                ? 'font-semibold text-text-strong bg-surface shadow-segtab'
                : 'font-medium text-text-muted bg-transparent hover:text-text-strong cursor-pointer'
            }`}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
