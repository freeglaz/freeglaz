import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * freeglaZ splash — immersive opening (deep teal glaz).
 *
 * DELIBERATE EXCEPTION: this splash lives OUTSIDE the 3 light themes (data-theme),
 * like the neutral data-viz background. Launch identity validated — do NOT
 * "harmonize" it to light nor wire it to data-theme. (It only uses the
 * invariant glaz anchors --abyss/--deepc/--glazc/--seac/--foamc.)
 *
 * Faithful reproduction of the Design sequence (freeglaz/Identity.html §08):
 *   1. the "freeglaZ" wordmark arrives zooming out and "comes into focus"
 *      (focusIn: scale 1.5 → 1, blur 7 → 0);
 *   2. then the Z monogram lands (landZ: same zoom-out + slight bounce
 *      via the overshoot cubic-bezier(.34,1.28,.5,1)) at 0.60 s;
 *   3. the tag fades in at 1.02 s.
 * ~1.5 s, non-blocking (the app loads behind), skippable on click/key, then
 * FADES to the app (which starts in light Brume) to avoid the dark→light flash.
 */
export default function Splash({ onDismiss }) {
  const { t } = useTranslation();
  const [leaving, setLeaving] = useState(false);
  const doneRef = useRef(false);

  useEffect(() => {
    const finish = () => {
      if (doneRef.current) return;
      doneRef.current = true;
      setLeaving(true);                       // triggers the fade (.44s) to the app
      setTimeout(() => onDismiss?.(), 440);
    };
    const auto = setTimeout(finish, 2500);    // end of sequence (~2.4 s) then fade — continuous motion, no dead pause
    window.addEventListener('click', finish);
    window.addEventListener('keydown', finish);
    return () => {
      clearTimeout(auto);
      window.removeEventListener('click', finish);
      window.removeEventListener('keydown', finish);
    };
  }, [onDismiss]);

  return (
    <div
      className={`splash-root fixed inset-0 z-[100] flex items-center justify-center overflow-hidden ${leaving ? 'splash-leaving' : ''}`}
      style={{ background: 'var(--abyss)' }}
      role="status"
      aria-label="freeglaZ">
      <style>{`
        .splash-root{ transition: opacity .44s ease; }
        .splash-leaving{ opacity: 0; }
        .splash-word{ animation: splashFocusIn 1s cubic-bezier(.2,.7,.3,1); }
        .splash-mark{ animation: splashLandZ .85s cubic-bezier(.34,1.28,.5,1) 1s backwards; }
        .splash-tag{ animation: splashFade .7s ease-out 1.7s backwards; }
        @keyframes splashFocusIn{
          0%{ opacity:0; transform:scale(1.5); filter:blur(7px); }
          55%{ opacity:1; }
          100%{ opacity:1; transform:scale(1); filter:blur(0); }
        }
        @keyframes splashLandZ{
          0%{ opacity:0; transform:scale(1.5); filter:blur(6px); }
          55%{ opacity:1; }
          80%{ transform:scale(.94); filter:blur(0); }
          100%{ opacity:1; transform:scale(1); filter:blur(0); }
        }
        @keyframes splashFade{ from{ opacity:0; } to{ opacity:1; } }
        .splash-waves .sw1{ animation: splashDriftA 13s linear infinite; }
        .splash-waves .sw2{ animation: splashDriftB 19s linear infinite; }
        .splash-waves .sw3{ animation: splashDriftC 25s linear infinite; }
        @keyframes splashDriftA{ 0%{transform:translate(0,0);} 50%{transform:translate(-200px,7px);} 100%{transform:translate(-400px,0);} }
        @keyframes splashDriftB{ 0%{transform:translate(-400px,0);} 50%{transform:translate(-200px,-6px);} 100%{transform:translate(0,0);} }
        @keyframes splashDriftC{ 0%{transform:translate(0,0);} 50%{transform:translate(-200px,5px);} 100%{transform:translate(-400px,0);} }
        @media (prefers-reduced-motion: reduce){
          .splash-word, .splash-mark, .splash-tag,
          .splash-waves .sw1, .splash-waves .sw2, .splash-waves .sw3{ animation: none; }
        }
      `}</style>

      {/* Vertical glaz gradient (abyss → foam), opacity 0.9 over the abyss background */}
      <div
        className="absolute inset-0"
        style={{
          background: 'linear-gradient(180deg, var(--abyss) 0%, var(--deepc) 34%, var(--glazc) 62%, var(--seac) 88%, var(--foamc) 100%)',
          opacity: 0.9,
        }}/>

      {/* Drifting waves (soft-light) — "glaz sea" ambiance */}
      <svg
        className="splash-waves absolute inset-0 w-full h-full"
        viewBox="0 0 1200 750" preserveAspectRatio="none" aria-hidden="true"
        style={{ mixBlendMode: 'soft-light', pointerEvents: 'none' }}>
        <path className="sw1" fill="#ffffff" fillOpacity="0.05" d="M-400,300 Q-300,274 -200,300 Q-100,326 0,300 Q100,274 200,300 Q300,326 400,300 Q500,274 600,300 Q700,326 800,300 Q900,274 1000,300 Q1100,326 1200,300 Q1300,274 1400,300 Q1500,326 1600,300 Q1700,274 1800,300 Q1900,326 2000,300 Q2100,274 2200,300 Q2300,326 2400,300 L2400,750 L-400,750 Z"/>
        <path className="sw2" fill="#ffffff" fillOpacity="0.06" d="M-400,440 Q-300,418 -200,440 Q-100,462 0,440 Q100,418 200,440 Q300,462 400,440 Q500,418 600,440 Q700,462 800,440 Q900,418 1000,440 Q1100,462 1200,440 Q1300,418 1400,440 Q1500,462 1600,440 Q1700,418 1800,440 Q1900,462 2000,440 Q2100,418 2200,440 Q2300,462 2400,440 L2400,750 L-400,750 Z"/>
        <path className="sw3" fill="#dff0ea" fillOpacity="0.07" d="M-400,575 Q-300,545 -200,575 Q-100,605 0,575 Q100,545 200,575 Q300,605 400,575 Q500,545 600,575 Q700,605 800,575 Q900,545 1000,575 Q1100,605 1200,575 Q1300,545 1400,575 Q1500,605 1600,575 Q1700,545 1800,575 Q1900,605 2000,575 Q2100,545 2200,575 Q2300,605 2400,575 L2400,750 L-400,750 Z"/>
      </svg>

      {/* Fine grain */}
      <div
        className="absolute inset-0"
        style={{
          opacity: 0.16, mixBlendMode: 'overlay',
          backgroundImage: 'radial-gradient(rgba(255,255,255,0.7) 0.5px, transparent 0.6px)',
          backgroundSize: '5px 5px',
        }}/>

      {/* Centered scene */}
      <div className="relative text-center">
        {/* Monogram gradient (vertical glaz) */}
        <svg width="0" height="0" className="absolute" aria-hidden="true">
          <defs>
            <linearGradient id="splashGlazV" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="oklch(40% 0.07 222)"/>
              <stop offset="0.55" stopColor="oklch(57% 0.082 197)"/>
              <stop offset="1" stopColor="oklch(72% 0.066 182)"/>
            </linearGradient>
          </defs>
        </svg>

        {/* Z monogram (lands last, slight bounce) */}
        <div className="splash-mark flex justify-center mb-5">
          <svg
            width="66" height="66" viewBox="0 0 100 100" aria-hidden="true"
            style={{
              // Detaches the glaz tile from the glaz background (same color
              // family): dark halo all around (offset 0) + depth shadow.
              filter: 'drop-shadow(0 0 8px rgba(6,17,26,0.55)) drop-shadow(0 7px 17px rgba(6,17,26,0.5))',
            }}>
            <rect width="100" height="100" rx="24" fill="url(#splashGlazV)"/>
            <path d="M26 28 H74 V39 H48 L74 61 V72 H26 V61 H52 L26 39 Z" fill="#fff"/>
          </svg>
        </div>

        {/* freeglaZ wordmark (capital Z) — "comes into focus" */}
        <div
          className="splash-word"
          style={{ fontFamily: 'var(--serif)', fontWeight: 600, letterSpacing: '-0.035em', fontSize: '54px', lineHeight: 1, color: '#fff' }}>
          <span style={{ fontWeight: 300 }}>free</span>gla<span style={{ fontWeight: 700 }}>Z</span>
        </div>

        {/* Tag */}
        <div
          className="splash-tag"
          style={{ fontFamily: 'var(--mono)', fontSize: '12px', letterSpacing: '0.14em', color: 'rgba(255,255,255,0.75)', marginTop: '16px', textTransform: 'uppercase' }}>
          {t('common.splash_tagline')}
        </div>
      </div>
    </div>
  );
}
