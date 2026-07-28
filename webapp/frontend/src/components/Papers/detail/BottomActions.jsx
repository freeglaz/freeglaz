import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as api from '../../../api/client.js';
import { useLoadedPaper } from '../../../hooks/useLoadedPaper.js';
import ConfirmModal from '../../ui/ConfirmModal.jsx';

export default function BottomActions({ paper, onDeleted, onNotice, onProfilePaper }) {
  const { t } = useTranslation();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const loadedPaper = useLoadedPaper();

  if (paper.factory && !onProfilePaper) return null;

  const isLoaded = loadedPaper?.mediaid === paper.mediaid;

  const handleConfirmDelete = async () => {
    setBusy(true);
    try {
      await api.deletePaper(paper.mediaid);
      onNotice?.('success', t('papers.detail.delete_success', { name: paper.name }));
      onDeleted?.(paper);
    } catch (e) {
      onNotice?.('error', t('papers.detail.delete_error', { message: e.message }));
    } finally {
      setBusy(false);
      setConfirmOpen(false);
    }
  };

  return (
    <section className="px-5 py-4">
      <div className="flex items-center justify-end gap-2">
        {onProfilePaper && (
          <button
            type="button"
            onClick={() => isLoaded && onProfilePaper(paper)}
            disabled={busy || !isLoaded}
            title={!isLoaded ? t('papers.action_load_this_to_profile') : undefined}
            className={`px-3 py-2 rounded-md text-xs2 font-medium border transition-colors ${
              isLoaded
                ? 'border-accent/40 text-accent hover:bg-accent/10'
                : 'border-border-strong text-text-faint opacity-55 cursor-not-allowed'
            }`}>
            {t('wizard_profile_paper.entry_profile_paper')}
          </button>
        )}
        {paper.custom && (
          <button
            type="button"
            onClick={() => setConfirmOpen(true)}
            disabled={busy}
            title={t('papers.detail.bottom_delete_tooltip')}
            className="px-3 py-2 rounded-md text-xs2 font-medium border border-danger/40 text-danger hover:bg-danger/10 transition-colors disabled:opacity-50 disabled:cursor-wait">
            {busy ? t('papers.detail.bottom_delete_in_progress') : t('papers.detail.bottom_delete')}
          </button>
        )}
      </div>

      <ConfirmModal
        open={confirmOpen}
        title={t('papers.detail.modal_delete_title', { name: paper.name })}
        message={t('papers.detail.modal_delete_message')}
        confirmLabel={busy ? t('papers.detail.bottom_delete_in_progress') : t('common.delete')}
        cancelLabel={t('common.cancel')}
        confirmKind="danger"
        onConfirm={handleConfirmDelete}
        onCancel={() => setConfirmOpen(false)}/>
    </section>
  );
}
