import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Info, Loader2, XCircle, ChevronDown, ChevronRight } from 'lucide-react';
import { summarizePreview } from '../../lib/buildPreview.js';

// What the next build WILL use (POST /profile/preview, apply_all) — shown before any build, in
// Measurements and in the scan wizard. Presentational: every number is the backend's. Warnings
// stay warnings; only a backend refusal disables the build.
function Groups({ groups, t, reasonKey }) {
  return (
    <ul className="pl-4 list-disc space-y-0.5">
      {(groups || []).map((g, i) => (
        <li key={i} className="font-mono text-tiny text-text-muted break-all">
          {g.chart_id} · {g.scan} : {(g.sids || []).join(', ')}
          {reasonKey && g.reason && <span className="font-sans text-text-faint"> — {t(`${reasonKey}.${g.reason}`, { defaultValue: g.reason })}</span>}
        </li>
      ))}
    </ul>
  );
}

export default function BuildPreviewSummary({ preview }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!preview || preview.status === 'idle') return null;
  if (preview.status === 'loading') {
    return <div className="text-tiny text-text-muted flex items-center gap-1.5" data-preview-status="loading">
      <Loader2 size={12} className="animate-spin"/>{t('build_preview.loading')}</div>;
  }
  if (preview.status === 'error') {
    return <div className="text-tiny text-danger bg-danger/10 border border-danger/30 rounded-md px-2.5 py-1.5" role="alert"
                data-preview-status="error">{t('build_preview.error', { message: preview.error?.message || '' })}</div>;
  }
  const p = preview.data;
  const s = summarizePreview(p);
  const box = s.refused ? 'border-danger/40 bg-danger/10' : (s.warnings.length || s.recommend) ? 'border-icc-warn/40 bg-icc-warn/10' : 'border-border-soft bg-sunken/50';
  return (
    <div className={`rounded-md border px-3 py-2 text-xs2 space-y-1 ${box}`} data-preview-status={s.refused ? 'refused' : 'ready'}
         data-excluded={s.nExcluded} data-holes={s.nHoles}>
      <div className="font-semibold text-text-strong">{t('build_preview.title')}</div>
      <div className="text-text-muted">
        {s.baseFallback ? t('build_preview.base_fallback', { requested: t(`build_preview.base.${s.baseRequested}`), effective: t(`build_preview.base.${s.baseEffective}`) })
                        : t('build_preview.base_used', { base: t(`build_preview.base.${s.baseEffective}`) })}
      </div>
      <div className="text-text-strong">{t('build_preview.excluded', { count: s.nExcluded })}
        {s.nInactive > 0 && <span className="text-text-muted"> · {t('build_preview.inactive', { count: s.nInactive })}</span>}
        {s.nHoles > 0 && <span className="text-text-muted"> · {t('build_preview.holes', { count: s.nHoles })}</span>}
      </div>
      {s.nNewlyApplied > 0 && (
        <div className="flex items-start gap-1.5 text-text-strong"><Info size={13} className="mt-0.5 flex-shrink-0 text-accent"/>
          <span>{t('build_preview.newly_applied', { count: s.nNewlyApplied })}</span></div>
      )}
      {s.nStrongIncluded > 0 && (
        <div className="flex items-start gap-1.5 text-text-strong"><AlertTriangle size={13} className="mt-0.5 flex-shrink-0 text-icc-warn"/>
          <span>{t('build_preview.strong_included', { count: s.nStrongIncluded })}</span></div>
      )}
      {s.warnings.length > 0 && (
        <div className="flex items-start gap-1.5"><AlertTriangle size={13} className="mt-0.5 flex-shrink-0 text-icc-warn"/>
          <span>{[...new Set(s.warnings.map((g) => g.code))].map((c) => t(`build_preview.guard.${c}`, { defaultValue: c })).join(' · ')}</span></div>
      )}
      {s.recommend && !s.refused && (
        <div className="text-text-strong">{t('build_preview.recommend')} <span className="text-text-muted">
          ({s.reasons.map((r) => t(`build_preview.reason.${r}`, { defaultValue: r })).join(' · ')})</span></div>
      )}
      {s.refused && (
        <div className="flex items-start gap-1.5 text-danger font-semibold" role="alert"><XCircle size={13} className="mt-0.5 flex-shrink-0"/>
          <span>{s.refusals.map((r) => t(`build_preview.refusal.${r}`, { defaultValue: r })).join(' ')}</span></div>
      )}
      {p.current_build_id && (
        <div className="text-tiny text-text-faint">{s.inputsChangedSinceCurrent ? t('build_preview.differs_current') : t('build_preview.same_as_current')}</div>
      )}
      {(s.nExcluded > 0 || s.nInactive > 0 || s.nUnmatched > 0) && (
        <button type="button" onClick={() => setOpen(!open)} className="text-tiny text-accent hover:underline inline-flex items-center gap-0.5"
                data-action="show-exclusions">
          {open ? <ChevronDown size={11}/> : <ChevronRight size={11}/>}{t('build_preview.show_list')}
        </button>
      )}
      {open && (
        <div className="space-y-1 pt-1">
          {s.nExcluded > 0 && <div><div className="text-tiny text-text-muted">{t('build_preview.list_applied')}</div><Groups groups={p.rejections_applied} t={t}/></div>}
          {s.nInactive > 0 && <div><div className="text-tiny text-text-muted">{t('build_preview.list_inactive')}</div>
            <Groups groups={p.rejections_not_applied} t={t} reasonKey="build_preview.inactive_reason"/></div>}
          {s.nUnmatched > 0 && <div><div className="text-tiny text-text-muted">{t('build_preview.list_unmatched')}</div><Groups groups={p.rejections_unmatched} t={t}/></div>}
        </div>
      )}
    </div>
  );
}
