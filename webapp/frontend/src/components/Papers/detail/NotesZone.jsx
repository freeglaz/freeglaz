import { Edit2, Eye, Check } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { usePaperNotes } from '../../../hooks/usePaperNotes.js';
import MarkdownPreview from '../MarkdownPreview.jsx';

/**
 * Zone 5 — Editable notes (spec §5 Zone 5).
 *
 * The only truly interactive thing in P1.
 * - Toggle Edit / Preview (2-option segmented control)
 * - <textarea> mono min-height 180px in edit mode
 * - MarkdownPreview in preview mode
 * - Auto-save debounce 1.2s (usePaperNotes hook)
 * - Visual indicator while typing / after save
 */
export default function NotesZone({ paper }) {
  const { t } = useTranslation();
  const [mode, setMode] = useState('edit');
  const { notes, setNotes, status, error } = usePaperNotes(paper.mediaid);

  return (
    <section className="px-5 py-4 border-b border-border-soft">
      <div className="flex items-center justify-between mb-2.5">
        <h3 className="text-[11px] font-semibold tracking-[0.08em] uppercase text-text-muted">
          {t('papers.detail.section_notes')}
        </h3>
        <div className="flex items-center gap-3">
          <SaveIndicator status={status} error={error}/>
          <Segmented
            value={mode}
            onChange={setMode}
            options={[
              { value: 'edit',    label: t('papers.notes.edit_mode'),    icon: Edit2 },
              { value: 'preview', label: t('papers.notes.preview_mode'), icon: Eye },
            ]}/>
        </div>
      </div>

      {mode === 'edit' ? (
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder={t('papers.notes.placeholder')}
          aria-label={t('papers.notes.aria_label', { name: paper.name })}
          aria-describedby="notes-save-indicator"
          className="w-full min-h-[180px] resize-y font-mono text-[12.5px] leading-[1.55] bg-sunken/50 border border-border-soft rounded-md px-3 py-2 text-text-strong placeholder:text-text-faint focus:outline-none focus:border-accent focus:bg-surface transition-colors"/>
      ) : (
        <div className="min-h-[180px] bg-sunken/30 border border-border-soft rounded-md px-3 py-2">
          <MarkdownPreview source={notes}/>
        </div>
      )}
    </section>
  );
}


function Segmented({ value, onChange, options }) {
  return (
    <div className="inline-flex bg-sunken rounded-md p-0.5">
      {options.map((opt) => {
        const Icon = opt.icon;
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            aria-pressed={active}
            className={`
              flex items-center gap-1 px-2 py-1 rounded text-tiny font-medium transition-colors
              ${active
                ? 'bg-surface text-text-strong shadow-sm'
                : 'text-text-muted hover:text-text-strong'}
            `}>
            <Icon size={10} strokeWidth={2.5} aria-hidden="true"/>
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}


function SaveIndicator({ status, error }) {
  const { t } = useTranslation();
  if (status === 'pending') {
    return (
      <span id="notes-save-indicator"
        className="flex items-center gap-1 text-tiny text-icc-warn-deep">
        <span className="w-1.5 h-1.5 rounded-full bg-icc-warn animate-pulse"/>
        {t('papers.notes.saving')}
      </span>
    );
  }
  if (status === 'saved') {
    return (
      <span id="notes-save-indicator"
        className="flex items-center gap-1 text-tiny text-icc-ok">
        <Check size={10} strokeWidth={2.5} aria-hidden="true"/>
        {t('papers.notes.saved')}
      </span>
    );
  }
  if (status === 'error') {
    return (
      <span id="notes-save-indicator"
        className="text-tiny text-danger"
        title={error || ''}>
        {t('papers.notes.save_error')}
      </span>
    );
  }
  return <span id="notes-save-indicator" className="sr-only">{t('papers.notes.screen_reader_ready')}</span>;
}
