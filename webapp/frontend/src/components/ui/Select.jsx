import { useState, useRef, useEffect } from 'react';
import { ChevronDown } from 'lucide-react';

// Minimal select — trigger + absolute menu. No accessible-roving keyboard
// for this phase, to be completed if the need arises.
export default function Select({ value, options, onChange, disabled = false }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);
  // Normalize options: accepts string[] (label = value) or {value,label}[].
  const items = (options || []).map((o) =>
    typeof o === 'string' ? { value: o, label: o } : o,
  );
  const current = items.find((o) => o.value === value);
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={`flex items-center gap-1.5 text-xs text-text-strong bg-sunken pl-2.5 pr-2 py-1 rounded cursor-pointer ${disabled ? 'opacity-40' : 'hover:bg-sunken-deep'}`}>
        <span>{current?.label ?? value}</span>
        <ChevronDown size={11} className="text-text-muted" aria-hidden="true"/>
      </button>
      {open && (
        <div
          role="listbox"
          className="absolute right-0 top-full mt-1 z-10 min-w-[120px] bg-surface border border-border-soft rounded-md shadow-lg py-1">
          {items.map((opt) => (
            <button
              key={opt.value}
              type="button"
              role="option"
              aria-selected={opt.value === value}
              onClick={() => { onChange?.(opt.value); setOpen(false); }}
              className={`w-full text-left text-xs px-3 py-1.5 hover:bg-sunken ${opt.value === value ? 'text-text-strong font-medium' : 'text-text-muted'}`}>
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
