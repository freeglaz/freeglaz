import { useTranslation } from 'react-i18next';
import { AlertTriangle } from 'lucide-react';
import Toggle from '../ui/Toggle.jsx';

/**
 * Settings control: show/hide the experimental Convert tab. Controlled (value +
 * onChange come from App, the single source of truth) so toggling updates the
 * nav live. Reuses the existing Toggle. The status mention is shown INLINE and
 * always visible — its whole point is that the choice is made knowingly (Convert
 * is research-only, not production-validated).
 */
export default function ConvertTabSetting({ value, onChange }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-4">
        <span className="text-xs2 font-medium text-text-strong">
          {t('settings.convert_tab_label')}
        </span>
        <Toggle on={value} onChange={onChange}/>
      </div>
      <p className="text-tiny text-text-faint">{t('settings.convert_tab_hint')}</p>
      <div className="flex items-start gap-1.5 text-xs2 text-warn">
        <AlertTriangle size={13} className="mt-0.5 flex-shrink-0" aria-hidden="true"/>
        <span>{t('settings.convert_tab_status')}</span>
      </div>
    </div>
  );
}
