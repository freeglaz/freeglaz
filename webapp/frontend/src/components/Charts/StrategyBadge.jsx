import { useTranslation } from 'react-i18next';
import { Layers } from 'lucide-react';
import { strategyBadge } from '../../lib/profilingStrategy.js';

// "Pass 1/2" pill on the charts of a two-pass profiling (lists, drawer, wizard). Nothing for a
// one-pass chart.
export default function StrategyBadge({ strategy }) {
  const { t } = useTranslation();
  const b = strategyBadge(strategy);
  if (!b) return null;
  return (
    <span data-strategy-badge={b.pass} title={t('strategy.badge_title')}
          className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-accent/10 text-accent whitespace-nowrap">
      <Layers size={10}/>{b.index ? t('strategy.badge', { index: b.index, total: b.total }) : b.pass}
    </span>
  );
}
