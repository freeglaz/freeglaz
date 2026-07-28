import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * Easter egg — cracktro Grostrad (a demoscene/cracking wink).
 *
 * Opened in a centered MODAL (styled like the app's modals: dimmed + blurred
 * backdrop, bg-bg/border/rounded-[14px]/shadow-2xl container, × button in the
 * corner) — no longer the previous full-screen scene.
 *
 * Loaded ON DEMAND (conditional render on the parent side): nothing runs until
 * the overlay is mounted. The scene is a standalone page
 * (`/easter/freeglaz_egg.html`) in an ISOLATED IFRAME — never in the JS bundle —
 * so it does not interfere with the app (audio, RAF, styles).
 *
 * Closing (all → `onClose`, which unmounts the iframe → cuts audio/RAF):
 *   - click on the backdrop (outside the modal);
 *   - × button in the corner;
 *   - Escape;
 *   - `freeglaz-egg-close` message posted by the scene (click/Escape INSIDE the
 *     iframe: `closeEgg()` cuts the audio via AudioContext.close() then postMessage).
 *
 * Note: audio is unlocked via the in-frame click of the scene's "attract" screen
 * (Chrome/Safari/mobile) → no autoplay permission required on the iframe.
 */
export default function EasterEgg({ onClose }) {
  const { t } = useTranslation();

  useEffect(() => {
    const onMessage = (e) => {
      if (e.data === 'freeglaz-egg-close') onClose();
    };
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('message', onMessage);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('message', onMessage);
      window.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-[120] bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="freeglaz"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      {/* App-styled modal, sized to a 4/3 ratio and bounded by 96vw / 90vh;
          overflow-hidden clips the iframe to the rounded corners. */}
      <div
        className="relative bg-bg border border-border-soft rounded-[14px] shadow-2xl overflow-hidden"
        style={{ width: 'min(680px, 96vw, calc(90vh * 4 / 3))', aspectRatio: '4 / 3' }}>
        {/* × in the corner (app modal style) — semi-opaque surface to stay legible
            over the dark scene. */}
        <button
          type="button"
          onClick={onClose}
          aria-label={t('common.close')}
          className="absolute top-2 right-2 z-10 w-7 h-7 rounded-md flex items-center justify-center bg-bg/70 backdrop-blur-sm border border-border-soft text-text-muted hover:text-text-strong hover:bg-sunken">
          <X size={16}/>
        </button>
        {/* Standalone scene isolated in an iframe (audio/RAF sandboxed); unmounted
            on close → cuts the sound. "Attract" screen: the 1st in-frame click
            unlocks audio everywhere, subsequent clicks close (postMessage). */}
        <iframe
          src="/easter/freeglaz_egg.html"
          title="freeglaz"
          className="block w-full h-full border-0"/>
      </div>
    </div>,
    document.body,
  );
}
