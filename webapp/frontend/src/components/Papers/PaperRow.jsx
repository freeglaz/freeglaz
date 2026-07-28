import { Star, Disc, Sparkles, Square, StickyNote } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import Badge from '../ui/Badge.jsx';
import { useLoadedPaper } from '../../hooks/useLoadedPaper.js';

/**
 * A row in the papers list (P1.C — spec §4).
 *
 * Horizontal layout ~58px tall, padding 12/22:
 *  [★]  Paper name                     [Caps]  ● CLC Badge / + Create custom
 *       Subtitle donor / mediaid
 *
 * Spec §4 — key requirements:
 * - Empty star = text-faint, filled = #E8A04A (icc-warn)
 * - Name: text-[13.5px] font-medium tracking-[-0.005em] truncated
 * - CUSTOM/HP FACTORY badge ONLY in the Favorites section
 *   (passed via the ``showTypeBadge`` prop by PapersList)
 * - 📝 notes icon if has_notes (subtle — explicit tooltip)
 * - Donor in subtitle for customs, "MEDIAID · finish" for factory
 * - Discreet capability indicators (GE accent if supported, otherwise
 *   absent — see user brief spec "capabilities absent if not supported")
 * - CLC badge custom only, "+ Create custom" button factory
 * - Selected: bg-accent/8 + border-left 3px accent
 */
