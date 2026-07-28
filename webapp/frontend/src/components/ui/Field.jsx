import { HelpCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';

// Label + control pair. Wrapper-only, the control is passed as children.
// `help` optional: if present, a small HelpCircle icon is shown to the
// right of the label with a native tooltip (`title`) on hover.
export default function Field({ label, help, dim = false, children }) {
  const { t } = useTranslation();
  return (
    <div className={`mb-3.5 ${dim ? 'opacity-50 pointer-events-none' : ''}`}>
      <div className="flex items-center gap-1 text-xs2 text-text-muted mb-1.5 font-medium">
        <span>{label}</span>
        {help && (
          // Wrap in a <span>: Chrome does not trigger the native tooltip
          // from a `title` attribute placed on an <svg> (the SVG spec expects
          // a child <title> element, non-standard behavior on Chrome).
          // The `title` on an HTML span is universally supported.
          <span
            title={help}
            aria-label={t('common.help_aria', { label })}
            className="inline-flex cursor-help">
            <HelpCircle
              size={11}
              strokeWidth={2}
              className="text-text-muted/70"
              aria-hidden="true"/>
          </span>
        )}
      </div>
      {children}
    </div>
  );
}
