import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react';

const ICONS = { info: Info, warn: AlertTriangle, danger: XCircle, success: CheckCircle2 };
const STYLES = {
  info:    'bg-accent/[0.06] border-accent/[0.22] text-accent',
  warn:    'bg-icc-warn/10 border-icc-warn/[0.38] text-icc-warn-deep',
  danger:  'bg-danger/10 border-danger/40 text-danger',
  success: 'bg-success/10 border-success/40 text-success',
};

export default function Banner({ kind = 'info', title, children }) {
  const Icon = ICONS[kind];
  return (
    <div role={kind === 'danger' ? 'alert' : kind === 'warn' ? 'status' : undefined}
         className={`flex items-start gap-2.5 p-[11px_13px] rounded-[7px] border ${STYLES[kind]}`}>
      <Icon size={14} className="flex-shrink-0 mt-px" aria-hidden="true"/>
      <div className="flex-1 text-[11.5px] leading-[1.5] text-text-strong">
        {title && <span className="font-semibold mr-1">{title}</span>}
        <span className={title ? 'text-text-muted' : ''}>{children}</span>
      </div>
    </div>
  );
}
