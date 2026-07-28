import { useTranslation } from 'react-i18next';

/**
 * Skeleton loading — 6 shimmer lines (spec §6 ``loading``).
 * Gradient bg-sunken → bg-sunken-deep animated to evoke
 * activity.
 */
export default function LoadingState() {
  const { t } = useTranslation();
  return (
    <div className="flex-1 p-4 space-y-2.5" role="status" aria-label={t('papers.loading_aria')}>
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="skel h-[58px] rounded-md"
          style={{ animationDelay: `${i * 100}ms` }}
          aria-hidden="true"/>
      ))}
      <span className="sr-only">{t('papers.loading_text')}</span>
    </div>
  );
}