export default function PaperRow({
  paper,
  selected = false,
  showTypeBadge = false,
  onSelect,
  onToggleFavorite,
  onCreateCustom,
}) {
  const { t } = useTranslation();
  const isCustom = paper.custom;
  const isFactory = paper.factory;
  // SINGLE source of "loaded paper" (= status.paper via context, App.jsx),
  // the same as the inspector badge — no second path.
  const loadedPaper = useLoadedPaper();
  const isLoaded = !!loadedPaper && loadedPaper.mediaid === paper.mediaid;

  return (
    <article
      tabIndex={0}
      role="button"
      aria-pressed={selected}
      onClick={(e) => onSelect?.(paper, e)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect?.(paper, e);
        }
      }}
      className={`
        relative flex items-center gap-3 px-[22px] py-3 cursor-pointer
        border-b border-border-soft/50
        transition-colors
        focus-visible:bg-sunken/50
        ${selected ? 'bg-accent-soft' : 'hover:bg-sunken/50'}
      `}>
      {/* Left accent border 3px if selected (spec DS) */}
      {selected && (
        <div
          aria-hidden="true"
          className="absolute left-0 top-0 bottom-0 w-[3px] bg-accent"/>
      )}

      {/* Favorite star — toggle, role=switch (a11y spec §11) */}
      <button
        type="button"
        role="switch"
        aria-checked={paper.favorite}
        aria-label={paper.favorite ? t('papers.favorite_toggle_off') : t('papers.favorite_toggle_on')}
        onClick={(e) => {
          e.stopPropagation();
          onToggleFavorite?.(paper.mediaid);
        }}
        className="flex-shrink-0 p-1 -m-1 rounded hover:bg-sunken transition-colors">
        <Star
          size={14} strokeWidth={2}
          className={
            paper.favorite
              ? 'fill-warn text-warn'
              : 'text-text-faint hover:text-text-muted'
          }
          aria-hidden="true"/>
      </button>

      {/* Main content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          {showTypeBadge && (
            <Badge kind={isCustom ? 'info' : 'neutral'}>
              {isCustom ? t('papers.type_badge_custom') : t('papers.type_badge_factory')}
            </Badge>
          )}
          {isLoaded && (
            // Same badge as the inspector (PaperDetailPanel): label + style reused.
            <span title={t('papers.locked_tooltip')} className="inline-flex flex-shrink-0">
              <Badge kind="ok">{t('papers.locked_label')}</Badge>
            </span>
          )}
          <span
            className="text-[13.5px] font-medium text-text-strong tracking-[-0.005em] truncate"
            title={paper.name}>
            {paper.name || <span className="italic text-text-faint">{t('papers.row_unnamed')}</span>}
          </span>
          {paper.has_notes && (
            // Wrapper <span title> like the capability icons (a `title` on a
            // lucide SVG does not trigger a reliable tooltip).
            <span title={t('papers.has_notes')} className="inline-flex flex-shrink-0">
              <StickyNote
                size={11} strokeWidth={2}
                className="text-text-muted"
                aria-label={t('papers.has_notes')}
                role="img"/>
            </span>
          )}
        </div>
        <div className="text-xs2 text-text-muted truncate mt-0.5">
          {isCustom && paper.donor_name && (
            <span>{t('papers.row_basé_sur', { donor: paper.donor_name })}</span>
          )}
          {isFactory && (
            <span className="font-mono text-text-faint">
              {paper.mediaid}{paper.finish !== 'other' && ` · ${_finishShort(paper.finish, t)}`}
            </span>
          )}
        </div>
      </div>

      {/* Capability indicators — only if supported, single accent color
          to signal "supported" unambiguously.
          Tooltip via <span title> (a `title` on a lucide-react SVG does
          not trigger a reliable native tooltip). */}
      <div className="flex items-center gap-1.5 flex-shrink-0">
        {paper.capabilities?.ge && (
          <span title={t('papers.capability.ge_supported')} className="inline-flex">
            <Disc
              size={13} strokeWidth={2}
              className="text-accent fill-accent/30"
              aria-label={t('papers.capability.ge_supported')}
              role="img"/>
          </span>
        )}
        {paper.capabilities?.max_detail && (
          <span title={t('papers.capability.max_detail_supported')} className="inline-flex">
            <Sparkles
              size={13} strokeWidth={2}
              className="text-accent"
              aria-label={t('papers.capability.max_detail_supported')}
              role="img"/>
          </span>
        )}
        {paper.capabilities?.borderless && (
          <span title={t('papers.capability.borderless_supported')} className="inline-flex">
            <Square
              size={13} strokeWidth={2}
              className="text-accent"
              strokeDasharray="3 2"
              aria-label={t('papers.capability.borderless_supported')}
              role="img"/>
          </span>
        )}
      </div>

      {/* Right: CLC badge (custom only).
          P3: removed the per-row "+ Create custom" button on the
          HP factory ones — it cluttered the list and risked accidental
          clicks. Creation via the global Custom section header button
          + secondary button in the detail panel (Zone 6). */}
      {isCustom && (
        <ClcPill
          status={paper.clc?.status}
          date={paper.clc?.date}/>
      )}
    </article>
  );
}


function ClcPill({ status, date }) {
  const { t } = useTranslation();
  // CLC status → DS Badge kind (mapping/labels unchanged): valid=ok,
  // stale/pending=warn (amber=caution, the label distinguishes), none=neutral.
  const kind = { valid: 'ok', stale: 'warn', pending: 'warn', never: 'neutral' }[status] || 'neutral';
  const label = t(`papers.clc_short.${status}`, { defaultValue: '—' });
  const titleByStatus = {
    valid:   date ? t('papers.clc_tooltip.valid', { date }) : t('papers.clc_tooltip.valid_no_date'),
    stale:   date ? t('papers.clc_tooltip.stale', { date }) : t('papers.clc_tooltip.stale_no_date'),
    pending: t('papers.clc_tooltip.pending'),
    never:   t('papers.clc_tooltip.never'),
  };

  return (
    <span className="flex-shrink-0" title={titleByStatus[status] || titleByStatus.never}>
      <Badge kind={kind}>{label}</Badge>
    </span>
  );
}


function _finishShort(f, t) {
  const key = {
    gloss: 'papers.finish.gloss_short',
    matte: 'papers.finish.matte_short',
    canvas: 'papers.finish.canvas_short',
    film: 'papers.finish.film_short',
  }[f];
  return key ? t(key) : '';
}
