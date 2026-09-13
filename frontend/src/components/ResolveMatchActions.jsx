import { useState } from 'react';
import { Input, Button, Space, message } from 'antd';
import { manualMatchPayout, dismissMatch } from '../api';
import { getUserName } from '../auth';

/**
 * The two resolution actions for an unmatched payout row:
 * - retry the ReachPro lookup with a human-corrected order ID
 * - explicitly dismiss the row as "won't match" (requires a note)
 * Used by both the import screen's per-batch resolve table and the
 * Marketplace Payouts unmatched view (the historical backlog).
 */
export default function ResolveMatchActions({ payoutId, onResolved }) {
  const [retryId, setRetryId] = useState('');
  const [dismissNote, setDismissNote] = useState(null); // null = note editor closed
  const [busy, setBusy] = useState(false);

  const handleRetry = async () => {
    const orderId = retryId.trim();
    if (!orderId) return;
    setBusy(true);
    try {
      const { data } = await manualMatchPayout(payoutId, orderId);
      if (data.matched) {
        message.success('Matched');
        onResolved?.();
      } else {
        message.warning(data.message || 'Still not found with that order ID');
      }
    } catch (e) {
      message.error(e?.response?.data?.detail || e?.message || 'Retry failed');
    } finally {
      setBusy(false);
    }
  };

  const handleDismiss = async () => {
    const note = (dismissNote || '').trim();
    if (!note) {
      message.warning('A note is required to dismiss a row');
      return;
    }
    setBusy(true);
    try {
      await dismissMatch(payoutId, getUserName(), note);
      message.success("Marked as won't match");
      onResolved?.();
    } catch (e) {
      message.error(e?.response?.data?.detail || e?.message || 'Failed to dismiss');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" size={4} style={{ width: '100%' }}>
      <Space.Compact style={{ width: '100%' }}>
        <Input
          size="small"
          placeholder="Correct order ID…"
          value={retryId}
          onChange={(e) => setRetryId(e.target.value)}
          onPressEnter={handleRetry}
          disabled={busy}
        />
        <Button size="small" type="primary" loading={busy} disabled={!retryId.trim()} onClick={handleRetry}>
          Retry
        </Button>
      </Space.Compact>
      {dismissNote == null ? (
        <Button size="small" onClick={() => setDismissNote('')}>
          Mark won't match
        </Button>
      ) : (
        <Space.Compact style={{ width: '100%' }}>
          <Input
            size="small"
            placeholder="Required note…"
            value={dismissNote}
            onChange={(e) => setDismissNote(e.target.value)}
            onPressEnter={handleDismiss}
            disabled={busy}
          />
          <Button size="small" danger loading={busy} disabled={!dismissNote.trim()} onClick={handleDismiss}>
            Confirm
          </Button>
        </Space.Compact>
      )}
    </Space>
  );
}
