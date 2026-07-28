import { useEffect, useRef } from 'react';
import { Star, X, Disc, Sparkles, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import IdentityZone from './detail/IdentityZone.jsx';
import IccZone from './detail/IccZone.jsx';
import CalibrationZone from './detail/CalibrationZone.jsx';
import MechanicalPropertiesSection from './detail/MechanicalPropertiesSection.jsx';
import NotesZone from './detail/NotesZone.jsx';
import BottomActions from './detail/BottomActions.jsx';
import Badge from '../ui/Badge.jsx';

/**
 * Paper detail panel (P1.D — spec §5).
 *
 * - Slide-over OVER the list (not push) — fixed 640px
 * - Semi-transparent scrim behind
 * - 250ms ease-out animation on open
 * - Persistence: stays open when the paper changes (parent does not
 *   unmount, content changes with a 150ms fade)
 * - Esc closes (handled by the useDetailPanel hook)
 * - Clicking the scrim closes (P4-2 revision, aligned with JobQueuePanel
 *   — notes are flushed via ``usePaperNotes`` on unmount)
 * - Focus trap: Tab cycles within the panel
 * - Focus restore: on close, focus the row that opened it (hook)
 *
 * @param {object} p
 * @param {boolean} p.open
 * @param {object} p.paper — selected Paper (may be null while the
 *   list resolves mediaid → paper)
 * @param {() => void} p.onClose
 * @param {(paper) => void} p.onToggleFavorite
 * @param {(paper) => void} p.onCreateCustom
 * @param {() => void} p.onIccChanged — refetch papers after ICC action
 * @param {(kind, msg) => void} p.onNotice — success/error toast
 */
export default function PaperDetailPanel({
  open, paper, onClose,
  onToggleFavorite, onCreateCustom, onProfilePaper, onModifyMechanical,
  onIccChanged, onNotice, onDeleted,
  loadedPaperMediaid = null,
  autoInstallSlot = null,
  offline = false,
}) {
  const { t } = useTranslation();
  const panelRef = useRef(null);

  // Focus the panel itself on open (avoids accidentally triggering
  // a button by pressing Space right after opening).
  // The user can then Tab to reach the controls.
  useEffect(() => {
    if (!open || !panelRef.current) return;
    const t = setTimeout(() => {
      panelRef.current?.focus();
    }, 100);  // after the slide animation
    return () => clearTimeout(t);
  }, [open]);

  // Focus trap: Tab cycles within the panel
  const handleTabTrap = (e) => {
    if (e.key !== 'Tab' || !panelRef.current) return;
    const focusables = panelRef.current.querySelectorAll(
      'button:not([disabled]), [tabindex="0"]:not([disabled]), a[href], textarea, input:not([disabled])'
    );
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  if (!open) return null;
  if (!paper) {
    // open=true but paper still resolving — render an empty panel
    // with nothing (transient case, should not last)
    return null;
  }

  return (
    <>
      {/* Semi-transparent scrim — click closes (P4-2 revision, aligned
          with JobQueuePanel). */}
      <div
        className="fixed inset-0 z-40 bg-black/10"
        onClick={onClose}
        aria-hidden="true"/>

      {/* Panel */}
      <aside
        ref={panelRef}
        role="complementary"
        aria-label={t('papers.detail.panel_aria', { name: paper.name })}
        tabIndex={-1}
        onKeyDown={handleTabTrap}
        className="fixed top-12 right-0 bottom-0 z-50 w-[640px] bg-surface border-l border-border-soft shadow-2xl flex flex-col animate-slidein focus:outline-none">
        <Header
          paper={paper}
          onClose={onClose}
          onToggleFavorite={onToggleFavorite}/>
        <div
          key={paper.mediaid}  // key change → internal cross-fade
          className="flex-1 overflow-y-auto no-scrollbar animate-fadein">
          <IdentityZone paper={paper}/>
          <IccZone paper={paper} offline={offline} onChanged={onIccChanged} onNotice={onNotice} onProfileSlot={onProfilePaper} autoInstallSlot={autoInstallSlot}/>
          <CalibrationZone
            paper={paper}
            offline={offline}
            loadedPaperMediaid={loadedPaperMediaid}
            onCalibrationDone={onIccChanged}
            onNotice={onNotice}/>
          <MechanicalPropertiesSection paper={paper} onModify={onModifyMechanical}/>
          <NotesZone paper={paper}/>
          <BottomActions
            paper={paper}
            onDeleted={onDeleted}
            onNotice={onNotice}
            onProfilePaper={onProfilePaper}/>
        </div>
      </aside>
    </>
  );
}


function Header({ paper, onClose, onToggleFavorite }) {
  const { t } = useTranslation();
  return (
    <div className="px-5 py-4 border-b border-border-soft">
      <div className="flex items-start gap-3">
        {/* Favorite star — increased size (18px spec §5) */}
        <button
          type="button"
          role="switch"
          aria-checked={paper.favorite}
          aria-label={paper.favorite ? t('papers.favorite_toggle_off') : t('papers.favorite_toggle_on')}
          onClick={() => onToggleFavorite?.(paper.mediaid)}
          className="flex-shrink-0 p-1 -m-1 rounded hover:bg-sunken transition-colors">
          <Star
            size={18} strokeWidth={2}
            className={paper.favorite
              ? 'fill-icc-warn text-icc-warn'
              : 'text-text-faint hover:text-text-muted'}
            aria-hidden="true"/>
        </button>

        <div className="flex-1 min-w-0">
          {/* Badges */}
          <div className="flex items-center gap-1.5 mb-1 flex-wrap">
            <Badge kind={paper.custom ? 'info' : 'neutral'}>
              {paper.custom ? t('papers.type_badge_custom') : t('papers.type_badge_factory')}
            </Badge>
            {paper.locked && (
              <span title={t('papers.locked_tooltip')}>
                <Badge kind="ok">{t('papers.locked_label')}</Badge>
              </span>
            )}
            {paper.capabilities?.ge && (
              <span title={t('papers.capability.ge_supported')} className="inline-flex">
                <Disc
                  size={11} strokeWidth={2}
                  className="text-accent fill-accent/30"
                  aria-label={t('papers.capability.ge_supported')} role="img"/>
              </span>
            )}
            {paper.capabilities?.max_detail && (
              <span title={t('papers.capability.max_detail_supported')} className="inline-flex">
                <Sparkles
                  size={11} strokeWidth={2}
                  className="text-accent"
                  aria-label={t('papers.capability.max_detail_supported')}
                  role="img"/>
              </span>
            )}
          </div>

          {/* Title */}
          <h2 className="text-[20px] font-semibold tracking-[-0.015em] text-text-strong leading-tight truncate"
              title={paper.name}>
            {paper.name}
          </h2>
          {/* Subtitle */}
          <p className="text-xs2 text-text-muted mt-0.5 truncate">
            {paper.custom && paper.donor_name
              ? `${paper.category} · ${t('papers.row_basé_sur', { donor: paper.donor_name })}`
              : paper.category}
          </p>
        </div>

        {/* Close button */}
        <button
          type="button"
          onClick={onClose}
          aria-label={t('common.close')}
          className="flex-shrink-0 p-1 rounded hover:bg-sunken text-text-muted hover:text-text-strong transition-colors">
          <X size={16} strokeWidth={2} aria-hidden="true"/>
        </button>
      </div>
    </div>
  );
}


/**
 * Primary action at the TOP of the panel — just below the header.
 *
 * "Create a custom based on this paper" button visible on all
 * papers (custom and factory). Donor pre-filled on click. P3
 * — placement revised after live validation feedback (was initially
 * at the bottom of the panel in ``BottomActions``).
 */
function TopPrimaryAction({ paper, onCreateCustom }) {
  const { t } = useTranslation();
  if (!onCreateCustom) return null;
  return (
    <div className="px-5 py-3 border-b border-border-soft bg-sunken/30">
      <button
        type="button"
        onClick={() => onCreateCustom(paper)}
        className="w-full flex items-center justify-center gap-2 py-2 bg-accent hover:bg-accent-press text-white rounded-md text-sm font-semibold transition-colors">
        <Plus size={14} strokeWidth={2.5} aria-hidden="true"/>
        {t('papers.detail.top_action_create_from')}
      </button>
      {paper.factory && (
        <p className="text-tiny text-text-faint text-center mt-1.5 italic">
          {t('papers.detail.top_action_small_format_warning')}
        </p>
      )}
    </div>
  );
}
