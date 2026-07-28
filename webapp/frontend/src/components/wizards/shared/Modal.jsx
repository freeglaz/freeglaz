import { useEffect } from 'react';

// widthClass: container width (Tailwind). Default 640px (legacy, shared
// by the other wizards). Always combined with max-w-full → never overflows.
export default function Modal({ open, onClose, children, ariaLabel, widthClass = 'w-[640px]' }) {
  useEffect(() => {
    if (!open) return;
    const h = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose?.();
      }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[55] bg-black/[0.42] backdrop-blur-[2px] flex items-center justify-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}>
      <div className={`bg-bg border border-border-soft rounded-[14px] shadow-2xl ${widthClass} max-w-full max-h-full overflow-hidden flex flex-col`}>
        {children}
      </div>
    </div>
  );
}
