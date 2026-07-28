const COLORS = {
  Fatal:   { fg: 'text-white', bg: 'bg-danger', dot: 'bg-white' },
  Error:   { fg: 'text-danger', bg: 'bg-danger/10', dot: 'bg-danger', border: 'border-danger/35' },
  Warning: { fg: 'text-icc-warn-deep', bg: 'bg-icc-warn/[0.12]', dot: 'bg-icc-warn', border: 'border-icc-warn/35' },
  Info:    { fg: 'text-accent', bg: 'bg-accent/10', dot: 'bg-accent', border: 'border-accent/[0.32]' },
  Debug:   { fg: 'text-text-muted', bg: 'bg-sunken', dot: 'bg-text-muted', border: 'border-transparent' },
};

export default function SeverityPill({ severity }) {
  const c = COLORS[severity] || COLORS.Debug;
  const isFatal = severity === 'Fatal';

  return (
    <span className={`inline-flex items-center gap-[5px] py-0.5 px-2 rounded-full text-[9.5px] font-semibold tracking-[0.04em] uppercase ${c.fg} ${c.bg} ${!isFatal && c.border ? `border ${c.border}` : ''}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`}/>
      {severity}
    </span>
  );
}
