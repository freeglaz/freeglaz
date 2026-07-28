import { FileText } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * Empty state — no custom in the firmware (spec §6 ``empty``).
 * Welcome message + button that expands the HP factory section.
 */
export default function EmptyState({ onBrowseFactory }) {
  const { t } = useTranslation();
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 text-center">
      <div className="w-14 h-14 rounded-full bg-sunken flex items-center justify-center mb-3">
        <FileText size={28} strokeWidth={1.5} className="text-text-faint" aria-hidden="true"/>
      </div>
      <h2 className="text-sm font-medium text-text-strong mb-1.5">
        {t('papers.empty_state_title')}
      </h2>
      <p className="text-xs2 text-text-muted leading-relaxed max-w-md mb-4">
        {t('papers.empty_state_message')}
      </p>
      {onBrowseFactory && (
        <button
          type="button"
          onClick={onBrowseFactory}
          className="px-3 py-1.5 rounded-md text-xs2 font-medium border border-border-strong hover:bg-sunken text-text-strong transition-colors">
          {t('papers.empty_state_button')}
        </button>
      )}
    </div>
  );
}
