import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export default function ModalHeader({ eyebrow, title, subtitle, onClose }) {
  const { t } = useTranslation();
  return (
    <div className="px-[22px] pt-[18px] pb-[14px] flex items-start gap-3.5">
      <div className="flex-1 min-w-0">
        {eyebrow && (
          <div className="text-[10px] font-bold tracking-[0.10em] uppercase text-accent mb-1.5 font-mono">
            {eyebrow}
          </div>
        )}
        <h2 className="text-[17px] font-semibold text-text-strong tracking-[-0.01em] leading-[1.3]">
          {title}
        </h2>
        {subtitle && (
          <p className="text-xs text-text-muted mt-1 leading-[1.5]">{subtitle}</p>
        )}
      </div>
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          title={t('common.close_esc')}
          className="w-7 h-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-strong hover:bg-sunken transition-colors flex-shrink-0 -mt-0.5 -mr-1">
          <X size={13} strokeWidth={1.7}/>
        </button>
      )}
    </div>
  );
}
