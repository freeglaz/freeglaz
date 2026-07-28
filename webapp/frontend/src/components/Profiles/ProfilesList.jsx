import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { geLabel } from '../../lib/geLabel.js';
import {
  Lock, ChevronRight, ChevronDown, Printer, Monitor, Palette, FolderOpen,
  Layers, Plus, Check, Sparkles,
} from 'lucide-react';

// 17.2.1 — C2: profile taxonomy (8 categories).
// 17.2.2: "Custom Paper" placed FIRST (the most useful category for
// profiling), followed by the 7 HP factory categories in their standard
// HP order. Any unknown label falls between the two in alphabetical
// order.
const CUSTOM_CATEGORY = 'Custom Paper';
const HP_FACTORY_CATEGORY_ORDER = [
  'Photo Paper',
  'Fine Art Material',
  'Bond and Coated Paper',
  'Film',
  'Backlit Material',
  'Self-Adhesive material',
  'Banner & Sign material',
];

function _orderedCategories(byCategory) {
  const out = [];
  if (CUSTOM_CATEGORY in byCategory) out.push(CUSTOM_CATEGORY);
  const knownFactory = HP_FACTORY_CATEGORY_ORDER.filter((c) => c in byCategory);
  const unknown = Object.keys(byCategory)
    .filter((c) => c !== CUSTOM_CATEGORY && !HP_FACTORY_CATEGORY_ORDER.includes(c))
    .sort((a, b) => a.localeCompare(b));
  out.push(...knownFactory, ...unknown);
  return out;
}

/**
 * List with collapsible sections (17.2).
 *
 * Sections:
 *  - Z9 mirror — <serial>   (read-only, lock)
 *    └─ grouped by paper → slot-keyed profiles
 *  - Personal repo → Printers / Displays / Working spaces
 *
 * Picker mode (profile-compare commit 4) — if `pickerMode=true`, each
 * profile shows a "+ Add" button on the right (or an "Already added"
 * chip if its absolute_path is in pickedPaths). The global row click is
 * disabled in picker mode so the DetailPanel doesn't open. Lets us reuse
 * THIS presentation in the comparison modal without reinventing a mini-tree.
 */
export default function ProfilesList({
  filtered, selected, onSelect, isEmptyMirror,
  // Profile-compare commit 4 — picker props (all optional).
  pickerMode = false,
  pickedPaths = null,
  onPick = null,
  canPick = true,
}) {
  const { t } = useTranslation();
  const pickerProps = pickerMode
    ? { pickerMode: true, pickedPaths, onPick, canPick }
    : {};
  return (
    <div className="px-4 py-3 space-y-4">
      {/* Z9 mirror(s) */}
      {(filtered.mirrors || []).map((m) => (
        <MirrorSection
          key={m.serial}
          mirror={m}
          selected={selected}
          onSelect={onSelect}
          {...pickerProps}/>
      ))}

      {isEmptyMirror && (
        <div className="p-4 rounded-md border border-dashed border-border-soft
                        text-text-faint text-xs2 text-center">
          {t('profils.empty_mirror_hint')}
        </div>
      )}

      {/* Personal Z9 space (refined profiles, grouped by paper) */}
      {(filtered.z9 || []).map((s) => (
        <Z9Section
          key={s.serial}
          z9Serial={s}
          selected={selected}
          onSelect={onSelect}
          {...pickerProps}/>
      ))}

      {/* Repo */}
      <RepoCategorySection
        title={t('profils.section_repo_printers')}
        icon={Printer}
        items={filtered.repo?.printers || []}
        selected={selected}
        onSelect={onSelect}
        groupByDevice
        {...pickerProps}/>
      <RepoCategorySection
        title={t('profils.section_repo_displays')}
        icon={Monitor}
        items={filtered.repo?.displays || []}
        selected={selected}
        onSelect={onSelect}
        {...pickerProps}/>
      <RepoCategorySection
        title={t('profils.section_repo_workingspaces')}
        icon={Palette}
        items={filtered.repo?.workingspaces || []}
        selected={selected}
        onSelect={onSelect}
        {...pickerProps}/>
    </div>
  );
}


