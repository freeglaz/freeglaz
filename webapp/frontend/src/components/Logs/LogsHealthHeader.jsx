import { useTranslation } from 'react-i18next';

export default function LogsHealthHeader({ unreadCount, hasFatal, onClearAlerts }) {
  const { t } = useTranslation();
  const ok = unreadCount === 0;
  // COMMON header container (bg-surface, 75px) like the other screens → no
  // jump. The state SIGNAL is still carried by the colored badge (dot + ping:
  // green nominal / amber warning / red fatal), NOT by the background. Zero loss
  // of info, consistent neutral background.
  const shell = 'px-[22px] h-[75px] flex items-center gap-3.5 border-b border-border-soft bg-surface';

  if (ok) {
    return (
      <div className={shell}>
        <div className="w-3.5 h-3.5 rounded-full bg-success flex-shrink-0"/>
        <div className="flex-1 text-[13.5px] font-semibold text-text-strong tracking-[-0.005em]">
          {t('logs.health_nominal')}
        </div>
      </div>
    );
  }

  const dotColor = hasFatal ? 'bg-danger' : 'bg-icc-warn';

  return (
    <div className={shell}>
      <div className="relative w-3.5 h-3.5 flex-shrink-0">
        <span className={`absolute inset-0 rounded-full ${dotColor}`}/>
        <span className={`absolute -inset-1 rounded-full border-[1.6px] ${hasFatal ? 'border-danger' : 'border-icc-warn'} opacity-40 animate-ping`}/>
      </div>
      <div className="flex-1">
        <div className="text-[13.5px] font-semibold text-text-strong tracking-[-0.005em]">
          {t('logs.health_unread', { count: unreadCount })}
        </div>
      </div>
      {onClearAlerts && (
        <button
          type="button"
          onClick={onClearAlerts}
          className="text-[11px] text-text-muted hover:text-text-strong font-medium transition-colors">
          {t('logs.clear_alerts')}
        </button>
      )}
    </div>
  );
}
