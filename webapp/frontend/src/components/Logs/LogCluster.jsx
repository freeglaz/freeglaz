import { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';

function fmtTime(iso) {
  const d = new Date(iso);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':');
}

export default function LogCluster({ events }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const first = events[0];

  return (
    <div className={`border-b border-border-soft opacity-85 pl-[22px] ${open ? 'bg-sunken/50' : ''}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-3.5 py-2.5 pr-[22px] text-left">
        <span className="w-[78px] text-[11px] text-text-muted font-mono tabular-nums flex-shrink-0">
          {fmtTime(first.timestamp)}
        </span>
        <span className="w-[74px] flex-shrink-0">
          <span className="inline-flex items-center gap-[5px] py-0.5 px-2 rounded-full bg-sunken text-text-muted text-[9.5px] font-semibold tracking-[0.04em] uppercase">
            <span className="w-1.5 h-1.5 rounded-full bg-success"/>
            {t('logs.routine')}
          </span>
        </span>
        <span className="w-[118px] text-[11px] text-text-faint font-mono flex-shrink-0">
          {first.event_code}
        </span>
        <span className="flex-1 min-w-0 text-xs text-text-muted">
          {t('logs.cluster_label', { count: events.length })}
        </span>
        <ChevronDown size={9} className={`text-text-muted transition-transform flex-shrink-0 ${open ? 'rotate-180' : ''}`}/>
      </button>

      {open && (
        <div className="pb-3 pr-[22px] pl-[92px] border-t border-dashed border-border-soft pt-2">
          {events.map((e, i) => (
            <div key={i} className="flex gap-3 py-0.5 text-[11px] font-mono text-text-muted">
              <span className="text-text-faint w-[60px]">#{e.sequence_number}</span>
              <span className="text-text-faint w-[74px]">:{e.source_line}</span>
              <span className="flex-1 text-text-strong truncate">{e.event_detail}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
