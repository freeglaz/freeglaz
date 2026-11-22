import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { XCircle, AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react';
import { identityRefusal, instrumentLabel } from '../../lib/measurementMethod.js';

// Structured refusal of the measurement identity (backend 409: measurement_identity_incompatible
// / _insufficient, measurement_record_invalid, scans_not_comparable): what differs between which
// scans, what the user can do, and the technical details kept for diagnostics.
function valueText(t, item, side) {
  const v = item[side];
  if (v == null) return t('measurement.value_unknown');
  if (item.field === 'instrument') return instrumentLabel(t, v);
  if (item.field === 'condition') return t(`measurement.condition.${v}`, { defaultValue: v });
  if (item.field === 'method') return t(`measurement.method_short.${v}`, { defaultValue: v });
  return v;
}

export default function IdentityRefusal({ error, compact = false }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const r = identityRefusal(error);
  if (!r) return null;
  const soft = r.code === 'measurement_identity_insufficient';
  return (
    <div role="alert" data-identity-refusal={r.code}
         className={`rounded-md border px-3 py-2 text-xs2 space-y-1.5 ${soft ? 'border-icc-warn/40 bg-icc-warn/10' : 'border-danger/40 bg-danger/10'}`}>
      <div className={`flex items-start gap-1.5 font-semibold ${soft ? 'text-text-strong' : 'text-danger'}`}>
        {soft ? <AlertTriangle size={13} className="mt-0.5 flex-shrink-0 text-icc-warn"/> : <XCircle size={13} className="mt-0.5 flex-shrink-0"/>}
        <span>{t(`errors.${r.code}`)}</span>
      </div>
      {r.conflicts.map((c, i) => (
        <div key={i} className="pl-5 space-y-0.5">
          <div className="font-mono text-tiny text-text-muted break-all">{c.a} ↔ {c.b}</div>
          <ul className="list-disc pl-4 text-text-strong">
            {c.items.map((it, j) => (
              <li key={j} data-difference={it.code}>
                {t(`measurement.difference.${it.code}`, { a: valueText(t, it, 'a'), b: valueText(t, it, 'b') })}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {r.recordError && (
        <p className="pl-5 text-text-strong">{t(`measurement.record_error.${r.recordError.code}`, { defaultValue: r.recordError.code })}</p>
      )}
      {!compact && r.remedies.length > 0 && (
        <ul className="pl-5 space-y-0.5 text-text-strong">
          {r.remedies.map((m) => <li key={m} data-remedy={m}>→ {t(`measurement.remedy.${m}`, { defaultValue: m })}</li>)}
        </ul>
      )}
      <button type="button" onClick={() => setOpen(!open)}
              className="pl-5 text-tiny text-accent hover:underline inline-flex items-center gap-0.5">
        {open ? <ChevronDown size={11}/> : <ChevronRight size={11}/>}{t('measurement.technical_details')}
      </button>
      {open && (
        <pre className="ml-5 max-h-[160px] overflow-auto bg-sunken/60 border border-border-soft rounded p-2 text-tiny font-mono whitespace-pre-wrap break-all">
          {r.code}{'\n'}{r.message}{r.recordError ? `\n${r.recordError.code}: ${r.recordError.message}` : ''}
        </pre>
      )}
    </div>
  );
}
