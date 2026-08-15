import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { getHealth } from '../../api/client.js';

/**
 * About: the running freeglaz version (from /api/health) + a repository link.
 * The version is the backend's — the single source of truth that travels with
 * the code into the packaged apps (see webapp/backend/version.py).
 */
export default function AboutSettings() {
  const { t } = useTranslation();
  const [version, setVersion] = useState(null);

  useEffect(() => {
    let alive = true;
    getHealth()
      .then((h) => { if (alive) setVersion(h?.version ?? '—'); })
      .catch(() => { if (alive) setVersion('—'); });
    return () => { alive = false; };
  }, []);

  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="text-sm text-text-strong">{t('settings.about.version_label')}</span>
      <span className="text-sm font-mono text-text-muted tabular-nums">
        {version === null ? '…' : version}
      </span>
    </div>
  );
}
