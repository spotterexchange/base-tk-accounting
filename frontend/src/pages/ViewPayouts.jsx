import { useEffect, useState, useCallback } from 'react';
import {
  Table, Select, Tag, Typography, Row, Col, Card, Statistic,
  Space, Switch, Button, Tooltip, DatePicker, message, Modal, Tabs, Badge
} from 'antd';
import { ReloadOutlined, FilterOutlined, CloudUploadOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { getPayouts, getPayoutFilterOptions, getPayoutFiles, setPaymentDates, getDuplicateReviews } from '../api';
import ImportPayouts from './ImportPayouts';
import DuplicateReview from './DuplicateReview';
import SyncPush from './SyncPush';
import CrowdVoldImport from './CrowdVoldImport';
import ResolveMatchActions from '../components/ResolveMatchActions';

const { Title, Text } = Typography;
const { Option } = Select;

const PAGE_SIZE = 100;

export default function ViewPayouts() {
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [parkingCount, setParkingCount] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [options, setOptions] = useState({ marketplaces: [], files: [], payment_types: [] });
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('payouts');
  const [dupCount, setDupCount] = useState(0);

  const [filters, setFilters] = useState({
    marketplace_id: undefined,
    import_file_id: undefined,
    payment_type: undefined,
    is_parking: undefined,
    unmatched_only: false,
  });

  const load = useCallback(async (currentPage = 1, currentFilters = filters) => {
    setLoading(true);
    try {
      const params = { page: currentPage, page_size: PAGE_SIZE };
      if (currentFilters.marketplace_id != null) params.marketplace_id = currentFilters.marketplace_id;
      if (currentFilters.import_file_id != null) params.import_file_id = currentFilters.import_file_id;
      if (currentFilters.payment_type != null) params.payment_type = currentFilters.payment_type;
      if (currentFilters.is_parking != null) params.is_parking = currentFilters.is_parking;
      if (currentFilters.unmatched_only) params.unmatched_only = true;
      const { data } = await getPayouts(params);
      setRows(data.rows);
      setTotal(data.total);
      setParkingCount(data.parking_count ?? 0);
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const loadDupCount = useCallback(async () => {
    try {
      const { data } = await getDuplicateReviews({ reviewed: false });
      setDupCount(data.length);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    getPayoutFilterOptions().then(({ data }) => setOptions(data));
    load(1, filters);
    loadDupCount();
  }, []);

  const setFilter = (key, val) => {
    const next = { ...filters, [key]: val };
    setFilters(next);
    setPage(1);
    load(1, next);
  };

  const resetFilters = () => {
    const next = { marketplace_id: undefined, import_file_id: undefined, payment_type: undefined, is_parking: undefined, unmatched_only: false };
    setFilters(next);
    setPage(1);
    load(1, next);
    loadFiles(next);
  };

  const [files, setFiles] = useState([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [savingFileId, setSavingFileId] = useState(null);

  const loadFiles = useCallback(async (currentFilters = filters) => {
    setFilesLoading(true);
    try {
      const params = {};
      if (currentFilters.marketplace_id != null) params.marketplace_id = currentFilters.marketplace_id;
      const { data } = await getPayoutFiles(params);
      setFiles(data.files);
    } finally {
      setFilesLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    loadFiles(filters);
  }, []);

  const handleSetFileDate = async (importFileId, date) => {
    const dateStr = date ? date.format('YYYY-MM-DD') : null;
    if (!dateStr) return;
    setSavingFileId(importFileId);
    try {
      await setPaymentDates([importFileId], dateStr);
      setFiles(prev => prev.map(f =>
        f.import_file_id === importFileId ? { ...f, payment_date: dateStr } : f
      ));
      message.success('Payment date updated for this file');
    } catch (e) {
      message.error(e?.response?.data?.detail || e?.message || 'Failed to update payment date');
    } finally {
      setSavingFileId(null);
    }
  };

  const fileColumns = [
    { title: 'File', dataIndex: 'filename', ellipsis: true },
    { title: 'Marketplace', dataIndex: 'marketplace', width: 130,
      render: v => <Tag color="blue">{v}</Tag> },
    { title: 'Rows', dataIndex: 'row_count', align: 'right', width: 80 },
    { title: 'Total Amount', dataIndex: 'total_amount', align: 'right', width: 130,
      render: v => {
        const n = parseFloat(v);
        return <Text type={n < 0 ? 'danger' : undefined}>
          ${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </Text>;
      }
    },
    { title: 'ReachPro Payment Date', dataIndex: 'payment_date', width: 170,
      render: (v, row) => (
        <DatePicker
          size="small"
          value={v ? dayjs(v) : null}
          disabled={savingFileId === row.import_file_id}
          onChange={(date) => handleSetFileDate(row.import_file_id, date)}
          placeholder="Not set"
        />
      )
    },
    { title: '', width: 90,
      render: (_, row) => (
        <Tooltip title="Filter payouts below to just this file">
          <Button
            size="small" icon={<FilterOutlined />}
            onClick={() => setFilter('import_file_id', row.import_file_id)}
          >
            View
          </Button>
        </Tooltip>
      )
    },
  ];

  const columns = [
    { title: 'ID', dataIndex: 'payout_id', key: 'id', width: 70, fixed: 'left' },
    { title: 'Marketplace', dataIndex: 'marketplace', key: 'marketplace', width: 120,
      sorter: (a, b) => (a.marketplace || '').localeCompare(b.marketplace || ''),
      render: v => <Tag color="blue">{v}</Tag> },
    { title: 'File', dataIndex: 'filename', key: 'filename', width: 240, ellipsis: true,
      render: v => <Tooltip title={v}><Text style={{ fontSize: 12 }}>{v}</Text></Tooltip> },
    { title: 'Order ID', dataIndex: 'marketplace_order_id', key: 'order_id', width: 150, ellipsis: true,
      sorter: (a, b) => (a.marketplace_order_id || '').localeCompare(b.marketplace_order_id || '') },
    { title: 'Amount', dataIndex: 'amount', key: 'amount', width: 100, align: 'right',
      sorter: (a, b) => (parseFloat(a.amount) || 0) - (parseFloat(b.amount) || 0),
      render: v => {
        const n = parseFloat(v);
        return <Text type={n < 0 ? 'danger' : n === 0 ? 'secondary' : undefined}>
          ${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </Text>;
      }
    },
    { title: 'Type', dataIndex: 'payment_type', key: 'payment_type', width: 130,
      sorter: (a, b) => (a.payment_type || '').localeCompare(b.payment_type || ''),
      render: v => v ? <Tag>{v}</Tag> : <Text type="secondary">—</Text> },
    { title: 'Parking', dataIndex: 'is_parking', key: 'is_parking', width: 80, align: 'center',
      render: v => v ? <Tag color="orange">Yes</Tag> : <Text type="secondary">No</Text> },
    { title: 'Matched', dataIndex: 'sale_id', key: 'sale_id', width: 110, align: 'center',
      render: (v, r) => v
        ? <Tag color="green">Matched</Tag>
        : r.match_dismissed_at
          ? <Tag>Won't match</Tag>
          : <Tag color="red">Unmatched</Tag> },
    { title: 'Resolve', key: 'resolve', width: 300,
      render: (_, r) => (r.sale_id || r.match_dismissed_at)
        ? <Text type="secondary">—</Text>
        : <ResolveMatchActions payoutId={r.payout_id} onResolved={() => load(page, filters)} /> },
    { title: 'Notes', dataIndex: 'notes', key: 'notes', ellipsis: true,
      sorter: (a, b) => (a.notes || '').localeCompare(b.notes || ''),
      render: v => v ? <Text type="secondary">{v}</Text> : '—' },
    { title: 'Imported', dataIndex: 'created_at', key: 'created_at', width: 170,
      render: v => v ? new Date(v).toLocaleString() : '—' },
  ];

  const filteredFiles = filters.marketplace_id
    ? options.files.filter(f => {
        const mkt = options.marketplaces.find(m => m.id === filters.marketplace_id);
        return mkt && f.marketplace === mkt.name;
      })
    : options.files;

  const handleImported = () => {
    getPayoutFilterOptions().then(({ data }) => setOptions(data));
    loadFiles(filters);
    load(page, filters);
    loadDupCount();
  };

  const pageTotal = rows.reduce((s, r) => s + (parseFloat(r.amount) || 0), 0);

  const payoutsTab = (
    <>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card><Statistic title="Total Rows" value={total} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="Page Total Amount" prefix="$" value={pageTotal.toFixed(2)}
            valueStyle={{ color: pageTotal < 0 ? '#ff4d4f' : 'inherit' }} /></Card>
        </Col>
      </Row>

      <Card
        title="Files"
        style={{ marginBottom: 16 }}
        extra={<Text type="secondary" style={{ fontSize: 12 }}>Payment date applies per file, used when pushing to ReachPro</Text>}
      >
        <Table
          size="small"
          rowKey="import_file_id"
          dataSource={files}
          columns={fileColumns}
          loading={filesLoading}
          pagination={{ pageSize: 10, showTotal: (t) => `${t} files` }}
        />
      </Card>

      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            allowClear placeholder="Marketplace" style={{ width: 160 }}
            value={filters.marketplace_id}
            onChange={v => {
              const next = { ...filters, marketplace_id: v, import_file_id: undefined };
              setFilters(next);
              setPage(1);
              load(1, next);
              loadFiles(next);
            }}
          >
            {options.marketplaces.map(m => <Option key={m.id} value={m.id}>{m.name}</Option>)}
          </Select>

          <Select
            allowClear placeholder="File" style={{ width: 300 }}
            value={filters.import_file_id}
            onChange={v => setFilter('import_file_id', v)}
            showSearch
            optionFilterProp="children"
          >
            {filteredFiles.map(f => <Option key={f.id} value={f.id}>{f.filename}</Option>)}
          </Select>

          <Select
            allowClear placeholder="Payment Type" style={{ width: 160 }}
            value={filters.payment_type}
            onChange={v => setFilter('payment_type', v)}
          >
            {options.payment_types.map(t => <Option key={t} value={t}>{t}</Option>)}
          </Select>

          <Space>
            <Tooltip title="Parking is auto-detected by scanning every column of the raw CSV row for the word 'parking' — it can misfire on real sales, so check what's hidden before trusting this.">
              <Text>Parking:</Text>
            </Tooltip>
            <Select
              allowClear placeholder="All" style={{ width: 100 }}
              value={filters.is_parking}
              onChange={v => setFilter('is_parking', v)}
            >
              <Option value={true}>Yes</Option>
              <Option value={false}>No</Option>
            </Select>
            {parkingCount > 0 && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {filters.is_parking === false
                  ? `${parkingCount.toLocaleString()} hidden as parking`
                  : `${parkingCount.toLocaleString()} flagged as parking`}
              </Text>
            )}
          </Space>

          <Space>
            <Text>Unmatched only:</Text>
            <Switch
              checked={filters.unmatched_only}
              onChange={v => setFilter('unmatched_only', v)}
            />
          </Space>

          <Button icon={<ReloadOutlined />} onClick={resetFilters}>Reset</Button>
        </Space>
      </Card>

      <Table
        dataSource={rows}
        columns={columns}
        rowKey="payout_id"
        loading={loading}
        scroll={{ x: 1700 }}
        size="small"
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total,
          showSizeChanger: false,
          showTotal: (t) => `${t.toLocaleString()} rows`,
          onChange: (p) => { setPage(p); load(p, filters); },
        }}
        rowClassName={(r) => parseFloat(r.amount) < 0 ? 'row-negative' : r.is_parking ? 'row-parking' : ''}
      />

      <style>{`
        .row-negative td { background: #fff1f0 !important; }
        .row-parking td { background: #fffbe6 !important; }
      `}</style>
    </>
  );

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <Title level={3} style={{ margin: 0 }}>Marketplace Payouts</Title>
        <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setImportModalOpen(true)}>
          Import Payouts
        </Button>
      </div>

      <Modal
        title="Import Marketplace Payouts"
        open={importModalOpen}
        onCancel={() => setImportModalOpen(false)}
        footer={null}
        width={1200}
        destroyOnHidden
      >
        <ImportPayouts hideTitle onImported={handleImported} />
      </Modal>

      <Tabs
        defaultActiveKey="payouts"
        onChange={setActiveTab}
        items={[
          { key: 'payouts', label: 'Payouts', children: payoutsTab },
          {
            key: 'duplicates',
            label: (
              <Badge size="small" count={dupCount} offset={[10, -2]}>
                <span>Duplicate Review</span>
              </Badge>
            ),
            children: <DuplicateReview />,
          },
          // Mounted fresh on each activation so its counters (unsynced rows,
          // ready-to-push, last sync) are never stale from an earlier visit
          { key: 'syncpush', label: 'Sync & Push',
            children: activeTab === 'syncpush' ? <SyncPush /> : null },
          { key: 'crowdvold', label: 'CrowdVold Sales Import',
            children: activeTab === 'crowdvold' ? <CrowdVoldImport /> : null },
        ]}
      />
    </div>
  );
}
