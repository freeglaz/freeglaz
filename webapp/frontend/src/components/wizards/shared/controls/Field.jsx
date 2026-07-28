export default function Field({ label, hint, children }) {
  return (
    <div className="mb-3.5">
      <label className="block text-[11.5px] font-semibold text-text-strong mb-1.5 tracking-[-0.005em]">
        {label}
      </label>
      {children}
      {hint && <div className="mt-1.5 text-[11px] text-text-faint leading-[1.4]">{hint}</div>}
    </div>
  );
}
