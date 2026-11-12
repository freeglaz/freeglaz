import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { History, Loader2 } from 'lucide-react';
import * as api from '../../api/client.js';
import Badge from '../ui/Badge.jsx';
import { historyRow, orderPair, statusBadges } from '../../lib/profileHistory.js';

// Status (v2) of the current profile + history of the immutable versions of the chart (newest
// first). Read-only: no purge, no deletion in this version. Select two versions to compare them.
const BADGE_KIND = { ok: 'ok', warn: 'warn', danger: 'danger', neutral: 'neutral' };

export default function ProfileHistory({ chartId, status, refreshKey = 0, onCompare }) {
  const { t, i18n } = useTranslation();
  const key = chartId ? `${chartId}#${refreshKey}` : null;
  const [res, setRes] = useState({ key: null, data: null, error: null });
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState([]);
  useEffect(() => {
    if (!key) return undefined;
    let alive = true;
    api.listBuilds(chartId)
      .then((d) => { if (alive) setRes({ key, data: d, error: null }); })
      .catch((e) => { if (alive) setRes({ key, data: null, error: e?.message || 'error' }); });
    return () => { alive = false; };
  }, [key]);   // eslint-disable-line react-hooks/exhaustive-deps -- chartId is in the key
  const data = res.key === key ? res.data : null;
  const rows = (data?.builds || []).map(historyRow);
  const badges = statusBadges(status);
  const fmt = (iso) => { try { return iso ? new Date(iso).toLocaleString(i18n.language) : '—'; } catch { return iso; } };
  const toggle = (id) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id].slice(-2)));
  const pair = orderPair(sel);

  return (
    <div className="space-y-1.5" data-profile-history>
      {badges.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" data-profile-status={status?.state}>
          {badges.map((b) => (
            <span key={b.code} title={t(`${b.key}_title`)}><Badge kind={BADGE_KIND[b.kind]}>{t(b.key)}</Badge></span>
          ))}
          {status?.missing_input?.length > 0 && (
            <span className="text-tiny text-text-faint">{status.missing_input.map((m) => m.scan || m.path || m.chart_id || m.kind).join(', ')}</span>
          )}
        </div>
      )}
      <button type="button" onClick={() => setOpen(!open)} data-action="toggle-history"
              className="inline-flex items-center gap-1.5 text-xs2 text-accent hover:bg-accent/10 px-2 py-1 rounded-md">
        <History size={13}/> {t('profile_history.title', { count: rows.length })}
      </button>
      {open && !data && !res.error && <div className="text-tiny text-text-muted flex items-center gap-1"><Loader2 size={11} className="animate-spin"/>{t('profile_history.loading')}</div>}
      {open && res.key === key && res.error && <div className="text-tiny text-danger">{t('profile_history.error')}</div>}
      {open && data && (
        <div className="space-y-1">
          {data.publication?.state && data.publication.state !== 'ok' && data.publication.state !== 'no_profile' && (
            <div className="text-tiny text-text-faint">{t('profile_history.publication', { state: data.publication.state })}</div>
          )}
          {rows.length === 0 && <div className="text-tiny text-text-faint">{t('profile_history.empty')}</div>}
          <ul className="divide-y divide-border-soft/50 border border-border-soft rounded-md">
            {rows.map((r) => (
              <li key={r.id} className="px-2 py-1.5 text-tiny flex items-start gap-2" data-build-id={r.id}>
                <input type="checkbox" checked={sel.includes(r.id)} disabled={!r.comparable} onChange={() => toggle(r.id)}
                       aria-label={t('profile_history.select', { id: r.shortId })} className="mt-0.5"/>
                <div className="flex-1 min-w-0 space-y-0.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-text-strong font-semibold">{r.preserved ? t('profile_history.preserved') : (r.label || '—')}</span>
                    {r.current && <Badge kind="ok">{t('profile_history.current')}</Badge>}
                    {!r.intact && <Badge kind="danger">{t('profile_history.corrupt')}</Badge>}
                    <span className="text-text-faint">{fmt(r.date)}</span>
                  </div>
                  {r.preserved ? (
                    <div className="text-text-faint">{r.provenanceUnknown ? t('profile_history.provenance_unknown') : t('profile_history.provenance_partial')}</div>
                  ) : (
                    <div className="text-text-muted">
                      {t('profile_history.summary', { base: r.base ? t(`build_preview.base.${r.base}`) : '—', scans: r.nScans ?? '—',
                        extra: r.nExtra ?? 0, profiles: r.nProfiles ?? 0, excluded: r.nExcluded ?? '—', holes: r.nHoles ?? '—' })}
                    </div>
                  )}
                  <div className="text-text-faint font-mono break-all">
                    {r.flags ? `colprof ${r.flags} · ` : ''}{r.id}{r.manifest ? '' : ` · ${t('profile_history.no_manifest')}`}
                  </div>
                </div>
              </li>
            ))}
          </ul>
          {onCompare && (
            <button type="button" disabled={!pair} onClick={() => pair && onCompare(pair)} data-action="compare-builds"
                    className="text-xs2 font-semibold px-3 py-1 rounded-md border border-border-soft text-text-strong hover:bg-sunken disabled:opacity-50">
              {t('profile_history.compare')}
            </button>
          )}
          <div className="text-tiny text-text-faint">{t('profile_history.kept_note')}</div>
        </div>
      )}
    </div>
  );
}
