import * as api from '../../api/client.js';

// Shared PRESENTATIONAL chart-card (zero context coupling): preview + chart_id +
// one `info` line (JSX, varies with context) + an `actions` slot (JSX) on the right.
// Reused by the scan wizard's SelectView AND the list in the Measurements tab.
// `onClick(chart)` = primary click (select-to-scan / open-the-detail).
// `selected` (optional) = highlights the card whose detail is open (Measurements drawer).
export default function ChartCard({ chart, onClick, info, actions, selected = false }) {
  return (
    <div className={`card w-full transition-colors px-3 py-2.5 flex items-center gap-3 ${
      selected ? 'border-accent bg-accent/5' : 'hover:border-accent/50'
    }`}>
      <button type="button" onClick={() => onClick?.(chart)}
              className="flex items-center gap-3 flex-1 min-w-0 text-left">
        <img src={api.chartPreviewUrl(chart.chart_id)} alt="" loading="lazy"
             className="w-16 h-12 object-contain bg-white rounded border border-border-soft flex-shrink-0"
             onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }}/>
        <div className="min-w-0 flex-1">
          <div className="text-xs2 font-mono text-text-strong truncate">{chart.chart_id}</div>
          <div className="text-tiny text-text-faint">{info}</div>
        </div>
      </button>
      {actions && <div className="flex items-center gap-1 flex-shrink-0">{actions}</div>}
    </div>
  );
}
