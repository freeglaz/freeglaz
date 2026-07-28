import { AlertTriangle, RotateCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * Error state (spec §6 ``error``) — reuses the pattern.
 * Red card + mono technical code + "Retry" button.
 */
export default function ErrorState({ message, onRetry }) {
  const { t } = useTranslation();
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 text-center"
         role="alert">
      <div className="w-12 h-12 rounded-full bg-danger/10 border border-danger/30 flex items-center justify-center mb-3">
        <AlertTriangle size={22} className="text-danger" aria-hidden="true"/>
      </div>
      <div className="text-sm font-semibold text-text-strong mb-1.5">
        {t('papers.error_title')}
      </div>
      <p className="text-xs2 text-text-muted leading-relaxed max-w-md mb-4">
        {t('papers.error_message')}
      </p>
      {message && (
        <pre className="text-tiny font-mono text-text-faint bg-sunken px-2.5 py-1.5 rounded mb-4 max-w-md whitespace-pre-wrap break-all">
          {message}
        </pre>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs2 font-medium border border-border-strong hover:bg-sunken text-text-strong transition-colors">
          <RotateCw size={11} strokeWidth={2.5} aria-hidden="true"/>
          {t('common.retry')}
        </button>
      )}
    </div>
  );
}
