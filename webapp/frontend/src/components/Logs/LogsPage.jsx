import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useLogs } from './hooks/useLogs.js';
import { safeLocalGet, safeLocalSet } from '../../lib/safeLocalStorage.js';
import LogsHealthHeader from './LogsHealthHeader.jsx';
import LogsSidebar from './LogsSidebar.jsx';
import LogRow from './LogRow.jsx';
import LogCluster from './LogCluster.jsx';

function fmtDateHeader(iso, t) {
  const d = new Date(iso);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const dt = new Date(d); dt.setHours(0, 0, 0, 0);
  const days = Math.floor((today - dt) / 86400000);
  if (days === 0) return t('logs.today');
  if (days === 1) return t('logs.yesterday');
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'long' });
}

const LS_KEY = 'freeglaz.lastReadLogTimestamp';

export default function LogsPage() {
  const { t } = useTranslation();
  const [severities, setSeverities] = useState(['Fatal', 'Error', 'Warning', 'Info']);
  const [hideRoutine, setHideRoutine] = useState(true);
  const [liveTail, setLiveTail] = useState(false);
  const [lastReadTs, setLastReadTs] = useState(() => safeLocalGet(LS_KEY, '1970-01-01T00:00:00Z'));
  const autoMarkRef = useRef(null);

  const { logs, visible, counts, loading } = useLogs({ severity: severities, hideRoutine, liveTail });

  const unreadAlerts = useMemo(() =>
    logs.filter((l) => l.timestamp > lastReadTs && (l.severity === 'Error' || l.severity === 'Fatal')),
    [logs, lastReadTs],
  );
  const hasFatal = unreadAlerts.some((l) => l.severity === 'Fatal');

  const markRead = useCallback(() => {
    const now = new Date().toISOString();
    safeLocalSet(LS_KEY, now);
    setLastReadTs(now);
  }, []);

  useEffect(() => {
    if (unreadAlerts.length > 0) {
      autoMarkRef.current = setTimeout(markRead, 5000);
      return () => clearTimeout(autoMarkRef.current);
    }
  }, [unreadAlerts.length, markRead]);

  const toggleSev = (sev) => {
    setSeverities((prev) => prev.includes(sev) ? prev.filter((s) => s !== sev) : [...prev, sev]);
  };

  let lastDate = null;

  return (
    <div className="flex-1 flex min-w-0">
      <LogsSidebar
        severities={severities} onToggleSeverity={toggleSev}
        hideRoutine={hideRoutine} onToggleHideRoutine={() => setHideRoutine((h) => !h)}
        liveTail={liveTail} onToggleLiveTail={() => setLiveTail((l) => !l)}
        counts={counts}/>

      <div className="flex-1 flex flex-col min-w-0">
        <LogsHealthHeader
          unreadCount={unreadAlerts.length}
          hasFatal={hasFatal}
          onClearAlerts={unreadAlerts.length > 0 ? markRead : null}/>

        {/* Meta bar */}
        <div className="px-[22px] py-2.5 flex items-center gap-3.5 border-b border-border-soft text-[11.5px]">
          <span className="font-mono text-text-muted">
            <span className="text-text-strong font-semibold">{visible.length}</span> {t('logs.meta_shown')}
          </span>
          <div className="flex-1"/>
        </div>

        {/* Column headers */}
        <div className="px-[22px] py-2 flex items-center gap-3.5 text-[10px] font-bold tracking-[0.08em] uppercase text-text-faint font-mono border-b border-border-soft">
          <div className="w-[78px]">{t('logs.col_time')}</div>
          <div className="w-[74px]">{t('logs.col_severity')}</div>
          <div className="w-[118px]">{t('logs.col_code')}</div>
          <div className="flex-1">{t('logs.col_detail')}</div>
          <div className="w-[22px]"/>
        </div>

        {/* List */}
        {loading ? (
          <div className="flex-1 flex items-center justify-center text-text-faint text-sm">
            {t('logs.loading')}
          </div>
        ) : visible.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center gap-3.5 text-center p-10">
            <div className="w-14 h-14 rounded-[14px] bg-sunken text-text-faint flex items-center justify-center">
              <Search size={22}/>
            </div>
            <div className="text-sm font-semibold text-text-strong">{t('logs.empty_title')}</div>
            <div className="text-xs text-text-muted max-w-[300px]">{t('logs.empty_hint')}</div>
          </div>
        ) : (
          <div className="flex-1 overflow-auto">
            {visible.map((item, i) => {
              const e = item.type === 'event' ? item.event : item.events[0];
              const dateKey = new Date(e.timestamp).toDateString();
              const showHeader = dateKey !== lastDate;
              lastDate = dateKey;
              return (
                <div key={i}>
                  {showHeader && (
                    <div className="px-[22px] pt-2.5 pb-1.5 text-[10.5px] font-semibold tracking-[0.08em] uppercase text-text-faint font-mono">
                      {fmtDateHeader(e.timestamp, t)}
                    </div>
                  )}
                  {item.type === 'event'
                    ? <LogRow event={item.event}/>
                    : <LogCluster events={item.events}/>}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