function MirrorSection({
  mirror, selected, onSelect,
  pickerMode = false, pickedPaths = null, onPick = null, canPick = true,
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);

  // 17.2.1 — C2: grouping by `category_name` (present on each paper from
  // the backend, source = getMediumList). No
  // derivation, strict identity with the firmware grouping.
  const byCategory = useMemo(() => {
    const groups = {};
    for (const p of (mirror.papers || [])) {
      const cat = p.category_name || 'Custom Paper';
      (groups[cat] = groups[cat] || []).push(p);
    }
    return groups;
  }, [mirror]);
  const ordered = useMemo(() => _orderedCategories(byCategory), [byCategory]);
  const totalProfiles = (mirror.papers || []).reduce(
    (a, p) => a + (p.profiles?.length || 0), 0,
  );

  return (
    <section className="bg-surface border border-border-soft rounded-lg overflow-hidden">
      <SectionHeader
        title={t('profils.section_mirror', { serial: mirror.serial })}
        icon={Lock}
        readonly
        count={totalProfiles}
        open={open}
        onToggle={() => setOpen((v) => !v)}/>
      {open && (
        <div className="divide-y divide-border-soft">
          {ordered.map((cat) => (
            <CategoryGroup
              key={cat}
              category={cat}
              papers={byCategory[cat]}
              serial={mirror.serial}
              selected={selected}
              onSelect={onSelect}
              pickerMode={pickerMode}
              pickedPaths={pickedPaths}
              onPick={onPick}
              canPick={canPick}/>
          ))}
        </div>
      )}
    </section>
  );
}


