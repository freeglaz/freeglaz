import { useTranslation } from 'react-i18next';
import { stripMap } from '../../lib/externalSession.js';

// Every strip pass of the chart, in chartread's order: saved (validated by the backend),
// read but not saved yet, the one chartread asks for, still to read. Presentational only.
// Solid theme tokens only: the Tailwind theme maps colours to CSS variables, on which the
// opacity modifiers (bg-x/30) generate nothing.
const TONE = {
  saved: 'bg-success text-white border-success',
  acquired: 'bg-warn text-text-strong border-warn',
  current: 'bg-accent text-on-accent border-accent ring-2 ring-accent ring-offset-1 ring-offset-bg',
  pending: 'bg-transparent text-text-faint border-border-soft',
};

export default function StripMap({ snap }) {
  const { t } = useTranslation();
  const cells = stripMap(snap);
  if (!cells.length) return null;
  return (
    <div data-strip-map={cells.length}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] font-semibold uppercase tracking-[0.06em] text-text-muted">
          {t('external_measure.read.strip_map')}
        </span>
        <span className="flex items-center gap-2 text-[10px] text-text-muted">
          {['saved', 'current', 'acquired', 'pending'].map((k) => (
            <span key={k} className="inline-flex items-center gap-1">
              <span className={`inline-block w-2.5 h-2.5 rounded-sm border ${TONE[k]}`}/>
              {t(`external_measure.read.legend.${k}`)}
            </span>
          ))}
        </span>
      </div>
      <div className="flex flex-wrap gap-[3px] max-h-[132px] overflow-y-auto">
        {cells.map((c) => (
          <span key={c.label} data-strip={c.label} data-status={c.status}
                title={`${c.label} — ${t(`external_measure.read.legend.${c.status}`)}`}
                className={`inline-flex items-center justify-center min-w-[26px] h-[18px] px-1 rounded-sm border text-[9.5px] font-mono ${TONE[c.status]}`}>
            {c.label}
          </span>
        ))}
      </div>
    </div>
  );
}
