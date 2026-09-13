import { useEffect, useRef, useState } from 'react';
import {
  Alert, Button, Card, Input, message, Modal, Progress, Select, Space,
  Statistic, Table, Tag, Typography, Upload, Row, Col, Tooltip,
} from 'antd';
import { InboxOutlined, SearchOutlined } from '@ant-design/icons';
import {
  cvUpload, cvEvents, cvEventSearch, cvSaveMapping, cvExcludeSale,
  cvPreview, cvCreate, cvStatus,
} from '../api';
import { getRole } from '../auth';

const { Text, Title } = Typography;

const money = v => `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const DISPOSITION_META = {
  ready:              { color: 'green',   label: 'Ready' },
  unmapped:           { color: 'orange',  label: 'Unmapped event' },
  skipped_event:      { color: 'default', label: 'Event skipped' },
  no_inventory:       { color: 'red',     label: 'No inventory' },
  possible_duplicate: { color: 'gold',    label: 'Possible duplicate' },
  listing_error:      { color: 'red',     label: 'Listing lookup failed' },
  cancelled:          { color: 'default', label: 'Cancelled' },
  excluded:           { color: 'default', label: 'Excluded' },
  created:            { color: 'blue',    label: 'Created' },
  create_failed:      { color: 'red',     label: 'Create failed' },
};

function EventMappingCard({ onMappingChanged }) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [searchFor, setSearchFor] = useState(null); // event being manually searched
  const [searchQ, setSearchQ] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await cvEvents();
      setEvents(data);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const save = async (ev, mapping) => {
    try {
      await cvSaveMapping({
        cv_event_name: ev.event_name,
        cv_event_date: ev.event_date,
        ...mapping,
      });
      message.success(mapping.skip ? 'Event skipped' : 'Mapping saved');
      load();
      onMappingChanged?.();
    } catch (e) {
      message.error(e?.response?.data?.detail || 'Failed to save mapping');
    }
  };

  const runSearch = async () => {
    if (!searchQ.trim()) return;
    setSearching(true);
    try {
      const { data } = await cvEventSearch(searchQ.trim());
      setSearchResults(data);
      if (!data.length) message.info('No ReachPro events found for that search');
    } catch (e) {
      message.error(e?.response?.data?.detail || 'Search failed');
    } finally {
      setSearching(false);
    }
  };

  const unmapped = events.filter(e => !e.reachpro_event_id && !e.skip).length;

  const columns = [
    { title: 'CrowdVold Event', dataIndex: 'event_name', ellipsis: true },
    { title: 'Date', dataIndex: 'event_date', width: 105 },
    { title: 'Rows', dataIndex: 'rows', width: 60, align: 'right' },
    { title: 'Tickets', dataIndex: 'tickets', width: 70, align: 'right' },
    { title: 'Earnings', dataIndex: 'earnings', width: 100, align: 'right', render: money },
    { title: 'ReachPro Event', key: 'mapping', width: 380,
      render: (_, ev) => {
        if (ev.skip) return <Space><Tag>Skipped</Tag>
          <Button size="small" type="link" onClick={() => save(ev, { skip: false, reachpro_event_id: null })}>Undo</Button></Space>;
        if (ev.reachpro_event_id) return (
          <Space>
            <Tooltip title={`${ev.reachpro_event_date || 'date?'} · ${ev.reachpro_venue || 'venue?'} · ReachPro id ${ev.reachpro_event_id}`}>
              <Tag color="green">{ev.reachpro_event_name || ev.reachpro_event_id}</Tag>
            </Tooltip>
            {ev.created_rows > 0
              ? <Tooltip title={`${ev.created_rows} rows already created in ReachPro — mapping locked`}><Tag color="blue">{ev.created_rows} created</Tag></Tooltip>
              : <Button size="small" type="link" onClick={() => save(ev, { skip: false, reachpro_event_id: null, reachpro_event_name: null })}>Clear</Button>}
          </Space>
        );
        return (
          <Space wrap size={4}>
            {ev.suggestions.map(s => (
              <Tooltip key={s.reachpro_event_id} title={`${s.event_date} · ${s.venue || 'venue?'} · similarity ${s.score}`}>
                <Button size="small" onClick={() => save(ev, {
                  reachpro_event_id: s.reachpro_event_id, reachpro_event_name: s.name,
                  reachpro_event_date: s.event_date, reachpro_venue: s.venue, skip: false,
                })}>
                  {s.name} ({s.score})
                </Button>
              </Tooltip>
            ))}
            <Button size="small" icon={<SearchOutlined />} onClick={() => { setSearchFor(ev); setSearchQ(ev.event_name.split(/[:,(]/)[0].trim()); setSearchResults([]); }}>
              Search
            </Button>
            <Button size="small" onClick={() => save(ev, { skip: true })}>Skip event</Button>
          </Space>
        );
      } },
  ];

  return (
    <Card
      title="2. Map CrowdVold events to ReachPro"
      style={{ marginBottom: 16 }}
      extra={unmapped > 0
        ? <Tag color="orange">{unmapped} unmapped</Tag>
        : events.length > 0 ? <Tag color="green">All mapped</Tag> : null}
    >
      <Table
        size="small"
        rowKey={r => `${r.event_name}|${r.event_date}`}
        dataSource={events}
        columns={columns}
        loading={loading}
        pagination={{ pageSize: 40, hideOnSinglePage: true }}
      />
      <Modal
        title={searchFor ? `Find ReachPro event for "${searchFor.event_name}"` : ''}
        open={!!searchFor}
        onCancel={() => setSearchFor(null)}
        footer={null}
        width={720}
      >
        <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
          <Input
            value={searchQ}
            onChange={e => setSearchQ(e.target.value)}
            onPressEnter={runSearch}
            placeholder="Search ReachPro events by name, or paste an event id (e.g. 161813226)"
          />
          <Button type="primary" loading={searching} onClick={runSearch}>Search</Button>
        </Space.Compact>
        <Table
          size="small"
          rowKey="reachpro_event_id"
          dataSource={searchResults}
          pagination={false}
          columns={[
            { title: 'Event', dataIndex: 'name', ellipsis: true },
            { title: 'Date', dataIndex: 'event_date', width: 100 },
            { title: 'Venue', dataIndex: 'venue', width: 160, ellipsis: true },
            { title: 'Avail. tickets', dataIndex: 'available_tickets', width: 100, align: 'right' },
            { title: '', width: 80, render: (_, r) => (
              <Button size="small" type="primary" onClick={() => {
                save(searchFor, { reachpro_event_id: r.reachpro_event_id, reachpro_event_name: r.name,
                  reachpro_event_date: r.event_date, reachpro_venue: r.venue, skip: false });
                setSearchFor(null);
              }}>Use</Button>
            ) },
          ]}
        />
      </Modal>
    </Card>
  );
}

export default function CrowdVoldImport() {
  const isAdmin = getRole() === 'admin';
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [dispositionFilter, setDispositionFilter] = useState(undefined);
  const [selected, setSelected] = useState([]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [createStatus, setCreateStatus] = useState(null);
  const pollRef = useRef(null);
  const mappingRefresh = useRef(0);

  const doUpload = async (file) => {
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const { data } = await cvUpload(fd);
      setUploadResult(data);
      message.success(`Staged ${data.inserted} new rows (${data.already_staged} already staged, ${data.cancellations_applied} cancellations applied)`);
      mappingRefresh.current += 1;
      setPreview(null);
    } catch (e) {
      message.error(e?.response?.data?.detail || e?.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
    return false;
  };

  const runPreview = async () => {
    setPreviewLoading(true);
    try {
      const { data } = await cvPreview();
      setPreview(data);
      setSelected([]);
    } catch (e) {
      message.error(e?.response?.data?.detail || 'Preview failed');
    } finally {
      setPreviewLoading(false);
    }
  };

  const pollCreate = () => {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await cvStatus();
        setCreateStatus(data);
        if (!data.running) {
          clearInterval(pollRef.current);
          if (data.result) {
            message.success(`Create finished: ${data.result.created} created, ${data.result.already_existed} already existed, ${data.result.failed} failed`);
          } else if (data.error) {
            message.error(`Create run failed: ${data.error}`);
          }
          runPreview();
        }
      } catch { /* keep polling */ }
    }, 2000);
  };
  useEffect(() => () => clearInterval(pollRef.current), []);

  const startCreate = async (orderNumbers) => {
    setConfirmOpen(false);
    try {
      const { data } = await cvCreate(orderNumbers);
      if (!data.started) { message.warning(data.message); return; }
      setCreateStatus({ running: true, progress: { phase: 'starting' } });
      pollCreate();
    } catch (e) {
      message.error(e?.response?.data?.detail || 'Failed to start create run');
    }
  };

  const toggleExclude = async (row, excluded) => {
    try {
      await cvExcludeSale(row.order_number, excluded, excluded ? 'Excluded from preview' : null);
      runPreview();
    } catch (e) {
      message.error(e?.response?.data?.detail || 'Failed');
    }
  };

  const rows = preview?.rows || [];
  // Unmapped rows are counted in the summary but never listed - there is
  // nothing to do on them here; they get handled by mapping their event.
  const filteredRows = dispositionFilter
    ? rows.filter(r => r.disposition === dispositionFilter && r.disposition !== 'unmapped')
    : rows.filter(r => r.disposition !== 'unmapped');
  const readyRows = rows.filter(r => r.disposition === 'ready');
  const totals = preview?.totals || {};

  const rowColumns = [
    { title: 'Order', dataIndex: 'order_number', width: 90,
      render: v => <Text style={{ fontSize: 12 }}>{v}</Text> },
    { title: 'Tab', dataIndex: 'source_tab', width: 150, ellipsis: true },
    { title: 'Event', dataIndex: 'event_name', ellipsis: true },
    { title: 'Sold On', dataIndex: 'transaction_date', width: 105, render: v => (v || '').slice(0, 10) },
    { title: 'Qty', dataIndex: 'quantity', width: 55, align: 'right' },
    { title: 'Earnings', dataIndex: 'earnings', width: 95, align: 'right', render: money },
    { title: 'Disposition', dataIndex: 'disposition', width: 150,
      render: v => { const m = DISPOSITION_META[v] || { color: 'default', label: v }; return <Tag color={m.color}>{m.label}</Tag>; } },
    { title: 'Note / Invoice', key: 'note', ellipsis: true,
      render: (_, r) => r.reachpro_invoice_id
        ? <Text style={{ fontSize: 12 }}>Invoice {r.reachpro_invoice_id}{r.fulfilled ? '' : ' (not fulfilled yet)'}</Text>
        : <Text type="secondary" style={{ fontSize: 12 }}>{r.status_note || ''}</Text> },
    { title: '', key: 'actions', width: 90,
      render: (_, r) => {
        if (r.disposition === 'possible_duplicate' || r.disposition === 'ready') {
          return <Button size="small" onClick={() => toggleExclude(r, true)}>Exclude</Button>;
        }
        if (r.disposition === 'excluded') {
          return <Button size="small" onClick={() => toggleExclude(r, false)}>Include</Button>;
        }
        return null;
      } },
  ];

  const progress = createStatus?.progress;

  return (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="CrowdVold → ReachPro Offline sales"
        description="Upload the monthly CrowdVold sales workbook, map its events to ReachPro, preview what would be created, then create the approved rows in ReachPro as Offline sales marked Fulfilled. Creation is permanent and idempotent (keyed by CrowdVold order number). Nothing here runs a sync — run Full Sync yourself when you're ready for the new sales to enter recon."
      />

      <Card title="1. Upload CrowdVold workbook" style={{ marginBottom: 16 }}>
        <Upload.Dragger
          accept=".xlsx"
          maxCount={1}
          showUploadList={false}
          disabled={uploading}
          beforeUpload={doUpload}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">Click or drag the CV SALES workbook here</p>
          <p className="ant-upload-hint">Re-uploading is safe — already-staged order numbers are skipped.</p>
        </Upload.Dragger>
        {uploadResult && (
          <div style={{ marginTop: 12 }}>
            <Space wrap>
              {uploadResult.tabs.map(t => <Tag key={t.tab}>{t.tab}: {t.sale_rows} rows</Tag>)}
            </Space>
            {uploadResult.warnings?.length > 0 && (
              <Alert
                type="warning" style={{ marginTop: 8 }}
                message={`${uploadResult.warnings.length} warnings`}
                description={<ul style={{ margin: 0, paddingLeft: 18 }}>{uploadResult.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
              />
            )}
          </div>
        )}
      </Card>

      <EventMappingCard key={mappingRefresh.current} onMappingChanged={() => setPreview(null)} />

      <Card
        title="3. Preview dispositions"
        style={{ marginBottom: 16 }}
        extra={<Button type="primary" loading={previewLoading} onClick={runPreview}>Run Preview</Button>}
      >
        {!preview && <Text type="secondary">Run the preview to see what each staged row would do. It checks live ReachPro inventory, so it can take a few seconds.</Text>}
        {preview && (
          <>
            <Row gutter={12} style={{ marginBottom: 12 }}>
              {Object.entries(totals).map(([disp, t]) => {
                const m = DISPOSITION_META[disp] || { label: disp };
                const listable = disp !== 'unmapped';
                const card = (
                  <Card
                    size="small"
                    style={{ cursor: listable ? 'pointer' : 'default',
                             borderColor: dispositionFilter === disp ? '#1677ff' : undefined,
                             opacity: listable ? 1 : 0.75 }}
                    onClick={listable ? () => setDispositionFilter(dispositionFilter === disp ? undefined : disp) : undefined}
                  >
                    <Statistic
                      title={m.label}
                      value={t.rows}
                      suffix={<Text type="secondary" style={{ fontSize: 12 }}>rows · {money(t.earnings)}</Text>}
                    />
                  </Card>
                );
                return (
                  <Col key={disp}>
                    {listable ? card : (
                      <Tooltip title="Not listed below — map these events in step 2 and they'll join the preview.">
                        {card}
                      </Tooltip>
                    )}
                  </Col>
                );
              })}
            </Row>
            {Object.keys(preview.listing_errors || {}).length > 0 && (
              <Alert type="error" style={{ marginBottom: 12 }}
                message="Some events' inventory could not be fetched — their rows are held back. Re-run the preview." />
            )}
            <Table
              size="small"
              rowKey="order_number"
              dataSource={filteredRows}
              columns={rowColumns}
              pagination={{ pageSize: 50, showTotal: t => `${t} rows` }}
              rowSelection={{
                selectedRowKeys: selected,
                onChange: setSelected,
                getCheckboxProps: r => ({ disabled: r.disposition !== 'ready' }),
                // Only Ready rows can be created, so only they get a checkbox
                renderCell: (checked, r, index, node) => r.disposition === 'ready' ? node : null,
              }}
            />
          </>
        )}
      </Card>

      <Card title="4. Create in ReachPro">
        {!isAdmin && <Alert type="warning" showIcon message="Only admins can create sales in ReachPro." />}
        {isAdmin && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Space wrap>
              <Button
                type="primary"
                disabled={!preview || selected.length === 0 || createStatus?.running}
                onClick={() => setConfirmOpen('selected')}
              >
                Create {selected.length} selected
              </Button>
              <Button
                disabled={!preview || readyRows.length === 0 || createStatus?.running}
                onClick={() => setConfirmOpen('all')}
              >
                Create all {readyRows.length} ready rows
              </Button>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Tip: pilot with a few selected rows first, verify them in ReachPro, then create the rest.
              </Text>
            </Space>
            {createStatus?.running && progress && (
              <div>
                <Text>{progress.phase}{progress.current ? ` — ${progress.current}` : ''}</Text>
                {progress.total > 0 && (
                  <Progress percent={Math.round(((progress.done || 0) / progress.total) * 100)}
                    format={() => `${progress.done || 0}/${progress.total}`} />
                )}
              </div>
            )}
            {createStatus && !createStatus.running && createStatus.result && (
              <Alert
                type={createStatus.result.failed > 0 ? 'warning' : 'success'}
                showIcon
                message={`Last run: ${createStatus.result.created} created, ${createStatus.result.fulfilled} fulfilled, ${createStatus.result.already_existed} already existed, ${createStatus.result.failed} failed`}
                description="Remember: nothing syncs automatically. Run Full Sync (Sync & Push tab) when you want these sales in recon."
              />
            )}
            {createStatus && !createStatus.running && createStatus.error && (
              <Alert type="error" showIcon message={`Create run failed: ${createStatus.error}`} />
            )}
          </Space>
        )}
        <Modal
          title="Create sales in ReachPro?"
          open={!!confirmOpen}
          onOk={() => startCreate(confirmOpen === 'selected' ? selected : null)}
          okText="Create"
          okButtonProps={{ danger: true }}
          onCancel={() => setConfirmOpen(false)}
        >
          <p>
            This creates <b>{confirmOpen === 'selected' ? selected.length : readyRows.length} Offline sales</b> in
            ReachPro, marked Fulfilled, allocating real inventory. ReachPro has no delete for sales — this is
            effectively permanent.
          </p>
          <p>No sync will run afterwards; the sales stay out of recon until you run Full Sync manually.</p>
        </Modal>
      </Card>
    </div>
  );
}
