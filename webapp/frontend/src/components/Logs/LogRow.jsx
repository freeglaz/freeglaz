import { useState } from 'react';
import { ChevronDown, Copy } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import SeverityPill from './SeverityPill.jsx';

function fmtTime(iso) {
  const d = new Date(iso);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':');
}

export default function LogRow({ event: e }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const isSevere = e.severity === 'Fatal' || e.severity === 'Error';

  return (
    <div className={`border-b border-border-soft ${open ? 'bg-sunken/50' : ''} ${
      isSevere ? 'border-l-[3px] border-l-danger pl-[19px]' : 'pl-[22px]'
    }`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-3.5 py-2.5 pr-[22px] text-left">
        <span className="w-[78px] text-[11px] text-text-muted font-mono tabular-nums flex-shrink-0">
          {fmtTime(e.timestamp)}
        </span>
        <span className="w-[74px] flex-shrink-0">
          <SeverityPill severity={e.severity}/>
        </span>
        <span className="w-[118px] text-[11px] text-text-faint font-mono flex-shrink-0">
          {e.event_code}
        </span>
        <span className="flex-1 min-w-0 text-xs text-text-strong font-mono truncate">
          {e.event_detail}
        </span>
        <ChevronDown size={9} className={`text-text-muted transition-transform flex-shrink-0 ${open ? 'rotate-180' : ''}`}/>
      </button>

      {open && (
        <div className="pb-3 pr-[22px] pl-[92px] flex flex-col gap-1.5">
          {[
            [t('logs.meta_source'), `${e.source_file}:${e.source_line}`],
            [t('logs.meta_internal'), String(e.internal_error_code)],
            [t('logs.meta_firmware'), e.firmware_revision],
            [t('logs.meta_sequence'), String(e.sequence_number)],
          ].map(([k, v]) => (
            <div key={k} className="flex gap-3.5 items-baseline">
              <span className="w-24 text-[10.5px] text-text-faint font-semibold tracking-[0.06em] uppercase font-mono">{k}</span>
              <span className="flex-1 text-[11.5px] text-text-strong font-mono break-all">{v}</span>
            </div>
          ))}
          <button
            type="button"
            onClick={() => navigator.clipboard?.writeText(JSON.stringify(e, null, 2))}
            className="mt-1.5 inline-flex items-center gap-1.5 px-2.5 py-1 border border-border-soft rounded-[5px] text-[11px] text-text-muted font-medium hover:bg-sunken transition-colors w-fit">
            <Copy size={10}/> {t('logs.copy_json')}
          </button>
        </div>
      )}
    </div>
  );
}
