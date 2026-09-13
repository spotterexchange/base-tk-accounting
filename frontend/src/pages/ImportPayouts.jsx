import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Upload, Button, Table, Alert, Card, Statistic, Row, Col,
  Tag, Space, Divider, Typography, Spin, Result, DatePicker,
  Progress, Tooltip, message
} from 'antd';
import { InboxOutlined, CheckCircleOutlined, WarningOutlined, SyncOutlined, CloudUploadOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import {
  stageFiles, commitFiles, getSyncStatus, getBatchStatus,
  startPushPayments, getPushStatus,
} from '../api';
import ResolveMatchActions from '../components/ResolveMatchActions';

const { Dragger } = Upload;
const { Title, Text } = Typography;

const STATUS_COLORS = {
  imported: 'success',
  skipped: 'warning',
};

export default function ImportPayouts({ onImported, hideTitle }) {
  const [fileList, setFileList] = useState([]);
  const [staging, setStaging] = useState(null);
  const [committed, setCommitted] = useState(null);
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState('upload'); // upload | preview | done
  const [error, setError] = useState(null);
  const [paymentDates, setPaymentDates] = useState({}); // filename -> "YYYY-MM-DD"

  const buildFormData = (files) => {
    const fd = new FormData();
    files.forEach(f => {
      const file = f.originFileObj || f;
      if (file instanceof File) fd.append('files', file);
    });
    return fd;
  };

  const handleStage = async () => {
    if (!fileList.length) return;
    setLoading(true);
    setError(null);
    try {
      const { data } = await stageFiles(buildFormData(fileList));
      setStaging(data);
      const initialDates = {};
      data.files.forEach(f => {
        if (f.auto_payment_date) initialDates[f.filename] = f.auto_payment_date;
      });
      setPaymentDates(initialDates);
      setStep('preview');
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Unknown error during validation');
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleCommit = async () => {
    setLoading(true);
    setError(null);
    try {
      const fd = buildFormData(fileList);
      fd.append('payment_dates', JSON.stringify(paymentDates));
      const { data } = await commitFiles(fd);
      setCommitted(data);
      setStep('done');
      onImported?.(data);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Unknown error during import');
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setFileList([]);
    setStaging(null);
    setCommitted(null);
    setPaymentDates({});
    setStep('upload');
    setSyncStatus(null);
    setBatchStatus(null);
    setPushStatus(null);
    if (syncPollRef.current) clearInterval(syncPollRef.current);
    if (pushPollRef.current) clearInterval(pushPollRef.current);
  };

  // ── Post-import: auto-sync, resolve unmatched, gated push ──────────────
  const [syncStatus, setSyncStatus] = useState(null);
  const [batchStatus, setBatchStatus] = useState(null);
  const [batchLoading, setBatchLoading] = useState(false);
  const [pushStatus, setPushStatus] = useState(null);
  const syncPollRef = useRef(null);
  const pushPollRef = useRef(null);

  const loadBatchStatus = useCallback(async (fileIds) => {
    setBatchLoading(true);
    try {
      const { data } = await getBatchStatus(fileIds);
      setBatchStatus(data);
    } catch {
      /* ignore, user can still push manually via the Sync & Push page */
    } finally {
      setBatchLoading(false);
    }
  }, []);

  useEffect(() => {
    if (step !== 'done' || !committed?.import_file_ids?.length) return;
    if (!committed.auto_sync_started) {
      // Another sync was already running when we committed - can't watch its
      // progress reliably, so just check current resolution status directly.
      loadBatchStatus(committed.import_file_ids);
      return;
    }
    setSyncStatus({ running: true, result: null, error: null, progress: null });
    syncPollRef.current = setInterval(async () => {
      try {
        const { data } = await getSyncStatus();
        setSyncStatus(data);
        if (!data.running) {
          clearInterval(syncPollRef.current);
          loadBatchStatus(committed.import_file_ids);
        }
      } catch {
        /* try again next tick */
      }
    }, 1500);
    return () => clearInterval(syncPollRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, committed]);

  const handlePush = async () => {
    setPushStatus({ running: true, result: null, error: null, progress: null });
    try {
      const { data } = await startPushPayments(committed.import_file_ids);
      if (!data.started) {
        message.error(data.message);
        setPushStatus(null);
        return;
      }
    } catch (e) {
      message.error(e?.response?.data?.detail || e?.message || 'Failed to start push');
      setPushStatus(null);
      return;
    }
    pushPollRef.current = setInterval(async () => {
      try {
        const { data } = await getPushStatus();
        setPushStatus(data);
        if (!data.running) clearInterval(pushPollRef.current);
      } catch {
        /* try again next tick */
      }
    }, 1500);
  };

  const missingDateFiles = staging
    ? staging.files.filter(f => f.can_import && !paymentDates[f.filename]).length
    : 0;

  const stageColumns = [
    { title: 'File', dataIndex: 'filename', key: 'filename', width: 360, ellipsis: true,
      render: v => <Tooltip title={v}><span>{v}</span></Tooltip> },
    { title: 'Marketplace', dataIndex: 'marketplace', key: 'marketplace', width: 110,
      render: v => v === 'Unknown'
        ? <Tag color="red">Unrecognized</Tag>
        : <Tag color="blue">{v}</Tag>
    },
    { title: <Tooltip title="Total rows in the file">Rows</Tooltip>,
      dataIndex: 'total_rows_in_file', key: 'total', align: 'right', width: 65 },
    { title: 'Importable', dataIndex: 'importable_rows', key: 'importable', align: 'right', width: 92 },
    { title: 'New', dataIndex: 'new_rows', key: 'new', align: 'right', width: 50,
      render: v => <Text type={v > 0 ? 'success' : 'secondary'}>{v}</Text> },
    { title: <Tooltip title="Rows already in the database from a previous import">Duplicates</Tooltip>,
      dataIndex: 'duplicate_rows', key: 'dupes', align: 'right', width: 92,
      render: v => v > 0 ? <Text type="warning">{v}</Text> : v },
    { title: 'Skipped', dataIndex: 'skipped_rows', key: 'skipped', align: 'right', width: 72 },
    { title: 'Payment Date', key: 'payment_date', width: 130,
      render: (_, row) => {
        if (!row.can_import) return '—';
        const value = paymentDates[row.filename];
        return (
          <DatePicker
            size="small"
            value={value ? dayjs(value) : null}
            onChange={(d) => setPaymentDates(prev => ({
              ...prev,
              [row.filename]: d ? d.format('YYYY-MM-DD') : undefined,
            }))}
            status={!value ? 'error' : undefined}
            placeholder="Required"
          />
        );
      }
    },
    { title: 'Status', dataIndex: 'can_import', key: 'status', align: 'center', width: 125,
      render: (can, row) => {
        if (row.already_imported) return <Tag color="orange">Already Imported</Tag>;
        if (row.marketplace === 'Unknown') return <Tag color="red">Unrecognized</Tag>;
        if (row.errors?.length) return <Tag color="red">Error</Tag>;
        if (row.new_rows === 0) return <Tag color="default">No New Rows</Tag>;
        return <Tag color="green">Ready</Tag>;
      }
    },
  ];

  const commitColumns = [
    { title: 'File', dataIndex: 'filename', key: 'filename', width: 380, ellipsis: true,
      render: v => <Tooltip title={v}><span>{v}</span></Tooltip> },
    { title: 'Marketplace', dataIndex: 'marketplace', key: 'marketplace',
      render: v => v ? <Tag color="blue">{v}</Tag> : '—' },
    { title: 'Rows Imported', dataIndex: 'rows_imported', key: 'rows', align: 'right',
      render: v => <Text type="success">{v}</Text> },
    { title: 'Duplicates Skipped', dataIndex: 'rows_skipped_duplicate', key: 'dupes', align: 'right' },
    { title: 'Errored', dataIndex: 'rows_errored', key: 'errored', align: 'right',
      render: v => v > 0 ? <Text type="danger">{v}</Text> : <Text type="secondary">0</Text> },
    { title: 'Skipped (parse)', dataIndex: 'rows_skipped_parse', key: 'parse_skipped', align: 'right',
      render: v => v > 0 ? <Text type="warning">{v}</Text> : <Text type="secondary">0</Text> },
    { title: 'Status', dataIndex: 'status', key: 'status', align: 'center',
      render: v => <Tag color={STATUS_COLORS[v] || 'default'}>{v}</Tag> },
    { title: 'Reason', dataIndex: 'reason', key: 'reason',
      render: v => v ? <Text type="secondary">{v}</Text> : '—' },
  ];

  const hasRowDetail = (row) =>
    (row.duplicate_details?.length > 0) || (row.row_errors?.length > 0) || (row.skipped_details?.length > 0);

  return (
    <div style={{ maxWidth: 1160, margin: '0 auto' }}>
      {!hideTitle && <Title level={3}>Stage 1 — Import Marketplace Payouts</Title>}

      {error && (
        <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }}
          closable onClose={() => setError(null)} />
      )}

      {step === 'upload' && (
        <Card>
          <Dragger
            multiple
            fileList={fileList}
            beforeUpload={() => false}
            onChange={({ fileList }) => setFileList(fileList)}
            accept=".csv"
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">Click or drag payout files here</p>
            <p className="ant-upload-hint">
              Supports all marketplace formats: SeatGeek, Viagogo, Lysted, Gametime, TickPick, GoTickets, TicketNetwork, Tevo, B2B
            </p>
          </Dragger>
          <div style={{ marginTop: 16, textAlign: 'right' }}>
            <Button
              type="primary"
              size="large"
              disabled={!fileList.length}
              loading={loading}
              onClick={handleStage}
            >
              Validate Files
            </Button>
          </div>
        </Card>
      )}

      {step === 'preview' && staging && (
        <>
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={6}>
              <Card><Statistic title="Files Ready" value={staging.summary.files_ready}
                valueStyle={{ color: '#52c41a' }} prefix={<CheckCircleOutlined />} /></Card>
            </Col>
            <Col span={6}>
              <Card><Statistic title="New Rows to Import" value={staging.summary.total_new_rows} /></Card>
            </Col>
            <Col span={6}>
              <Card><Statistic title="Duplicate Rows" value={staging.summary.total_duplicate_rows}
                valueStyle={staging.summary.total_duplicate_rows > 0 ? { color: '#faad14' } : {}} /></Card>
            </Col>
            <Col span={6}>
              <Card><Statistic title="Already Imported" value={staging.summary.files_already_imported}
                suffix="files" /></Card>
            </Col>
          </Row>

          {staging.summary.files_unrecognized > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`${staging.summary.files_unrecognized} file(s) could not be identified. Check the filename matches a known marketplace format.`}
              style={{ marginBottom: 16 }}
            />
          )}

          {missingDateFiles > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`${missingDateFiles} file(s) need a Payment Date set before you can import — the filename doesn't contain one, so it must be entered manually.`}
              style={{ marginBottom: 16 }}
            />
          )}

          <Card title="File Breakdown">
            <Table
              dataSource={staging.files}
              columns={stageColumns}
              rowKey="filename"
              pagination={false}
              size="small"
            />
          </Card>

          <div style={{ marginTop: 16, display: 'flex', justifyContent: 'space-between' }}>
            <Button onClick={handleReset}>Start Over</Button>
            <Space>
              <Text type="secondary">{staging.summary.total_new_rows} new rows will be imported</Text>
              <Button
                type="primary"
                size="large"
                disabled={staging.summary.total_new_rows === 0 || missingDateFiles > 0}
                loading={loading}
                onClick={handleCommit}
              >
                Import Now
              </Button>
            </Space>
          </div>
        </>
      )}

      {step === 'done' && committed && (
        <>
          <Result
            status="success"
            title={`Import Complete`}
            subTitle={`${committed.summary.total_rows_imported} rows imported across ${committed.summary.files_imported} files`}
          />
          {committed.summary.total_rows_errored > 0 && (
            <Alert
              type="error"
              showIcon
              message={`${committed.summary.total_rows_errored} row(s) failed to import due to an unexpected error (not a normal duplicate) — expand the affected file below for details.`}
              style={{ marginBottom: 16 }}
            />
          )}
          <Card title="Import Results">
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={6}>
                <Statistic title="Files Imported" value={committed.summary.files_imported}
                  valueStyle={{ color: '#52c41a' }} />
              </Col>
              <Col span={6}>
                <Statistic title="Files Skipped" value={committed.summary.files_skipped} />
              </Col>
              <Col span={6}>
                <Statistic title="Rows Imported" value={committed.summary.total_rows_imported}
                  valueStyle={{ color: '#52c41a' }} />
              </Col>
              <Col span={6}>
                <Statistic title="Duplicates Skipped" value={committed.summary.total_rows_skipped} />
              </Col>
            </Row>
            <Table
              dataSource={committed.files}
              columns={commitColumns}
              rowKey="filename"
              pagination={false}
              size="small"
              expandable={{
                rowExpandable: hasRowDetail,
                expandedRowRender: (row) => (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    {row.duplicate_details?.length > 0 && (
                      <div>
                        <Text strong>Sample duplicate order IDs (already in DB):</Text>
                        <div style={{ marginTop: 4 }}>
                          {row.duplicate_details.map((d, i) => (
                            <Tag key={i}>{d.order_id} (${Number(d.amount).toFixed(2)})</Tag>
                          ))}
                        </div>
                      </div>
                    )}
                    {row.row_errors?.length > 0 && (
                      <div>
                        <Text strong type="danger">Sample rows that errored (not duplicates):</Text>
                        <div style={{ marginTop: 4 }}>
                          {row.row_errors.map((e, i) => (
                            <div key={i} style={{ fontSize: 12 }}>
                              <Text code>{e.order_id}</Text> (${Number(e.amount).toFixed(2)}) — <Text type="danger">{e.error}</Text>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {row.skipped_details?.length > 0 && (
                      <div>
                        <Text strong type="warning">Sample rows dropped during parsing (never made it to a payout row):</Text>
                        <div style={{ marginTop: 4 }}>
                          {row.skipped_details.map((s, i) => (
                            <div key={i} style={{ fontSize: 12 }}>
                              <Text type="warning">{s.reason}</Text>
                            </div>
                          ))}
                        </div>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          Full raw rows are saved permanently — see /imports/skipped-rows?import_file_id={row.import_file_id}
                        </Text>
                      </div>
                    )}
                  </Space>
                ),
              }}
            />
          </Card>

          {committed.import_file_ids?.length > 0 && (
            <Card title="Match & Push to ReachPro" style={{ marginTop: 16 }}>
              {syncStatus?.running && (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Progress
                    percent={syncStatus.progress?.direct_attempted
                      ? Math.min(100, Math.round((syncStatus.progress.direct_matched / syncStatus.progress.direct_attempted) * 100))
                      : 0}
                    status="active"
                  />
                  <Text type="secondary">
                    <SyncOutlined spin /> Matching to ReachPro… {syncStatus.progress
                      ? `${syncStatus.progress.direct_matched ?? 0} / ${syncStatus.progress.direct_attempted ?? 0} matched so far`
                      : 'starting…'}
                  </Text>
                </Space>
              )}

              {!syncStatus?.running && batchLoading && <Spin />}

              {!syncStatus?.running && !batchLoading && batchStatus && (
                <>
                  <Row gutter={16} style={{ marginBottom: 16 }}>
                    <Col span={6}><Statistic title="Total Rows" value={batchStatus.total} /></Col>
                    <Col span={6}><Statistic title="Matched" value={batchStatus.matched} valueStyle={{ color: '#52c41a' }} /></Col>
                    <Col span={6}><Statistic title="Dismissed" value={batchStatus.dismissed} /></Col>
                    <Col span={6}>
                      <Statistic title="Still Unresolved" value={batchStatus.unresolved}
                        valueStyle={batchStatus.unresolved > 0 ? { color: '#ff4d4f' } : { color: '#52c41a' }} />
                    </Col>
                  </Row>

                  {batchStatus.unresolved > 0 && (
                    <>
                      <Alert
                        type="warning" showIcon style={{ marginBottom: 12 }}
                        message="Resolve every row below before you can push this batch — retry with a corrected order ID, or mark it as won't match with a note."
                      />
                      <Table
                        dataSource={batchStatus.unresolved_rows}
                        rowKey="payout_id"
                        size="small"
                        pagination={false}
                        style={{ marginBottom: 16 }}
                        columns={[
                          { title: 'Marketplace', dataIndex: 'marketplace', width: 100,
                            render: v => <Tag color="blue">{v}</Tag> },
                          { title: 'Order ID', dataIndex: 'order_id', width: 130 },
                          { title: 'Event', key: 'event', render: (_, r) => (
                            <div>
                              {r.event && <div>{r.event}</div>}
                              {r.venue && <Text type="secondary" style={{ fontSize: 12 }}>{r.venue}</Text>}
                              {!r.event && !r.venue && <Text type="secondary">—</Text>}
                            </div>
                          ) },
                          { title: 'Amount', dataIndex: 'amount', width: 100, align: 'right',
                            render: v => `$${Number(v).toFixed(2)}` },
                          { title: 'Resolve', key: 'resolve', width: 300, render: (_, r) => (
                            <ResolveMatchActions
                              payoutId={r.payout_id}
                              onResolved={() => loadBatchStatus(committed.import_file_ids)}
                            />
                          ) },
                        ]}
                      />
                    </>
                  )}

                  <div style={{ textAlign: 'right' }}>
                    <Tooltip title={batchStatus.unresolved > 0 ? `Resolve ${batchStatus.unresolved} more row(s) first` : undefined}>
                      <Button
                        type="primary" size="large" icon={<CloudUploadOutlined />}
                        disabled={batchStatus.unresolved > 0 || pushStatus?.running}
                        loading={pushStatus?.running}
                        onClick={handlePush}
                      >
                        Push to ReachPro
                      </Button>
                    </Tooltip>
                  </div>
                </>
              )}

              {pushStatus && !pushStatus.running && pushStatus.result && (
                <Row gutter={16} style={{ marginTop: 16 }}>
                  <Col span={8}>
                    <Card><Statistic title="Lines Pushed" value={pushStatus.result.lines_pushed} valueStyle={{ color: '#52c41a' }} /></Card>
                  </Col>
                  <Col span={8}>
                    <Card><Statistic title="Lines Failed" value={pushStatus.result.lines_failed}
                      valueStyle={pushStatus.result.lines_failed > 0 ? { color: '#ff4d4f' } : {}} /></Card>
                  </Col>
                  <Col span={8}>
                    <Card><Statistic title="Skipped — No Payment Date" value={pushStatus.result.skipped_no_payment_date} /></Card>
                  </Col>
                </Row>
              )}
              {pushStatus?.error && (
                <Alert type="error" showIcon message="Push failed" description={pushStatus.error} style={{ marginTop: 16 }} />
              )}
            </Card>
          )}

          <div style={{ marginTop: 16, textAlign: 'right' }}>
            <Button type="primary" onClick={handleReset}>Import More Files</Button>
          </div>
        </>
      )}
    </div>
  );
}
