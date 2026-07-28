import { useEffect, useMemo, useRef } from 'react';
import { AlertTriangle, Info } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export default function StepParams({
  paper,
  workflow = 'PRINT_AND_SCAN',
  profileName,
  setProfileName,
  glossEnhancer,
  setGlossEnhancer,
  nameManuallyEdited,
  setNameManuallyEdited,
}) {
  const { t } = useTranslation();
  const inputRef = useRef(null);
  const geSupported = paper?.capabilities?.ge ?? false;
  const geState = glossEnhancer ? 'ON' : 'OFF';

  const suggestedName = useMemo(
    () => _buildSuggestedName(paper?.name, geState),
    [paper?.name, geState],
  );

  useEffect(() => {
    if (!nameManuallyEdited) setProfileName(suggestedName);
  }, [suggestedName, nameManuallyEdited, setProfileName]);

  useEffect(() => {
    setTimeout(() => inputRef.current?.focus(), 100);
  }, []);

  const nameError = _validateName(profileName, t);
  const hasOverwrite = _hasCustomForSlot(paper, glossEnhancer);

  const introKey = {
    PRINT_AND_SCAN: 'wizard_profile_paper.intro_description',
    PRINT_ONLY: 'wizard_profile_paper.intro_print_only',
    SCAN_ONLY: 'wizard_profile_paper.intro_scan_only',
  }[workflow] || 'wizard_profile_paper.intro_description';

  const sheetKey = workflow === 'PRINT_ONLY'
    ? 'wizard_profile_paper.sheet_required_print_only'
    : workflow === 'SCAN_ONLY'
      ? 'wizard_profile_paper.sheet_required_scan_only'
      : null;

  return (
    <div className="space-y-5">
      <p className="text-xs2 text-text-muted leading-relaxed">
        {t(introKey)}
      </p>

      {sheetKey && (
        <div className="flex items-start gap-2.5 bg-accent/[0.06] border border-accent/20 rounded-md p-3">
          <Info size={14} className="text-accent mt-0.5 flex-shrink-0" aria-hidden="true"/>
          <p className="text-xs2 text-text-muted leading-relaxed">{t(sheetKey)}</p>
        </div>
      )}

      {/* Block 1 — Profile name */}
      <div>
        <label htmlFor="profile-name" className="block text-xs font-semibold text-text-strong mb-1.5">
          {t('wizard_profile_paper.param_name_label')}
        </label>
        <input
          ref={inputRef}
          id="profile-name"
          type="text"
          value={profileName}
          maxLength={63}
          onChange={(e) => {
            const sanitized = _sanitizeProfileName(e.target.value);
            setProfileName(sanitized);
            if (!nameManuallyEdited) setNameManuallyEdited(true);
          }}
          className={`w-full px-3 py-2 bg-sunken/40 border rounded-md text-sm text-text-strong placeholder:text-text-faint focus:outline-none focus:bg-surface transition-colors ${
            nameError
              ? 'border-danger focus:border-danger'
              : 'border-border-soft focus:border-accent'
          }`}/>
        {nameError ? (
          <p className="text-tiny text-danger mt-1">{nameError}</p>
        ) : (
          <p className="text-tiny text-text-faint font-mono mt-1">
            {t('wizard_profile_paper.param_name_hint', {
              paper_name: _slugify(paper?.name || ''),
              ge_state: geState,
            })}
          </p>
        )}
      </div>

      {/* Block 2 — Gloss Enhancer toggle */}
      <GlossEnhancerCard
        supported={geSupported}
        checked={glossEnhancer}
        onChange={setGlossEnhancer}/>

      {/* Block 3 — Conditional overwrite banner */}
      {hasOverwrite && (
        <div className="flex items-start gap-3 bg-icc-warn/10 border border-icc-warn/30 rounded-lg p-4"
             role="status">
          <AlertTriangle size={16} className="text-warn mt-0.5 flex-shrink-0" aria-hidden="true"/>
          <p className="text-sm text-text-strong leading-relaxed">
            {t('wizard_profile_paper.param_overwrite_warning', { ge_state: geState })}
          </p>
        </div>
      )}
    </div>
  );
}


function GlossEnhancerCard({ supported, checked, onChange }) {
  const { t } = useTranslation();

  if (!supported) {
    return (
      <div className="bg-sunken border border-border-soft rounded-lg p-4 opacity-65 cursor-not-allowed">
        <div className="flex items-center gap-3">
          <div className="w-[18px] h-[18px] rounded border-2 border-border-strong flex-shrink-0"/>
          <div className="min-w-0">
            <span className="text-sm font-medium text-text-strong">
              {t('wizard_profile_paper.param_ge_label')}
            </span>
            <span className="ml-2 text-[9px] font-semibold tracking-wider uppercase px-1.5 py-px rounded bg-sunken-deep text-text-muted">
              {t('wizard_profile_paper.param_ge_unsupported_pill')}
            </span>
          </div>
        </div>
        <p className="text-xs2 text-text-faint mt-2 ml-[30px]">
          {t('wizard_profile_paper.param_ge_unsupported_hint')}
        </p>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`w-full text-left rounded-lg p-4 border transition-colors ${
        checked
          ? 'bg-accent/[0.04] border-accent/35'
          : 'bg-surface border-border-soft'
      }`}>
      <div className="flex items-center gap-3">
        <div className={`w-[18px] h-[18px] rounded flex items-center justify-center flex-shrink-0 transition-colors ${
          checked
            ? 'bg-accent border-2 border-accent'
            : 'border-2 border-border-strong'
        }`}>
          {checked && (
            <svg width="10" height="8" viewBox="0 0 10 8" fill="none" aria-hidden="true">
              <path d="M1 3.5L3.5 6L9 1" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          )}
        </div>
        <span className="text-sm font-medium text-text-strong">
          {t('wizard_profile_paper.param_ge_label')}
        </span>
      </div>
      <p className="text-xs2 text-text-muted mt-2 ml-[30px]">
        {t('wizard_profile_paper.param_ge_hint')}
      </p>
    </button>
  );
}


function _slugify(name) {
  return name
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-zA-Z0-9]+/g, '_')
    .replace(/^_|_$/g, '');
}

function _buildSuggestedName(paperName, geState) {
  if (!paperName) return '';
  return `HP Designjet Z9 ${_slugify(paperName)} GE ${geState}`;
}

function _sanitizeProfileName(raw) {
  return raw.replace(/[^a-zA-Z0-9 _-]/g, '').replace(/  +/g, ' ');
}

function _validateName(name, t) {
  const trimmed = (name || '').trim();
  if (trimmed.length === 0) return '';
  if (trimmed.length > 63) return t('wizard_profile_paper.param_name_too_long');
  if (!/^[a-zA-Z0-9 _-]+$/.test(trimmed)) return t('wizard_profile_paper.param_name_invalid');
  return '';
}

function _hasCustomForSlot(paper, glossEnhancer) {
  if (!paper?.icc) return false;
  const targetKind = glossEnhancer ? 'ge_on' : 'ge_off';
  return paper.icc.some(
    (slot) => (slot.kind === targetKind || slot.kind === 'single') && slot.custom,
  );
}
