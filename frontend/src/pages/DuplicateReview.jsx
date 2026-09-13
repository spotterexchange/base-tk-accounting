import { useEffect, useState, useCallback } from 'react';
import {
  Table, Select, Tag, Typography, Row, Col, Card, Statistic,
  Space, Button, Tooltip, Input, message,
} from 'antd';
import { ReloadOutlined, CheckCircleOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { getDuplicateReviews, markDuplicateReviewed, getPayoutFilterOptions } from '../api';
import { getUserName } from '../auth';

const { Title, Text } = Typography;
const { Option } = Select;

function money(v) {
  const n = Number(v);
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function DuplicateReview() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [marketplaces, setMarketplaces] = useState([]);
  const [savingId, setSavingId] = useState(null);
  const [notesDraft, setNotesDraft] = useState({});

  const [filters, setFilters] = useState({
    reviewed: false, // default to showing only unreviewed - the actual work queue
    marketplace_id: undefined,
  });

  const load = useCallback(async (currentFilters = filters) => {
    setLoading(true);
    try {
      const params = {};
      if (currentFilters.reviewed != null) params.reviewed = currentFilters.reviewed;
      if (currentFilters.marketplace_id != null) params.marketplace_id = currentFilters.marketplace_id;
      const { data } = await getDuplicateReviews(params);
      setRows(data);
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    getPayoutFilterOptions().then(({ data }) => setMarketplaces(data.marketplaces));
    load(filters);
  }, []);

  const setFilter = (key, val) => {
    const next = { ...filters, [key]: val };
    setFilters(next);
    load(next);
  };

  const handleMarkReviewed = async (id) => {
    setSavingId(id);
    try {
      await markDuplicateReviewed(id, getUserName(), notesDraft[id]);
      message.success('Marked reviewed');
      setRows(prev => prev.filter(r => r.duplicate_review_id !== id || filters.reviewed !== false));
      load(filters);
    } catch (e) {
      message.error(e?.response?.data?.detail || e?.message || 'Failed to save');
    } finally {
      setSavingId(null);
    }
  };

  const totalAmount = rows.reduce((s, r) => s + Number(r.amount || 0), 0);

  const columns = [
    { title: 'Marketplace', dataIndex: 'marketplace_name', width: 120,
      render: v => <Tag color="blue">{v}</Tag> },
    { title: 'File', dataIndex: 'filename', width: 240, ellipsis: true,
      render: v => <Tooltip title={v}><Text style={{ fontSize: 12 }}>{v}</Text></Tooltip> },
    { title: 'Order ID', dataIndex: 'marketplace_order_id', width: 150, ellipsis: true },
    { title: 'Amount', dataIndex: 'amount', width: 100, align: 'right',
      render: v => money(v) },
    { title: 'Imported At', dataIndex: 'created_at', width: 170,
      render: v => v ? new Date(v).toLocaleString() : '—' },
    { title: 'Raw Row', dataIndex: 'raw_row', width: 320,
      render: v => (
        <Tooltip title={<pre style={{ margin: 0, fontSize: 11 }}>{JSON.stringify(v, null, 2)}</pre>}>
          <Text type="secondary" style={{ fontSize: 12 }}>hover to inspect</Text>
        </Tooltip>
      ) },
    { title: 'Notes', dataIndex: 'review_notes', width: 220,
      render: (v, r) => (
        <Input
          size="small"
          disabled={r.reviewed}
          value={notesDraft[r.duplicate_review_id] ?? v ?? ''}
          placeholder="Why this is/isn't a real duplicate..."
          onChange={(e) => setNotesDraft(prev => ({ ...prev, [r.duplicate_review_id]: e.target.value }))}
        />
      ) },
    { title: 'Status', width: 140, align: 'center',
      render: (_, r) => r.reviewed
        ? <Tag color="green">Reviewed{r.reviewed_by ? ` by ${r.reviewed_by}` : ''}</Tag>
        : (
          <Button
            size="small" type="primary" icon={<CheckCircleOutlined />}
            loading={savingId === r.duplicate_review_id}
            onClick={() => handleMarkReviewed(r.duplicate_review_id)}
          >
            Mark Reviewed
          </Button>
        ) },
  ];

  return (
    <div style={{ width: '100%' }}>
      <Title level={3}>Duplicate Review</Title>
      <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
        Every time an import row's key (marketplace + order ID + amount) matched a row already in the
        database, the incoming row was dropped and flagged here instead of silently discarded. This is
        deliberately not auto-resolved — confirm each one really is the same transaction re-arriving,
        not two distinct transactions that happened to share a key.
      </Text>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Card><Statistic title="Rows Shown" value={rows.length} /></Card></Col>
        <Col span={6}><Card><Statistic title="Total Amount" value={totalAmount} precision={2} prefix="$" /></Card></Col>
      </Row>

      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            style={{ width: 200 }}
            value={filters.reviewed}
            onChange={v => setFilter('reviewed', v)}
          >
            <Option value={false}>Needs Review</Option>
            <Option value={true}>Already Reviewed</Option>
          </Select>

          <Select
            allowClear placeholder="Marketplace" style={{ width: 160 }}
            value={filters.marketplace_id}
            onChange={v => setFilter('marketplace_id', v)}
          >
            {marketplaces.map(m => <Option key={m.id} value={m.id}>{m.name}</Option>)}
          </Select>

          <Button icon={<ReloadOutlined />} onClick={() => load(filters)}>Refresh</Button>
        </Space>
      </Card>

      <Table
        dataSource={rows}
        columns={columns}
        rowKey="duplicate_review_id"
        loading={loading}
        size="small"
        pagination={{ pageSize: 50, showTotal: (t) => `${t.toLocaleString()} rows` }}
      />
    </div>
  );
}
