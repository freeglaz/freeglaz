import { Minus, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export default function Stepper({ value, onChange, min = 1, max = 99, disabled = false }) {
  const { t } = useTranslation();
  const dec = () => onChange?.(Math.max(min, value - 1));
  const inc = () => onChange?.(Math.min(max, value + 1));
  return (
    <div className={`flex items-center bg-sunken rounded-md ${disabled ? 'opacity-40 pointer-events-none' : ''}`}>
      <button
        type="button"
        onClick={dec}
        aria-label={t('common.decrease')}
        className="w-[30px] h-[30px] flex items-center justify-center text-text-muted hover:text-text-strong rounded-l-md">
        <Minus size={11} strokeWidth={2} aria-hidden="true"/>
      </button>
      <div className="flex-1 text-center font-mono text-[13px] text-text-strong tabular-nums" aria-live="polite">{value}</div>
      <button
        type="button"
        onClick={inc}
        aria-label={t('common.increase')}
        className="w-[30px] h-[30px] flex items-center justify-center text-text-muted hover:text-text-strong rounded-r-md">
        <Plus size={11} strokeWidth={2} aria-hidden="true"/>
      </button>
    </div>
  );
}
