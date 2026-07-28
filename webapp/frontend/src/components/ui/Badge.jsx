// Semantic badge. kind = neutral | info | ok | warn | danger.
// `info` (glaz accent) = selection / CUSTOM status. `ok` = valid / QC pass.
// `warn` (amber) = non-blocking uncertainty (stale, borderline). `danger`
// (red) = critical alert (ICC mismatch: distorted color rendering) —
// distinct from `warn` (cf. color semantics: red=danger, amber=uncertainty).
const styles = {
  neutral: 'bg-sunken text-text-muted border-transparent',
  info:    'bg-accent-soft text-accent border-accent/30',
  ok:      'bg-icc-ok/15 text-icc-ok border-icc-ok/30',
  warn:    'bg-icc-warn/15 text-icc-warn border-icc-warn/30',
  danger:  'bg-danger/15 text-danger border-danger/30',
};
export default function Badge({ kind = 'neutral', className = '', children }) {
  return (
    <span className={`inline-flex items-center gap-[5px] text-micro px-2 py-[3px] rounded-md font-semibold tracking-wide border ${styles[kind]} ${className}`}>
      {children}
    </span>
  );
}
