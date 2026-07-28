import { useEffect, useState } from 'react';
import { Power, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * "Wake the Z9" button in the StatusBar.
 *
 * Appears ONLY when the Z9 is detected asleep / unreachable
 * — the parent passes ``visible`` based on ``status.z9_state === 'error'``.
 *
 * Behavior:
 *  - Click → ``onWake()`` async (POST /api/wake on the parent side)
 *  - During the operation: spinner + elapsed counter (local 1s tick)
 *  - Button ``disabled`` during the wake
 *  - The parent handles the success/failure toast and hides the button on return
 *
 * @param {object} p
 * @param {boolean} p.visible
 * @param {boolean} p.busy — true while the wake operation is in progress
 * @param {() => Promise<void>} p.onWake
 */
export default function WakeButton({ visible, busy, onWake }) {
  const { t } = useTranslation();
  const [elapsed, setElapsed] = useState(0);

  // Elapsed counter during the wake: ticks every second while busy.
  useEffect(() => {
    if (!busy) {
      setElapsed(0);
      return;
    }
    setElapsed(0);
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [busy]);

  if (!visible) return null;

  const handleClick = () => {
    if (busy) return;
    onWake?.();
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={busy}
      aria-label={busy ? t('wake.in_progress_aria', { elapsed }) : t('wake.button')}
      title={t('wake.button_tooltip')}
      className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-xs2 font-medium transition-colors border ${
        busy
          ? 'border-warn/30 bg-warn/10 text-warn cursor-wait'
          : 'border-accent/40 bg-accent/10 text-accent hover:bg-accent/15'
      }`}>
      {busy ? (
        <>
          <Loader2 size={11} strokeWidth={2.5} className="animate-spin" aria-hidden="true"/>
          <span className="tabular-nums">{t('wake.in_progress', { elapsed })}</span>
        </>
      ) : (
        <>
          <Power size={11} strokeWidth={2.5} aria-hidden="true"/>
          <span>{t('wake.button')}</span>
        </>
      )}
    </button>
  );
}
