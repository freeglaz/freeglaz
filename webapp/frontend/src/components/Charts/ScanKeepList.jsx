// List of a chart's scans (ti3) with a ROLE per scan: Included | Excluded (segmented, active in
// blue → clearly an action). PRESENTATIONAL, shared (Measurements + scanned screen). Software
// mutation: the role is a durable flag (the ti3 is never deleted), reversible. Included =
// feeds the profile · Excluded = out of the profile but KEPT/displayed. The REASON (too fresh, botched)
// is read from the print→scan DELAY shown next to it, not from a 3rd role. onSetRole(ti3, role).
import { useTranslation } from 'react-i18next';
import { Eye, Trash2 } from 'lucide-react';

const _ROLES = ['included', 'excluded'];

// print→scan delay (seconds) → "+6 min" / "+8h32" ; null → "unknown" label
function formatDelay(sec, t) {
  if (sec == null) return t('scan.delay_unknown');
  if (sec < 0) return t('scan.delay_unknown');
  if (sec < 3600) return `+${Math.max(1, Math.round(sec / 60))} min`;
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return m > 0 ? `+${h}h${String(m).padStart(2, '0')}` : `+${h}h`;
}

export default function ScanKeepList({ scans, onSetRole, disabled, onView, onDelete }) {
  // onView/onDelete OPTIONAL → per-row icons (view measurements / delete the set). Provided
  // by Measurements (the management home); the wizard doesn't pass them → unchanged behavior.
  const { t } = useTranslation();
  if (!scans || scans.length === 0) return null;
  return (
    <div className="space-y-1">
      {scans.map((s) => {
        const rowCls = s.role === 'included' ? 'text-text-muted'
                     : 'text-text-faint line-through opacity-60';   // excluded
        return (
          <div key={s.ti3}
               className={`flex items-center justify-between gap-2 text-xs2 border-b border-border-soft/40 py-1 ${rowCls}`}>
            <span className="font-mono truncate no-underline">{s.ti3}</span>
            <span className="flex items-center gap-2 flex-shrink-0 no-underline not-italic">
              <span className="text-text-faint">{s.n_patches != null ? `${s.n_patches}p` : '—'}</span>
              <span className="text-text-faint tabular-nums" title={t('scan.delay_title')}>
                {formatDelay(s.delay_seconds, t)}
              </span>
              <span className="inline-flex rounded-md border border-border-soft overflow-hidden text-tiny">
                {_ROLES.map((role, i) => (
                  <button key={role} type="button" disabled={disabled}
                          title={t(`scan.roles.${role}_title`)}
                          onClick={() => { if (s.role !== role) onSetRole(s.ti3, role); }}
                          className={`px-2 py-0.5 transition-colors ${i > 0 ? 'border-l border-border-soft' : ''} ${
                            s.role === role ? 'bg-accent text-on-accent' : 'text-text-muted hover:bg-sunken'
                          } disabled:opacity-50`}>
                    {t(`scan.roles.${role}`)}
                  </button>
                ))}
              </span>
              {onView && (
                <button type="button" title={t('scan.measures.view')} onClick={() => onView(s.ti3)}
                        className="text-text-faint hover:text-accent p-0.5"><Eye size={14}/></button>
              )}
              {onDelete && (
                <button type="button" disabled={disabled} title={t('scan.measures.delete')}
                        onClick={() => onDelete(s.ti3)}
                        className="text-text-faint hover:text-danger p-0.5 disabled:opacity-40"><Trash2 size={13}/></button>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}
