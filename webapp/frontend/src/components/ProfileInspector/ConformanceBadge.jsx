import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CheckCircle2, AlertTriangle, XCircle, Info, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * ICC conformance badge. On click, opens a popover listing the
 * issues (moved from the former Internals tab → conformance no longer has
 * its own tab, the detail lives in the header badge). Data:
 * `conformance` ({ compliant, level, summary:{errors,warnings,infos}, issues }).
 */
export default function ConformanceBadge({ conformance, size = 'md' }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!conformance) return null;
  const level = conformance.level || 'compliant';
  const summary = conformance.summary || { errors: 0, warnings: 0 };

  const cfg = LEVEL_CFG[level] || LEVEL_CFG.compliant;
  const Icon = cfg.icon;

  let label;
  if (level === 'compliant') {
    label = t('inspector.conformance.label_compliant');
  } else if (level === 'warnings') {
    label = t('inspector.conformance.label_warnings', { count: summary.warnings });
  } else {
    label = t('inspector.conformance.label_non_compliant', {
      errors: summary.errors,
      warnings: summary.warnings,
    });
  }

  const pad = size === 'sm' ? 'px-1.5 py-px text-[10px]' : 'px-2 py-0.5 text-[11px]';
  const cls = `inline-flex items-center gap-1.5 ${pad} rounded font-medium
               border transition-colors ${cfg.cls} cursor-pointer hover:brightness-110`;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={t('inspector.conformance.click_to_view_details')}
        className={cls}>
        <Icon size={size === 'sm' ? 10 : 11} strokeWidth={2.2} aria-hidden="true"/>
        {label}
      </button>
      {open && (
        <ConformancePopover conformance={conformance} onClose={() => setOpen(false)}/>
      )}
    </>
  );
}


/** Centered popover (portal) listing the conformance issues. */
function ConformancePopover({ conformance, onClose }) {
  const { t } = useTranslation();
  const ref = useRef(null);
  const issues = conformance.issues || [];

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); onClose?.(); } };
    const onClick = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose?.(); };
    window.addEventListener('keydown', onKey);
    const t0 = setTimeout(() => window.addEventListener('mousedown', onClick), 0);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousedown', onClick);
      clearTimeout(t0);
    };
  }, [onClose]);

  return createPortal(
    <div
      ref={ref}
      role="dialog"
      aria-label={t('inspector.conformance.details_title')}
      className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[110]
                 w-[420px] max-w-[90vw] max-h-[80vh] overflow-y-auto
                 bg-surface border border-border-soft rounded-lg shadow-2xl p-4 text-text-strong">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-semibold">{t('inspector.conformance.details_title')}</span>
        <button
          type="button"
          onClick={onClose}
          title={t('common.close')}
          className="w-6 h-6 rounded flex items-center justify-center text-text-muted
                     hover:text-text-strong hover:bg-sunken transition-colors">
          <X size={13}/>
        </button>
      </div>

      {issues.length === 0 ? (
        <p className="text-xs2 text-text-muted">
          {t('inspector.internals.conformance_all_clean')}
        </p>
      ) : (
        <ul className="space-y-1.5">
          {issues.map((iss, i) => (
            <li key={i} className="flex items-start gap-2.5 py-1">
              <SeverityIcon severity={iss.severity}/>
              <div className="flex-1 min-w-0">
                <div className="text-xs2 text-text-strong">
                  {iss.message}
                </div>
                <div className="text-tiny text-text-faint font-mono mt-0.5">
                  {iss.code}
                  {iss.detail && (
                    <span className="text-text-muted not-italic"> · {iss.detail}</span>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>,
    document.body,
  );
}


function SeverityIcon({ severity }) {
  const props = { size: 13, strokeWidth: 2, 'aria-hidden': true,
                  className: 'mt-0.5 shrink-0' };
  if (severity === 'error') return <XCircle {...props} className={`${props.className} text-danger`}/>;
  if (severity === 'warning') return <AlertTriangle {...props} className={`${props.className} text-amber`}/>;
  if (severity === 'info') return <Info {...props} className={`${props.className} text-accent`}/>;
  return <CheckCircle2 {...props} className={`${props.className} text-success`}/>;
}


const LEVEL_CFG = {
  compliant: {
    icon: CheckCircle2,
    cls: 'bg-success/10 text-success border-success/30',
  },
  warnings: {
    icon: AlertTriangle,
    cls: 'bg-amber/10 text-amber border-amber/40',
  },
  non_compliant: {
    icon: XCircle,
    cls: 'bg-danger/10 text-danger border-danger/40',
  },
};
