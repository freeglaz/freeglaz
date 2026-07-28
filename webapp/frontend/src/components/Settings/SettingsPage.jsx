import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Printer } from 'lucide-react';
import LanguageSwitcher from './LanguageSwitcher.jsx';
import ThemeSwitcher from './ThemeSwitcher.jsx';
import GamutSettings from './GamutSettings.jsx';
import ArgyllSettings from './ArgyllSettings.jsx';
import PrintersModal from './PrintersModal.jsx';

/**
 * Settings page.
 */
export default function SettingsPage() {
  const { t } = useTranslation();
  const [printersOpen, setPrintersOpen] = useState(false);
  return (
    <div className="flex-1 flex flex-col min-w-0">
      {/* Common header bar (bg-surface, 75px, title on the left) — the title was
          MOVED here from the content (no more duplicate). */}
      <div className="sticky top-0 z-10 bg-surface border-b border-border-soft px-5 h-[75px] flex items-center gap-3">
        <h1 className="text-[15px] font-semibold text-text-strong tracking-tight">
          {t('settings.title')}
        </h1>
      </div>
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-5">
        <div className="bg-surface border border-border-soft rounded-xl p-5 max-w-2xl space-y-5">
          <LanguageSwitcher/>
          <ThemeSwitcher/>
        </div>
        <div className="bg-surface border border-border-soft rounded-xl p-5 max-w-2xl">
          <h2 className="text-xs2 font-semibold uppercase tracking-wider
                         text-accent mb-4 font-mono">
            {t('settings.gamut.section_title')}
          </h2>
          <GamutSettings/>
        </div>

        <div className="bg-surface border border-border-soft rounded-xl p-5 max-w-2xl">
          <h2 className="text-xs2 font-semibold uppercase tracking-wider
                         text-accent mb-1 font-mono">
            {t('settings.argyll.section_title')}
          </h2>
          <p className="text-xs text-text-muted mb-4 leading-relaxed">
            {t('settings.argyll.section_hint')}
          </p>
          <ArgyllSettings/>
        </div>

        <div className="bg-surface border border-border-soft rounded-xl p-5 max-w-2xl">
          <h2 className="text-xs2 font-semibold uppercase tracking-wider
                         text-accent mb-1 font-mono">
            {t('settings.printers.section_title')}
          </h2>
          <p className="text-xs text-text-muted mb-4 leading-relaxed">
            {t('settings.printers.section_hint')}
          </p>
          <button className="btn-secondary text-sm px-3.5 py-2 inline-flex items-center gap-1.5"
                  onClick={() => setPrintersOpen(true)}>
            <Printer size={15}/> {t('settings.printers.manage_button')}
          </button>
        </div>
      </div>

      <PrintersModal open={printersOpen} onClose={() => setPrintersOpen(false)}/>
    </div>
  );
}
