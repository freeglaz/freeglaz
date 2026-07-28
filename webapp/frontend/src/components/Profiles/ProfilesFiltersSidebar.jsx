import { useTranslation } from 'react-i18next';
import {
  Search, Lock, FolderOpen, Printer, Monitor, Palette, Sparkles,
} from 'lucide-react';

/**
 * Filters sidebar for the Profiles screen (17.2). 260px, surface background.
 * Papers P1 model, adapted to the 2 store zones.
 */
export default function ProfilesFiltersSidebar({
  filterZone, setFilterZone,
  filterCategory, setFilterCategory,
  filterDevice, setFilterDevice,
  repoDevices,
  search, setSearch,
  allTags = [], filterTags = [], onToggleTag, filterTagMode, setFilterTagMode,
  store, z9,
}) {
  const { t } = useTranslation();
  const counts = _computeCounts(store, z9);

  return (
    <aside className="w-[260px] flex-shrink-0 bg-surface border-r border-border-soft
                      overflow-y-auto p-3">
      {/* Search */}
      <div className="relative mb-4">
        <Search size={12}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-faint"/>
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('profils.filter_search_placeholder')}
          className="w-full pl-7 pr-2 py-1.5 text-xs2 bg-bg border border-border-soft
                     rounded-md text-text-strong placeholder-text-faint
                     focus:outline-none focus:border-accent"/>
      </div>

      {/* Zone */}
      <FilterSection title={t('profils.filter_zone')}>
        <RadioRow
          label={t('profils.zone_all')} icon={null}
          count={counts.total}
          checked={filterZone === 'all'} onClick={() => setFilterZone('all')}/>
        <RadioRow
          label={t('profils.zone_mirror')} icon={Lock}
          count={counts.mirror}
          checked={filterZone === 'mirror'} onClick={() => setFilterZone('mirror')}/>
        <RadioRow
          label={t('profils.zone_repo')} icon={FolderOpen}
          count={counts.repo}
          checked={filterZone === 'repo'} onClick={() => setFilterZone('repo')}/>
      </FilterSection>

      {/* Repo category (only if zone = repo or all) */}
      {filterZone !== 'mirror' && (
        <FilterSection title={t('profils.filter_category')}>
          <RadioRow
            label={t('profils.category_all')}
            count={counts.repo}
            checked={filterCategory === 'all'} onClick={() => setFilterCategory('all')}/>
          <RadioRow
            label={t('profils.category_printers')} icon={Printer}
            count={counts.printers}
            checked={filterCategory === 'printers'} onClick={() => setFilterCategory('printers')}/>
          <RadioRow
            label={t('profils.category_displays')} icon={Monitor}
            count={counts.displays}
            checked={filterCategory === 'displays'} onClick={() => setFilterCategory('displays')}/>
          <RadioRow
            label={t('profils.category_workingspaces')} icon={Palette}
            count={counts.workingspaces}
            checked={filterCategory === 'workingspaces'} onClick={() => setFilterCategory('workingspaces')}/>
          <RadioRow
            label={t('profils.category_z9')} icon={Sparkles}
            count={counts.z9}
            checked={filterCategory === 'z9'} onClick={() => setFilterCategory('z9')}/>
        </FilterSection>
      )}

      {/* Device (printers) */}
      {filterZone !== 'mirror'
       && (filterCategory === 'all' || filterCategory === 'printers')
       && repoDevices.length > 0 && (
        <FilterSection title={t('profils.filter_device')}>
          <RadioRow
            label={t('profils.device_all')}
            checked={filterDevice === 'all'} onClick={() => setFilterDevice('all')}/>
          {repoDevices.map((d) => (
            <RadioRow
              key={d} label={d}
              checked={filterDevice === d} onClick={() => setFilterDevice(d)}/>
          ))}
        </FilterSection>
      )}

      {/* Classification tags (repo_z9). Exclusive AND/OR selector ALWAYS visible above
          ("and" by default, a single active option), then multi-selection of tags (toggle).
          The mode only has a visible effect from 2 tags on (at 0-1, AND == OR). */}
      {filterZone !== 'mirror' && allTags.length > 0 && (
        <FilterSection title={t('profils.filter_tags_title')}>
          <div className="inline-flex rounded-md border border-border-soft overflow-hidden text-tiny mb-1.5 ml-1">
            <button type="button" onClick={() => setFilterTagMode('and')}
                    aria-pressed={filterTagMode === 'and'}
                    className={`px-2.5 py-0.5 transition-colors ${
                      filterTagMode === 'and' ? 'bg-accent text-on-accent font-medium'
                                              : 'text-text-muted hover:bg-sunken'}`}>
              {t('profils.filter_tags_mode_and')}
            </button>
            <button type="button" onClick={() => setFilterTagMode('or')}
                    aria-pressed={filterTagMode === 'or'}
                    className={`px-2.5 py-0.5 border-l border-border-soft transition-colors ${
                      filterTagMode === 'or' ? 'bg-accent text-on-accent font-medium'
                                             : 'text-text-muted hover:bg-sunken'}`}>
              {t('profils.filter_tags_mode_or')}
            </button>
          </div>
          <div className="flex flex-wrap gap-1 px-1">
            {allTags.map((tg) => {
              const on = filterTags.includes(tg);
              return (
                <button type="button" key={tg} onClick={() => onToggleTag(tg)} aria-pressed={on}
                        className={`px-2 py-0.5 rounded-full text-xs2 border transition-colors ${
                          on ? 'bg-accent text-on-accent border-accent font-medium'
                             : 'text-text-muted border-border-soft hover:bg-sunken'}`}>
                  {tg}
                </button>
              );
            })}
          </div>
        </FilterSection>
      )}
    </aside>
  );
}


function FilterSection({ title, children }) {
  return (
    <div className="mb-4">
      <div className="text-tiny text-text-faint uppercase tracking-wider mb-1.5 px-1">
        {title}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}


function RadioRow({ label, icon: Icon, count, checked, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full flex items-center gap-2 px-2 py-1 rounded text-xs2
                  text-left transition-colors ${
                    checked
                      ? 'bg-accent/10 text-accent font-medium'
                      : 'text-text-muted hover:text-text-strong hover:bg-sunken'
                  }`}>
      {Icon && <Icon size={12} className="shrink-0"/>}
      <span className="flex-1 truncate" title={typeof label === 'string' ? label : undefined}>{label}</span>
      {count != null && (
        <span className="text-tiny text-text-faint font-mono">{count}</span>
      )}
    </button>
  );
}


function _computeCounts(store, z9) {
  if (!store) {
    return { total: 0, mirror: 0, repo: 0,
             printers: 0, displays: 0, workingspaces: 0, z9: 0 };
  }
  const mirror = (store.mirrors || []).reduce(
    (acc, m) => acc + (m.papers || []).reduce(
      (a, p) => a + (p.profiles?.length || 0), 0,
    ), 0,
  );
  const printers = (store.repo?.printers || []).length;
  const displays = (store.repo?.displays || []).length;
  const workingspaces = (store.repo?.workingspaces || []).length;
  const z9Count = (z9?.serials || []).reduce(
    (acc, s) => acc + (s.papers || []).reduce(
      (a, p) => a + (p.profiles?.length || 0), 0,
    ), 0,
  );
  const repo = printers + displays + workingspaces + z9Count;
  return {
    total: mirror + repo,
    mirror, repo, printers, displays, workingspaces, z9: z9Count,
  };
}
