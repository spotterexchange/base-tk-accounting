import { useState, useEffect, useRef, useCallback } from 'react';
import { Button, Alert, Card, Statistic, Row, Col, Result, Typography, Progress, Space, Divider, Table, DatePicker, Tag } from 'antd';
import { SyncOutlined } from '@ant-design/icons';
import {
  getUnsyncedCount, startSync, getSyncStatus, getLastSyncRun,
  getPendingPushCount, startPushPayments, getPushStatus,
  getFilesMissingPaymentDate, setPaymentDates,
  startInventoryRefresh, getInventoryStatus, getInventoryLastRefresh,
} from '../api';

const { Title, Text } = Typography;

export default function SyncPush() {
  const [unsyncedCount, setUnsyncedCount] = useState(null);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  const [lastRun, setLastRun] = useState(null);

  const loadCount = useCallback(async () => {
    try {
      const { data } = await getUnsyncedCount();
      setUnsyncedCount(data.unsynced_count);
    } catch {
      /* ignore */
    }
  }, []);

  const loadLastRun = useCallback(async () => {
    try {
      const { data } = await getLastSyncRun();
      setLastRun(data);
    } catch {
      /* ignore */
    }
  }, []);

  const poll = useCallback(() => {
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await getSyncStatus();
        setStatus(data);
        if (!data.running) {
          clearInterval(pollRef.current);
          loadCount();
          loadLastRun();
        }
      } catch {
        /* ignore, try again next tick */
      }
    }, 1500);
  }, [loadCount, loadLastRun]);

  useEffect(() => {
    loadCount();
    loadLastRun();
    getSyncStatus().then(({ data }) => {
      if (data.running) {
        setStatus(data);
        poll();
      }
    }).catch(() => {});
    return () => clearInterval(pollRef.current);
  }, [loadCount, loadLastRun, poll]);

  const handleStart = async () => {
    setError(null);
    try {
      const { data } = await startSync();
      if (!data.started) {
        setError(data.message);
        return;
      }
      setStatus({ running: true, result: null, error: null, progress: null });
      poll();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to start sync');
    }
  };

  const running = status?.running;
  const progress = status?.progress;
  const result = !running ? status?.result : null;
  const syncError = !running ? status?.error : null;

  const percent = progress && progress.direct_attempted
    ? Math.min(100, Math.round((progress.direct_matched / progress.direct_attempted) * 100))
    : 0;

  const [pendingPush, setPendingPush] = useState(null);
  const [pushStatus, setPushStatus] = useState(null);
  const [pushError, setPushError] = useState(null);
  const pushPollRef = useRef(null);

  const loadPendingPush = useCallback(async () => {
    try {
      const { data } = await getPendingPushCount();
      setPendingPush(data);
    } catch {
      /* ignore */
    }
  }, []);

  const pollPush = useCallback(() => {
    pushPollRef.current = setInterval(async () => {
      try {
        const { data } = await getPushStatus();
        setPushStatus(data);
        if (!data.running) {
          clearInterval(pushPollRef.current);
          loadPendingPush();
        }
      } catch {
        /* ignore, try again next tick */
      }
    }, 1500);
  }, [loadPendingPush]);

  useEffect(() => {
    loadPendingPush();
    getPushStatus().then(({ data }) => {
      if (data.running) {
        setPushStatus(data);
        pollPush();
      }
    }).catch(() => {});
    return () => clearInterval(pushPollRef.current);
  }, [loadPendingPush, pollPush]);

  const handlePushPayments = async () => {
    setPushError(null);
    try {
      const { data } = await startPushPayments();
      if (!data.started) {
        setPushError(data.message);
        return;
      }
      setPushStatus({ running: true, result: null, error: null, progress: null });
      pollPush();
    } catch (e) {
      setPushError(e?.response?.data?.detail || e?.message || 'Failed to start payment push');
    }
  };

  const pushRunning = pushStatus?.running;
  const pushProgress = pushStatus?.progress;
  const pushResult = !pushRunning ? pushStatus?.result : null;
  const pushRunError = !pushRunning ? pushStatus?.error : null;

  const [missingDateFiles, setMissingDateFiles] = useState([]);
  const [selectedFileIds, setSelectedFileIds] = useState([]);
  const [bulkDate, setBulkDate] = useState(null);
  const [savingDates, setSavingDates] = useState(false);
  const [dateSaveError, setDateSaveError] = useState(null);

  const loadMissingDateFiles = useCallback(async () => {
    try {
      const { data } = await getFilesMissingPaymentDate();
      setMissingDateFiles(data.files);
      setSelectedFileIds([]);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadMissingDateFiles();
  }, [loadMissingDateFiles]);

  // Refresh the missing-dates list whenever a push run finishes, since it may
  // have changed which rows are still pending.
  useEffect(() => {
    if (!pushRunning) loadMissingDateFiles();
  }, [pushRunning, loadMissingDateFiles]);

  const handleApplyBulkDate = async () => {
    if (!bulkDate || selectedFileIds.length === 0) return;
    setSavingDates(true);
    setDateSaveError(null);
    try {
      await setPaymentDates(selectedFileIds, bulkDate.format('YYYY-MM-DD'));
      await loadMissingDateFiles();
      await loadPendingPush();
      setBulkDate(null);
    } catch (e) {
      setDateSaveError(e?.response?.data?.detail || e?.message || 'Failed to save payment dates');
    } finally {
      setSavingDates(false);
    }
  };

  // ── Inventory cache (refreshed nightly; manual backstop here) ──────────
  const [invLast, setInvLast] = useState(null);
  const [invRunning, setInvRunning] = useState(false);
  const [invError, setInvError] = useState(null);
  const invPollRef = useRef(null);

  const loadInvLast = useCallback(async () => {
    try {
      const { data } = await getInventoryLastRefresh();
      setInvLast(data);
    } catch {
      /* ignore */
    }
  }, []);

  const pollInventory = useCallback(() => {
    invPollRef.current = setInterval(async () => {
      try {
        const { data } = await getInventoryStatus();
        setInvRunning(!!data.running);
        if (!data.running) {
          clearInterval(invPollRef.current);
          loadInvLast();
        }
      } catch {
        /* try again next tick */
      }
    }, 5000);
  }, [loadInvLast]);

  useEffect(() => {
    loadInvLast();
    getInventoryStatus().then(({ data }) => {
      if (data.running) {
        setInvRunning(true);
        pollInventory();
      }
    }).catch(() => {});
    return () => clearInterval(invPollRef.current);
  }, [loadInvLast, pollInventory]);

  const handleInventoryRefresh = async () => {
    setInvError(null);
    try {
      const { data } = await startInventoryRefresh();
      if (!data.started) {
        setInvError(data.message);
        return;
      }
      setInvRunning(true);
      pollInventory();
    } catch (e) {
      setInvError(e?.response?.data?.detail || e?.message || 'Failed to start inventory refresh');
    }
  };

  const missingDateColumns = [
    { title: 'File', dataIndex: 'filename', ellipsis: true },
    { title: 'Marketplace', dataIndex: 'marketplace', width: 130,
      render: v => <Tag color="blue">{v}</Tag> },
    { title: 'Rows', dataIndex: 'row_count', align: 'right', width: 80 },
    { title: 'Total Amount', dataIndex: 'total_amount', align: 'right', width: 130,
      render: v => `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` },
  ];

  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      <Alert
        type="info" showIcon style={{ marginBottom: 16 }}
        message="New imports match automatically and push from the import screen."
        description="This tab is for the backlog: re-try matching older unmatched rows (e.g. sales ReachPro processed after the payout was imported) and push rows that became ready later."
      />

      <Card size="small" style={{ marginBottom: 16 }} title="Inventory Cache (for the commission file's Remaining Inventory tab)">
        {invError && (
          <Alert type="error" showIcon message={invError} closable
            onClose={() => setInvError(null)} style={{ marginBottom: 12 }} />
        )}
        {invLast?.last_attempt_error && (
          <Alert type="warning" showIcon style={{ marginBottom: 12 }}
            message="Most recent refresh attempt failed"
            description={invLast.last_attempt_error} />
        )}
        <Row gutter={16} align="middle">
          <Col span={9}>
            <Statistic title="Last Successful Refresh"
              value={invLast?.last_success_at ? new Date(invLast.last_success_at).toLocaleString() : '—'} />
          </Col>
          <Col span={5}>
            <Statistic title="Inventory Rows" value={invLast?.last_success_rows ?? '—'} />
          </Col>
          <Col span={10} style={{ textAlign: 'right' }}>
            <Space orientation="vertical" size={4} style={{ alignItems: 'flex-end' }}>
              <Button
                icon={<SyncOutlined spin={invRunning} />}
                loading={invRunning}
                onClick={handleInventoryRefresh}
              >
                {invRunning ? 'Refreshing… (takes ~1-2 hours)' : 'Refresh Now'}
              </Button>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Runs automatically every night — manual refresh is a backstop
              </Text>
            </Space>
          </Col>
        </Row>
      </Card>

      <Title level={4}>Full Sync with ReachPro</Title>
      <Text type="secondary">
        The Full Sync does four things: retries matching every unmatched payout row against
        ReachPro (sales processed after their payout arrived), pulls in <b>offline/private sales</b>{' '}
        and <b>wasted tickets</b> from the last 270 days for commissioned purchasers, and
        re-checks <b>tags and purchase dates</b> on existing sales to pick up edits made in
        ReachPro after a sale first synced. It typically takes 15–30 minutes.
      </Text>

      {error && (
        <Alert type="error" showIcon message={error} closable
          onClose={() => setError(null)} style={{ margin: '16px 0' }} />
      )}

      <Card style={{ marginTop: 16, marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col span={8}>
            <Statistic title="Unsynced Payout Rows" value={unsyncedCount ?? '—'} />
          </Col>
          <Col span={10}>
            {lastRun && lastRun.status !== 'never' && (
              <Space direction="vertical" size={2}>
                <Text type="secondary" style={{ fontSize: 12 }}>Last Full Sync</Text>
                <Space>
                  <Tag color={{ success: 'green', failed: 'red', in_progress: 'blue' }[lastRun.status]}>
                    {{ success: 'Success', failed: 'Failed', in_progress: 'In Progress' }[lastRun.status]}
                  </Tag>
                  <Text>{new Date(lastRun.started_at).toLocaleString('en-US',
                    { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</Text>
                </Space>
                {lastRun.status === 'success' && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {lastRun.processed ?? 0} payouts matched, {lastRun.sales_created ?? 0} sales created
                  </Text>
                )}
                {lastRun.status === 'failed' && lastRun.error && (
                  <Text type="danger" style={{ fontSize: 12 }}>{lastRun.error}</Text>
                )}
              </Space>
            )}
            {lastRun && lastRun.status === 'never' && (
              <Text type="secondary">No full sync recorded yet</Text>
            )}
          </Col>
          <Col span={6} style={{ textAlign: 'right' }}>
            <Button
              type="primary" size="large" icon={<SyncOutlined spin={!!running} />}
              loading={!!running}
              onClick={handleStart}
            >
              {running ? 'Syncing…' : 'Full Sync'}
            </Button>
          </Col>
        </Row>
      </Card>

      {running && (
        <Card style={{ marginBottom: 16 }}>
          {['wasted', 'offline', 'tags', 'purchase dates'].includes(progress?.phase) ? (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Progress percent={100} status="active" showInfo={false} />
              <Text>
                {{
                  wasted: `Fetching wasted tickets for ${progress.fetching}…`,
                  offline: `Scanning for offline sales (${progress.fetching})…`,
                  tags: `Refreshing sale tags from ReachPro (${progress.fetching})…`,
                  'purchase dates': `Refreshing purchase dates from purchase orders (${progress.fetching})…`,
                }[progress.phase]}
              </Text>
            </Space>
          ) : (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Progress percent={percent} status="active" />
              <Text type="secondary">
                {progress
                  ? `${progress.direct_matched ?? 0} / ${progress.direct_attempted ?? 0} payouts matched — ${progress.sales_created ?? 0} new sales created`
                  : 'Starting…'}
              </Text>
            </Space>
          )}
        </Card>
      )}

      {syncError && (
        <Alert type="error" showIcon message="Sync failed" description={syncError} style={{ marginBottom: 16 }} />
      )}

      {result && (
        <>
          <Result status="success" title="Sync Complete" />
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={8}>
              <Card><Statistic title="Sales Created" value={result.sales_created}
                valueStyle={{ color: '#52c41a' }} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="Payouts Matched" value={result.direct_matched}
                valueStyle={{ color: '#1890ff' }} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="Still Unmatched" value={result.unmatched_after_sync} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="Already Existed" value={result.already_existed} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="Wasted Tickets Created" value={result.wasted_created} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="Wasted Tickets Skipped (already synced)" value={result.wasted_skipped} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="Wasted Inventory $ Created" prefix="$"
                value={(result.wasted_total_cost ?? 0).toFixed(2)}
                valueStyle={result.wasted_total_cost > 0 ? { color: '#ff4d4f' } : {}} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="No Buyer ID" value={result.no_buyer_id} /></Card>
            </Col>
          </Row>
        </>
      )}

      <Divider />

      <Title level={4}>Push Backlog to ReachPro</Title>
      <Text type="secondary">
        Pushes matched payouts into ReachPro as invoice payments — one payment per
        imported file (per marketplace), with individual payment lines per sale.
        Rows already pushed are tracked and never reprocessed; rows whose file has
        no Payment Date set are skipped until one is set below.
      </Text>

      {pushError && (
        <Alert type="error" showIcon message={pushError} closable
          onClose={() => setPushError(null)} style={{ margin: '16px 0' }} />
      )}

      <Card style={{ marginTop: 16, marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col span={8}>
            <Statistic title="Ready to Push" value={pendingPush?.ready_to_push ?? '—'}
              valueStyle={{ color: '#52c41a' }} />
          </Col>
          <Col span={8}>
            <Statistic title="Missing Payment Date" value={pendingPush?.missing_payment_date ?? '—'} />
          </Col>
          <Col span={8} style={{ textAlign: 'right' }}>
            <Button
              type="primary" size="large" icon={<SyncOutlined spin={!!pushRunning} />}
              loading={!!pushRunning}
              disabled={!pendingPush?.ready_to_push}
              onClick={handlePushPayments}
            >
              {pushRunning ? 'Pushing…' : 'Push to ReachPro'}
            </Button>
          </Col>
        </Row>
      </Card>

      {missingDateFiles.length > 0 && (
        <Card
          title={`Files Missing a Payment Date (${missingDateFiles.length})`}
          style={{ marginBottom: 16 }}
        >
          {dateSaveError && (
            <Alert type="error" showIcon message={dateSaveError} closable
              onClose={() => setDateSaveError(null)} style={{ marginBottom: 12 }} />
          )}
          <Space style={{ marginBottom: 12 }}>
            <Text>{selectedFileIds.length} selected</Text>
            <DatePicker
              value={bulkDate}
              onChange={setBulkDate}
              placeholder="Payment date"
            />
            <Button
              type="primary"
              disabled={!bulkDate || selectedFileIds.length === 0}
              loading={savingDates}
              onClick={handleApplyBulkDate}
            >
              Apply to Selected
            </Button>
          </Space>
          <Table
            size="small"
            rowKey="import_file_id"
            dataSource={missingDateFiles}
            columns={missingDateColumns}
            pagination={{ pageSize: 10 }}
            rowSelection={{
              selectedRowKeys: selectedFileIds,
              onChange: setSelectedFileIds,
            }}
          />
        </Card>
      )}

      {pushRunning && (
        <Card style={{ marginBottom: 16 }}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Progress percent={100} status="active" showInfo={false} />
            <Text type="secondary">
              {pushProgress
                ? `File ${pushProgress.file_index ?? 0} / ${pushProgress.total_files ?? 0} (${pushProgress.file ?? ''}) — ${pushProgress.lines_pushed ?? 0} pushed, ${pushProgress.lines_failed ?? 0} failed`
                : 'Starting…'}
            </Text>
          </Space>
        </Card>
      )}

      {pushRunError && (
        <Alert type="error" showIcon message="Payment push failed" description={pushRunError} style={{ marginBottom: 16 }} />
      )}

      {pushResult && (
        <>
          <Result status="success" title="Payment Push Complete" />
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={8}>
              <Card><Statistic title="Lines Pushed" value={pushResult.lines_pushed}
                valueStyle={{ color: '#52c41a' }} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="Lines Failed" value={pushResult.lines_failed}
                valueStyle={pushResult.lines_failed > 0 ? { color: '#ff4d4f' } : {}} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="Payments Created/Reused" value={pushResult.payments_created_or_reused} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="Files Processed" value={pushResult.files_processed} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="Skipped — No Payment Date" value={pushResult.skipped_no_payment_date} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="Skipped — Already Paid" value={pushResult.skipped_already_paid} /></Card>
            </Col>
          </Row>
        </>
      )}
    </div>
  );
}