function Z9Section({
  z9Serial, selected, onSelect,
  pickerMode = false, pickedPaths = null, onPick = null, canPick = true,
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  const papers = z9Serial.papers || [];
  const totalProfiles = papers.reduce(
    (a, p) => a + (p.profiles?.length || 0), 0,
  );
  return (
    <section className="bg-surface border border-border-soft rounded-lg overflow-hidden">
      <SectionHeader
        title={t('profils.section_z9_perso', { serial: z9Serial.serial })}
        icon={Sparkles}
        readonly={false}
        count={totalProfiles}
        open={open}
        onToggle={() => setOpen((v) => !v)}/>
      {open && (
        <div className="divide-y divide-border-soft">
          {papers.map((p) => (
            <Z9PaperGroup
              key={p.media_id}
              paper={p}
              serial={z9Serial.serial}
              selected={selected}
              onSelect={onSelect}
              pickerMode={pickerMode}
              pickedPaths={pickedPaths}
              onPick={onPick}
              canPick={canPick}/>
          ))}
        </div>
      )}
    </section>
  );
}


function Z9PaperGroup({
  paper, serial, selected, onSelect,
  pickerMode = false, pickedPaths = null, onPick = null, canPick = true,
}) {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs2
                   text-text-strong hover:bg-sunken/40 transition-colors text-left">
        {open ? <ChevronDown size={11}/> : <ChevronRight size={11}/>}
        <span className="font-medium flex-1 truncate" title={paper.paper_name}>{paper.paper_name}</span>
        <span className="text-tiny text-text-faint font-mono">
          {paper.profiles.length}
        </span>
      </button>
      {open && (
        <div className="pl-7 pr-2 pb-1.5 space-y-1">
          {/* GE sub-groups (ON then OFF — same order as the Measures expander); same
              labels/style; sorted by descending FS mtime (most recent first; robust). */}
          {[{ key: 'on', ge: 'FULLPAGE' }, { key: 'off', ge: 'OFF' }]
            .map((sg) => ({
              ...sg,
              rows: paper.profiles
                .filter((p) => (sg.ge === 'FULLPAGE' ? p.gloss_slot === 'FULLPAGE' : p.gloss_slot !== 'FULLPAGE'))
                .sort((a, b) => (b.mtime || 0) - (a.mtime || 0)),
            }))
            .filter((sg) => sg.rows.length > 0)
            .map((sg) => (
              <div key={sg.key} className="space-y-0.5">
                <div className="px-2 pt-1 text-tiny font-semibold uppercase tracking-wide text-text-faint">{geLabel(sg.ge)}</div>
                {sg.rows.map((prof) => (
                  <ProfileRow
                    key={prof.filename}
                    profile={prof}
                    zone="z9"
                    serial={serial}
                    mediaId={paper.media_id}
                    paperName={paper.paper_name}
                    selected={selected}
                    onSelect={onSelect}
                    pickerMode={pickerMode}
                    pickedPaths={pickedPaths}
                    onPick={onPick}
                    canPick={canPick}/>
                ))}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}


function CategoryGroup({
  category, papers, serial, selected, onSelect,
  pickerMode = false, pickedPaths = null, onPick = null, canPick = true,
}) {
  // 17.2.2 — initial state: all mirror sections start CLOSED, except
  // "Custom Paper" (the most useful for profiling) which starts OPEN.
  // User toggle is then free.
  const isCustom = category === CUSTOM_CATEGORY;
  const [open, setOpen] = useState(isCustom);
  const totalProfiles = (papers || []).reduce(
    (a, p) => a + (p.profiles?.length || 0), 0,
  );

  // Visual emphasis for Custom Paper (17.2.2) — accent color on the
  // label + accent border on the left, immediately noticeable to the eye
  // without being garish. Tokens only.
  const headerCls = isCustom
    ? 'w-full flex items-center gap-2 px-3 py-2 text-xs2 text-left '
      + 'bg-accent/5 hover:bg-accent/10 border-l-2 border-accent '
      + 'transition-colors'
    : 'w-full flex items-center gap-2 px-3 py-1.5 text-xs2 text-left '
      + 'text-text-strong hover:bg-sunken/40 transition-colors';
  const labelCls = isCustom
    ? 'flex-1 truncate uppercase tracking-wider text-tiny '
      + 'font-bold text-accent'
    : 'flex-1 truncate uppercase tracking-wider text-tiny '
      + 'font-medium text-text-strong';
  const iconCls = isCustom ? 'text-accent shrink-0' : 'text-text-muted shrink-0';
  const childrenWrapCls = isCustom
    ? 'border-l-2 border-accent/40 ml-3'
    : 'border-l-2 border-border-soft ml-3';

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={headerCls}>
        {open ? <ChevronDown size={11}/> : <ChevronRight size={11}/>}
        <Layers size={11} className={iconCls}/>
        <span className={labelCls}>
          {category}
        </span>
        <span className={`text-tiny font-mono ${
          isCustom ? 'text-accent/80' : 'text-text-faint'
        }`}>
          {(papers || []).length} · {totalProfiles}
        </span>
      </button>
      {open && (
        <div className={childrenWrapCls}>
          {(papers || []).map((p) => (
            <PaperGroup
              key={p.slug}
              paper={p}
              serial={serial}
              selected={selected}
              onSelect={onSelect}
              pickerMode={pickerMode}
              pickedPaths={pickedPaths}
              onPick={onPick}
              canPick={canPick}/>
          ))}
        </div>
      )}
    </div>
  );
}


function PaperGroup({
  paper, serial, selected, onSelect,
  pickerMode = false, pickedPaths = null, onPick = null, canPick = true,
}) {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs2
                   text-text-strong hover:bg-sunken/40 transition-colors text-left">
        {open ? <ChevronDown size={11}/> : <ChevronRight size={11}/>}
        <span className="font-medium flex-1 truncate" title={paper.paper_name}>{paper.paper_name}</span>
        <span className="text-tiny text-text-faint font-mono">
          {paper.is_user_custom ? 'custom' : 'factory'} · {paper.profiles.length}
        </span>
      </button>
      {open && (
        <div className="pl-7 pr-2 pb-1.5">
          {paper.profiles.map((prof) => (
            <ProfileRow
              key={prof.filename}
              profile={prof}
              zone="mirror"
              serial={serial}
              paperSlug={paper.slug}
              paperName={paper.paper_name}
              mediaId={paper.paper_id}
              selected={selected}
              onSelect={onSelect}
              pickerMode={pickerMode}
              pickedPaths={pickedPaths}
              onPick={onPick}
              canPick={canPick}/>
          ))}
        </div>
      )}
    </div>
  );
}


function RepoCategorySection({
  title, icon, items, selected, onSelect, groupByDevice = false,
  pickerMode = false, pickedPaths = null, onPick = null, canPick = true,
}) {
  const { t } = useTranslation();
  // 17.2.2 — initial state: personal repo sections start CLOSED
  // (printers/displays/workingspaces). The mirror's Custom Paper is the
  // only section open by default.
  const [open, setOpen] = useState(false);
  if (!items || items.length === 0) return null;

  let body;
  if (groupByDevice) {
    const byDevice = items.reduce((acc, p) => {
      const k = p.device || '—';
      (acc[k] = acc[k] || []).push(p);
      return acc;
    }, {});
    body = Object.entries(byDevice).map(([device, profs]) => (
      <div key={device}>
        <div className="px-3 py-1 text-tiny text-text-faint uppercase tracking-wider">
          {device}
        </div>
        <div className="pb-1.5">
          {profs.map((p) => (
            <ProfileRow
              key={p.absolute_path}
              profile={p}
              zone="repo"
              category="printers"
              device={p.device}
              selected={selected}
              onSelect={onSelect}
              pickerMode={pickerMode}
              pickedPaths={pickedPaths}
              onPick={onPick}
              canPick={canPick}/>
          ))}
        </div>
      </div>
    ));
  } else {
    const cat = title.toLowerCase().includes('display') ? 'displays' : 'workingspaces';
    body = (
      <div className="pb-1.5">
        {items.map((p) => (
          <ProfileRow
            key={p.absolute_path}
            profile={p}
            zone="repo"
            category={cat}
            selected={selected}
            onSelect={onSelect}
            pickerMode={pickerMode}
            pickedPaths={pickedPaths}
            onPick={onPick}
            canPick={canPick}/>
        ))}
      </div>
    );
  }

  return (
    <section className="bg-surface border border-border-soft rounded-lg overflow-hidden">
      <SectionHeader
        title={title}
        icon={icon || FolderOpen}
        readonly={false}
        count={items.length}
        open={open}
        onToggle={() => setOpen((v) => !v)}/>
      {open && body}
    </section>
  );
}


function SectionHeader({ title, icon: Icon, readonly, count, open, onToggle }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="w-full px-3 py-2 flex items-center gap-2 bg-sunken/30
                 hover:bg-sunken/50 transition-colors text-left">
      {open ? <ChevronDown size={12}/> : <ChevronRight size={12}/>}
      <Icon size={13} className={readonly ? 'text-text-muted' : 'text-text-strong'}/>
      <span className="text-xs2 font-medium text-text-strong flex-1 truncate" title={title}>
        {title}
      </span>
      <span className="text-tiny text-text-faint font-mono">{count}</span>
    </button>
  );
}


function ProfileRow({
  profile, zone, serial, paperSlug, paperName, mediaId, category, device,
  selected, onSelect,
  pickerMode = false, pickedPaths = null, onPick = null, canPick = true,
}) {
  const { t } = useTranslation();
  const isSelected = !pickerMode
    && selected?.absolute_path === profile.absolute_path;
  const alreadyPicked = pickerMode
    && pickedPaths
    && pickedPaths.has(profile.absolute_path);
  const handleClick = (e) => {
    if (pickerMode) return;  // no detail panel in picker mode
    onSelect({
      ...profile,
      zone, serial, paperSlug, paperName, mediaId, category, device,
    }, e);
  };
  const sizeKb = profile.size_bytes
    ? Math.round(profile.size_bytes / 1024) + ' KB'
    : '';
  // 17.2.1 — B1: show the faithful z9_icc_name for mirror profiles,
  // otherwise the label/display_name (user-entered) then filename.
  const rowName = zone === 'mirror'
    ? (profile.z9_icc_name || profile.filename)
    : zone === 'z9'
      ? profile.filename                       // the file's REAL identity (unique, future editable name)
      : (profile.display_name || profile.filename);
  const z9Method = zone === 'z9'
    ? [profile.method_flags, profile.n_patches ? `${profile.n_patches}p` : null]
        .filter(Boolean).join(' · ')
    : '';
  // In picker mode: the whole row is a non-clickable `div`; only the
  // "+ Add" button on the right is interactive. In select mode: the whole
  // row is a `button` that selects the profile (DetailPanel).
  const Wrapper = pickerMode ? 'div' : 'button';
  const wrapperProps = pickerMode
    ? {}
    : { type: 'button', onClick: handleClick };
  return (
    <Wrapper
      {...wrapperProps}
      className={`w-full px-2 py-1 text-xs2 text-left rounded
                  flex items-center gap-2 transition-colors ${
                    isSelected
                      ? 'bg-accent/10 text-accent'
                      : pickerMode
                        ? 'text-text-muted'
                        : 'text-text-muted hover:text-text-strong hover:bg-sunken/60'
                  }`}>
      {zone === 'mirror' && (
        <Lock size={10} className="shrink-0 text-text-faint"/>
      )}
      <span className="flex-1 truncate font-mono" title={rowName}>
        {rowName}
      </span>
      {zone === 'mirror' && (
        <span className="text-tiny text-text-faint">
          {geLabel(profile.gloss_enhancer)} · {profile.custom ? 'custom' : 'factory'}
        </span>
      )}
      {zone === 'z9' && (
        <span className="text-tiny text-text-faint">
          {[profile.gloss_slot && geLabel(profile.gloss_slot), z9Method].filter(Boolean).join(' · ')}
        </span>
      )}
      {sizeKb && (
        <span className="text-tiny text-text-faint font-mono">{sizeKb}</span>
      )}
      {pickerMode && (
        alreadyPicked ? (
          <span className="inline-flex items-center gap-1 text-tiny text-text-faint shrink-0">
            <Check size={11}/>
            {t('compare.add_already_in')}
          </span>
        ) : (
          <button
            type="button"
            onClick={() => onPick && onPick(profile.absolute_path)}
            disabled={!canPick}
            className="inline-flex items-center gap-1 px-2 py-0.5 text-tiny
                       bg-accent/10 text-accent hover:bg-accent/20 rounded
                       transition-colors disabled:opacity-40 disabled:cursor-not-allowed shrink-0">
            <Plus size={11}/>
            {t('compare.add_button')}
          </button>
        )
      )}
    </Wrapper>
  );
}
