import { useState } from 'react';
import { ChevronRight } from 'lucide-react';

/**
 * Accordion for the HP factory sub-categories (spec §2).
 * Collapsed by default. Clickable header with counter.
 *
 * Animates height 200ms ease-out (spec §10).
 */
export default function CategoryAccordion({
  title, count, defaultOpen = false, children,
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-border-soft/30">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-[22px] py-2.5 hover:bg-sunken/30 transition-colors">
        <ChevronRight
          size={12} strokeWidth={2.5}
          className={`text-text-muted transition-transform ${open ? 'rotate-90' : ''}`}
          aria-hidden="true"/>
        <span className="text-[13px] font-medium text-text-strong">
          {title}
        </span>
        <span className="text-xs2 text-text-faint">
          ({count})
        </span>
      </button>
      {open && (
        <div className="bg-bg/50 animate-fadein">
          {children}
        </div>
      )}
    </div>
  );
}
