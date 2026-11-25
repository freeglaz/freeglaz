import { ChevronDown, ChevronRight } from 'lucide-react';

// Fields shared by the guided compositions of the wizard (one pass, two passes): the same
// rows, number inputs and folding sections, so both read and behave the same way.

export function NumberField({ value, onChange, min, max, step = 1, width = 'w-24', disabled = false, attr }) {
  return (
    <input type="number" value={value} min={min} max={max} step={step} disabled={disabled} data-field={attr}
           onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
           className={`${width} bg-sunken rounded px-2 py-1 text-xs2 text-text-strong font-mono disabled:opacity-50`}/>
  );
}

export function Row({ label, children, hint }) {
  return (
    <div className="space-y-0.5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs2 text-text-muted w-48 flex-shrink-0">{label}</span>
        <span className="flex items-center gap-2 flex-wrap">{children}</span>
      </div>
      {hint && <p className="text-tiny text-text-faint leading-snug pl-[12.5rem] max-sm:pl-0">{hint}</p>}
    </div>
  );
}

export function Collapsible({ title, open, onToggle, children, attr }) {
  return (
    <div className="rounded-md border border-border-soft" data-collapsible={attr} data-open={open ? '1' : '0'}>
      <button type="button" onClick={onToggle}
              className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-xs2 font-medium text-text-strong hover:bg-sunken/60">
        {open ? <ChevronDown size={13}/> : <ChevronRight size={13}/>}{title}
      </button>
      {open && <div className="px-2.5 pb-2.5 pt-1 space-y-2">{children}</div>}
    </div>
  );
}

// The header card of a composition: what this chart is for, in two lines.
export function ComposeHeader({ title, intro, attr }) {
  return (
    <div className="rounded-md bg-accent/[0.06] border border-accent/30 px-3 py-2 text-xs2 leading-snug">
      <div className="font-semibold text-text-strong" data-pass-header={attr}>{title}</div>
      <p className="text-text-muted mt-0.5">{intro}</p>
    </div>
  );
}
