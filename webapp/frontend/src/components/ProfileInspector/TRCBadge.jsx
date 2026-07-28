import { useTranslation } from 'react-i18next';

const TRC_STYLE = {
  linear:         { dot: '#2BA0A0', label_key: 'inspector.trc.linear' },
  'gamma_1.0':    { dot: '#2BA0A0', label_key: 'inspector.trc.linear' },
  'gamma_1.8':    { dot: '#D49B3D', label_key: 'inspector.trc.gamma_1_8' },
  'gamma_2.2':    { dot: '#B87333', label_key: 'inspector.trc.gamma_2_2' },
  'gamma_2.4':    { dot: '#B87333', label_key: 'inspector.trc.gamma_2_4' },
  srgb:           { dot: '#3A8540', label_key: 'inspector.trc.srgb' },
  lstar:          { dot: '#8A4FB6', label_key: 'inspector.trc.lstar' },
  rec709:         { dot: '#1F5FB8', label_key: 'inspector.trc.rec709' },
  unknown_custom: { dot: '#666666', label_key: 'inspector.trc.custom' },
};


export default function TRCBadge({ family, size = 'md' }) {
  const { t } = useTranslation();
  const style = TRC_STYLE[family] || TRC_STYLE.unknown_custom;
  const pad = size === 'sm' ? 'px-1.5 py-px text-[10px]' : 'px-2 py-0.5 text-[11px]';
  return (
    <span
      className={`inline-flex items-center gap-1.5 ${pad} rounded font-medium bg-sunken text-text-strong border border-border-soft`}>
      <span
        className="w-1.5 h-1.5 rounded-full shrink-0"
        style={{ backgroundColor: style.dot }}
        aria-hidden="true"/>
      {t(style.label_key)}
    </span>
  );
}
