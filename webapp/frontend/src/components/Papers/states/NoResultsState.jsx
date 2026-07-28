import { SearchX } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * "No results" state — filters too restrictive (spec §6 ``no-results``).
 */
export default function NoResultsState({ onReset }) {
  const { t } = useTranslation();
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 text-center">
      <SearchX size={36} strokeWidth={1.5} className="text-text-faint mb-3" aria-hidden="true"/>
      <h2 className="text-sm font-medium text-text-strong mb-1.5">
        {t('papers.no_results_title')}
      </h2>
      <p className="text-xs2 text-text-muted leading-relaxed max-w-md mb-4">
        {t('papers.no_results_message')}
      </p>
      {onReset && (
        <button
          type="button"
          onClick={onReset}
          className="px-3 py-1.5 rounded-md text-xs2 font-medium border border-border-strong hover:bg-sunken text-text-strong transition-colors">
          {t('papers.no_results_button')}
        </button>
      )}
    </div>
  );
}
