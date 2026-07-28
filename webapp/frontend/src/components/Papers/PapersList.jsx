import { useEffect, useMemo } from 'react';
import { ChevronRight, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PaperRow from './PaperRow.jsx';
import CategoryAccordion from './CategoryAccordion.jsx';
import { useExpandedSections } from '../../hooks/useExpandedSections.js';

/**
 * Papers list orchestration (P1.C — spec §2).
 *
 * Top-to-bottom hierarchy, each collapsible with localStorage
 * persistence:
 *  1. Favorites section (visible if >= 1 favorite) — alphabetical
 *  2. Custom papers section (always visible) — configurable sort
 *  3. HP factory papers section — accordion by native Z9 category
 *
 * Sidebar filters and sort apply to Favorites + Customs.
 * Not applied to HP factory (always alpha within category).
 *
 * @param {object} p
 * @param {Array} p.papers — full list from usePapers
 * @param {(papers: Paper[]) => Paper[]} p.filterAndSort — selector from
 *   the usePaperFilters hook applied to Favorites + Customs
 * @param {boolean} p.factoryDefaultOpen — expand HP factory on mount
 *   (useful for empty state that invites browsing). Overrides
 *   localStorage persistence for this session.
 */
export default function PapersList({
  papers,
  filterAndSort,
  selectedMediaid,
  onSelect,
  onToggleFavorite,
  onCreateCustom,
  factoryDefaultOpen = false,
}) {
  const { t } = useTranslation();
  // Section sorting
  const { favorites, customs, factoryByCategory } = useMemo(() => {
    const all = papers || [];
    const fav = filterAndSort(all.filter((p) => p.favorite));
    const cust = filterAndSort(all.filter((p) => p.custom));
    // Factory: no filter, just grouping by category + alpha sort
    const factory = all.filter((p) => p.factory);
    const byCat = {};
    for (const p of factory) {
      const cat = p.category || 'Autres';
      (byCat[cat] = byCat[cat] || []).push(p);
    }
    for (const cat of Object.keys(byCat)) {
      byCat[cat].sort((a, b) =>
        (a.name || '').localeCompare(b.name || '', 'fr', { sensitivity: 'base' })
      );
    }
    return { favorites: fav, customs: cust, factoryByCategory: byCat };
  }, [papers, filterAndSort]);

  const { expanded, toggle, setExpanded } = useExpandedSections();

  // ``factoryDefaultOpen`` (empty state → click "Browse HP factory")
  // overrides persistence for this session.
  useEffect(() => {
    if (factoryDefaultOpen) setExpanded('factory', true);
  }, [factoryDefaultOpen, setExpanded]);

  const totalFactory = Object.values(factoryByCategory)
    .reduce((sum, arr) => sum + arr.length, 0);
  const sortedCategories = Object.keys(factoryByCategory).sort();

  return (
    <div className="flex-1 overflow-y-auto no-scrollbar">
      {/* Favorites section (visible if not empty) */}
      {favorites.length > 0 && (
        <CollapsibleSection
          title={t('papers.section_favorites')}
          count={favorites.length}
          expanded={expanded.favorites}
          onToggle={() => toggle('favorites')}>
          {favorites.map((p) => (
            <PaperRow
              key={`fav-${p.mediaid}`}
              paper={p}
              showTypeBadge
              selected={selectedMediaid === p.mediaid}
              onSelect={onSelect}
              onToggleFavorite={onToggleFavorite}/>
          ))}
        </CollapsibleSection>
      )}

      <CollapsibleSection
        title={t('papers.section_custom')}
        count={customs.length}
        expanded={expanded.custom}
        onToggle={() => toggle('custom')}>
        {customs.length === 0 ? (
          <div className="px-[22px] py-4 text-xs2 text-text-faint italic">
            {t('papers.row_no_filtered_match')}
          </div>
        ) : (
          customs.map((p) => (
            <PaperRow
              key={`cust-${p.mediaid}`}
              paper={p}
              selected={selectedMediaid === p.mediaid}
              onSelect={onSelect}
              onToggleFavorite={onToggleFavorite}/>
          ))
        )}
      </CollapsibleSection>

      {/* HP factory section */}
      <CollapsibleSection
        title={t('papers.section_factory')}
        count={totalFactory}
        expanded={expanded.factory}
        onToggle={() => toggle('factory')}
        subtitle={t('papers.section_factory_subtitle')}>
        {sortedCategories.map((cat) => (
          <CategoryAccordion key={cat} title={cat} count={factoryByCategory[cat].length}>
            {factoryByCategory[cat].map((p) => (
              <PaperRow
                key={`fac-${p.mediaid}`}
                paper={p}
                selected={selectedMediaid === p.mediaid}
                onSelect={onSelect}
                onToggleFavorite={onToggleFavorite}/>
            ))}
          </CategoryAccordion>
        ))}
      </CollapsibleSection>
    </div>
  );
}


// ─── Sub-components ───────────────────────────────────────────────────


/**
 * Unified collapsible section (spec §2 + P3).
 *
 * Sticky header: [chevron] Title  count, clickable (toggle). Optional
 * action on the right ("+ Create a custom paper" button for the
 * Custom section). Italic subtitle shown only when collapsed.
 *
 * Structure note: we cannot put an action <button> inside the toggle
 * <button> (invalid HTML). So we use a <div> wrapper with the toggle
 * as a sibling. The toggle keeps its ``aria-expanded`` and stays
 * keyboard-activatable.
 */
function CollapsibleSection({
  title, count, expanded, onToggle, subtitle, action, children,
}) {
  return (
    <section>
      <div className="bg-bg sticky top-0 z-10 flex items-center pr-3 border-b border-border-soft/50">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className="flex-1 px-[22px] pt-3 pb-2 flex items-start gap-2 hover:bg-sunken/30 transition-colors text-left">
          <ChevronRight
            size={12} strokeWidth={2.5}
            className={`mt-1 text-text-muted transition-transform ${expanded ? 'rotate-90' : ''}`}
            aria-hidden="true"/>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-[11px] font-semibold tracking-[0.08em] uppercase text-text-muted">
                {title}
              </h2>
              <span className="text-tiny text-text-faint tabular-nums">
                {count}
              </span>
            </div>
            {subtitle && !expanded && (
              <p className="text-tiny text-text-faint italic mt-0.5">
                {subtitle}
              </p>
            )}
          </div>
        </button>
        {action}
      </div>
      {expanded && <div>{children}</div>}
    </section>
  );
}
