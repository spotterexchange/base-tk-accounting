import { useState, useEffect } from 'react';
import {
  Card, DatePicker, Button, Table, Typography, Alert, Space,
  Statistic, Row, Col, Tag, Result, Divider, Spin, Tooltip
} from 'antd';
import { SearchOutlined, CheckOutlined, DownloadOutlined } from '@ant-design/icons';
import { previewPayouts, generatePayouts, getPurchaserDetail, downloadPurchaserPayout } from '../api';
import dayjs from 'dayjs';

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

const fmt = (n) => n == null ? '—' : `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const negStyle = { color: '#ff4d4f' };

const detailColumns = [
  { title: 'Category', dataIndex: 'category', width: 140,
    render: v => <Text strong>{v}</Text> },
  { title: '# Rows', dataIndex: 'count', align: 'right', width: 90 },
  { title: 'Total Amount', dataIndex: 'total_amount', align: 'right', width: 150,
    render: v => <Text style={v < 0 ? negStyle : undefined}>{fmt(v)}</Text> },
  { title: 'Total P&L', dataIndex: 'total_pnl', align: 'right', width: 150,
    render: v => <Text style={v < 0 ? negStyle : undefined}>{fmt(v)}</Text> },
  { title: 'Commission', dataIndex: 'commission', align: 'right', width: 150,
    render: v => <Text strong style={v < 0 ? negStyle : undefined}>{fmt(v)}</Text> },
];

function PurchaserDetail({ purchaserId, dateFrom, dateTo, expenses = [] }) {
  const [rows, setRows] = useState(null);

  useEffect(() => {
    getPurchaserDetail(purchaserId, dateFrom, dateTo)
      .then(({ data }) => setRows(data))
      .catch(() => setRows([]));
  }, [purchaserId, dateFrom, dateTo]);

  if (rows === null) return <Spin size="small" style={{ padding: 16 }} />;

  // Categories first, then each expense as its own line item. The expense
  // dollars hit the P&L column; commission drops by the purchaser's default
  // commission % of the amount (commission_impact), not dollar-for-dollar.
  const dataSource = [
    ...rows.map(r => ({ ...r, key: r.category })),
    ...expenses.map(e => ({
      key: `exp-${e.expense_id}`,
      isExpense: true,
      category: `Expense (${e.expense_date}): ${e.description}`,
      count: null, total_amount: null,
      total_pnl: -e.amount,
      commission: -(e.commission_impact ?? e.amount),
    })),
  ];

  return (
    <Table
      rowKey="key"
      dataSource={dataSource}
      columns={detailColumns}
      pagination={false}
      size="small"
      style={{ margin: '8px 48px' }}
      onRow={r => r.isExpense ? { style: { background: '#fffbe6' } } : {}}
      summary={data => {
        const cats = data.filter(r => !r.isExpense);
        const totalCount = cats.reduce((s, r) => s + r.count, 0);
        const totalAmt = cats.reduce((s, r) => s + r.total_amount, 0);
        const netPnl = data.reduce((s, r) => s + (r.total_pnl || 0), 0);
        const netComm = data.reduce((s, r) => s + r.commission, 0);
        return (
          <Table.Summary.Row style={{ fontWeight: 600 }}>
            <Table.Summary.Cell>{expenses.length ? 'Net of expenses' : 'Total'}</Table.Summary.Cell>
            <Table.Summary.Cell align="right">{totalCount}</Table.Summary.Cell>
            <Table.Summary.Cell align="right">{fmt(totalAmt)}</Table.Summary.Cell>
            <Table.Summary.Cell align="right">{fmt(netPnl)}</Table.Summary.Cell>
            <Table.Summary.Cell align="right">
              <Text strong style={{ color: '#52c41a' }}>{fmt(netComm)}</Text>
            </Table.Summary.Cell>
          </Table.Summary.Row>
        );
      }}
    />
  );
}

export default function GeneratePayouts() {
  const [dates, setDates] = useState(null);
  const [preview, setPreview] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState(null);
  const [selectedIds, setSelectedIds] = useState([]);

  const handlePreview = async () => {
    if (!dates) return;
    setLoading(true);
    setError(null);
    setPreview(null);
    setSelectedIds([]);
    try {
      const { data } = await previewPayouts(dates[0].format('YYYY-MM-DD'), dates[1].format('YYYY-MM-DD'));
      setPreview(data);
      setSelectedIds(data.purchasers.map(p => p.purchaser_id));
    } catch (e) {
      setError(e?.response?.data?.detail || 'Preview failed');
    } finally {
      setLoading(false);
    }
  };

  const handleGenerate = async () => {
    setConfirming(true);
    setError(null);
    try {
      const { data } = await generatePayouts(
        dates[0].format('YYYY-MM-DD'),
        dates[1].format('YYYY-MM-DD'),
        selectedIds
      );
      setResult(data);
      setPreview(null);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Generate failed');
    } finally {
      setConfirming(false);
    }
  };

  const handleReset = () => {
    setDates(null);
    setPreview(null);
    setResult(null);
    setError(null);
    setSelectedIds([]);
  };

  const expandedRowRender = (record) => (
    <PurchaserDetail
      purchaserId={record.purchaser_id}
      dateFrom={dates[0].format('YYYY-MM-DD')}
      dateTo={dates[1].format('YYYY-MM-DD')}
      expenses={record.expenses || []}
    />
  );

  const previewColumns = [
    { title: 'Purchaser', dataIndex: 'purchaser_name', sorter: (a, b) => a.purchaser_name.localeCompare(b.purchaser_name) },
    { title: 'Payout Rows', dataIndex: 'payout_count', width: 110, align: 'right' },
    { title: 'Gross Payout', dataIndex: 'gross_payout', width: 130, align: 'right', render: fmt },
    { title: 'Gross P&L', dataIndex: 'gross_pnl', width: 130, align: 'right', render: fmt },
    { title: 'Commission', dataIndex: 'commission_amount', width: 130, align: 'right',
      render: v => <Text strong style={{ color: '#1890ff' }}>{fmt(v)}</Text>
    },
    { title: 'Expenses', dataIndex: 'expenses_total', width: 120, align: 'right',
      render: (v, row) => v > 0
        ? <Tooltip title={
            (row.expenses || []).map(e => `${e.expense_date}: ${e.description} — ${fmt(e.amount)}`).join('\n')
            + `\nCommission impact: −${fmt(row.expense_commission_impact)}`
          }>
            <Text type="danger">−{fmt(v)}</Text>
          </Tooltip>
        : <Text type="secondary">—</Text>
    },
    { title: 'Net Commission', dataIndex: 'net_commission', width: 140, align: 'right',
      render: (v, row) => <Text strong style={{ color: '#52c41a' }}>{fmt(v ?? row.commission_amount)}</Text>
    },
  ];

  const selectedPurchasers = preview?.purchasers?.filter(p => selectedIds.includes(p.purchaser_id)) || [];
  const selectedCommission = selectedPurchasers.reduce((s, r) => s + (r.net_commission ?? r.commission_amount), 0);

  const handleDownload = async (record) => {
    try {
      const { data, headers } = await downloadPurchaserPayout(record.purchaser_payout_id);
      const cd = headers['content-disposition'] || '';
      const match = cd.match(/filename="?([^"]+)"?/);
      const filename = match ? match[1] : `${record.purchaser_name}.xlsx`;
      const url = URL.createObjectURL(new Blob([data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      /* ignore */
    }
  };

  const resultColumns = [
    { title: 'Purchaser', dataIndex: 'purchaser_name' },
    { title: 'Payout Rows', dataIndex: 'payout_count', width: 110, align: 'right' },
    { title: 'Gross Payout', dataIndex: 'gross_payout', width: 130, align: 'right', render: fmt },
    { title: 'Commission', dataIndex: 'commission_amount', width: 130, align: 'right', render: fmt },
    { title: 'Expenses', dataIndex: 'expenses_total', width: 110, align: 'right',
      render: v => v > 0 ? <Text type="danger">−{fmt(v)}</Text> : <Text type="secondary">—</Text>
    },
    { title: 'Net Due', dataIndex: 'net_commission', width: 130, align: 'right',
      render: (v, row) => <Text strong style={{ color: '#52c41a' }}>{fmt(v ?? row.commission_amount)}</Text>
    },
    { title: 'Status', width: 130, render: () => <Tag color="orange">Pending Review</Tag> },
    { title: '', width: 120, render: (_, record) => (
      <Button size="small" icon={<DownloadOutlined />} onClick={() => handleDownload(record)}>
        Download
      </Button>
    )},
  ];

  if (result) {
    const totalCommission = result.created.reduce((s, r) => s + (r.net_commission ?? r.commission_amount), 0);
    return (
      <div style={{ width: '100%' }}>
        <Result status="success" title="Payouts Generated" />
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={8}>
            <Card><Statistic title="Purchasers" value={result.created.length} /></Card>
          </Col>
          <Col span={8}>
            <Card><Statistic title="Total Commission Due" prefix="$"
              value={totalCommission.toFixed(2)} valueStyle={{ color: '#52c41a' }} /></Card>
          </Col>
        </Row>
        <Table rowKey="purchaser_payout_id" dataSource={result.created} columns={resultColumns} pagination={false} size="small" />
        <div style={{ marginTop: 16, textAlign: 'right' }}>
          <Button onClick={handleReset}>Generate Another Period</Button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ width: '100%' }}>
      <Title level={3}>Stage 4 — Generate Purchaser Payouts</Title>

      {error && (
        <Alert type="error" message={error} closable onClose={() => setError(null)} style={{ marginBottom: 16 }} />
      )}

      <Card style={{ marginBottom: 16 }}>
        <Space size="middle" align="end">
          <div>
            <div style={{ marginBottom: 4, fontWeight: 500 }}>Payment Date Range</div>
            <RangePicker
              value={dates}
              onChange={setDates}
              format="MM/DD/YYYY"
              allowClear
            />
          </div>
          <Button
            type="primary" icon={<SearchOutlined />}
            disabled={!dates}
            loading={loading}
            onClick={handlePreview}
          >
            Preview
          </Button>
        </Space>
      </Card>

      {preview && (
        <>
          {preview.unpriced_offline?.length > 0 && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 16 }}
              message={`${preview.unpriced_offline.length} offline (private) sale${preview.unpriced_offline.length > 1 ? 's' : ''} in this range ha${preview.unpriced_offline.length > 1 ? 've' : 's'} no real sale price entered in ReachPro`}
              description={
                <>
                  <div style={{ marginBottom: 8 }}>
                    These would be treated as total losses and claw back commission. Enter the actual
                    sale price on the invoice in ReachPro, then run Sync again — the sweep re-prices
                    them automatically.
                  </div>
                  <Table
                    rowKey="reachpro_sale_id"
                    size="small"
                    pagination={false}
                    dataSource={preview.unpriced_offline}
                    columns={[
                      { title: 'ReachPro Sale ID', dataIndex: 'reachpro_sale_id' },
                      { title: 'Sale Date', dataIndex: 'sale_date', render: v => (v || '').slice(0, 10) },
                      { title: 'Event', dataIndex: 'event_name' },
                      { title: 'Purchaser', dataIndex: 'purchasers' },
                      { title: 'Proceeds Entered', dataIndex: 'proceeds', align: 'right', render: v => `$${Number(v).toFixed(2)}` },
                      { title: 'Ticket Cost', dataIndex: 'cost', align: 'right', render: v => `$${Number(v).toFixed(2)}` },
                    ]}
                  />
                </>
              }
            />
          )}
          {preview.cancelled_unoffset?.length > 0 && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message={`${preview.cancelled_unoffset.length} cancelled sale${preview.cancelled_unoffset.length > 1 ? 's' : ''} in this range ${preview.cancelled_unoffset.length > 1 ? 'have' : 'has'} payments with no offsetting adjustment`}
              description={
                <>
                  <div style={{ marginBottom: 8 }}>
                    These sales are cancelled in ReachPro but the marketplace paid them, and no
                    clawback has been imported. Verify the offset in the marketplace's statements
                    (or add it manually to a settlement file) before paying commission on them.
                  </div>
                  <Table
                    rowKey="reachpro_sale_id"
                    size="small"
                    pagination={false}
                    dataSource={preview.cancelled_unoffset}
                    columns={[
                      { title: 'Order ID', dataIndex: 'order_id' },
                      { title: 'Event', dataIndex: 'event_name' },
                      { title: 'Status', dataIndex: 'fulfillment_status' },
                      { title: 'Purchaser', dataIndex: 'purchasers' },
                      { title: 'Unoffset $', dataIndex: 'net_unoffset', align: 'right',
                        render: v => <Text type="danger">{fmt(v)}</Text> },
                    ]}
                  />
                </>
              }
            />
          )}
          {preview.purchasers.length === 0 ? (
            <Alert type="warning" showIcon
              message="No eligible payout rows found for this date range."
              description="Check that marketplace payouts are imported and matched to sales, and that the date range covers payment dates." />
          ) : (
            <>
              <Card style={{ marginBottom: 16 }}>
                <Row gutter={16}>
                  <Col span={8}>
                    <Statistic title="Date Range" value={`${preview.date_from} → ${preview.date_to}`} />
                  </Col>
                  <Col span={8}>
                    <Statistic title="Marketplace Payout Rows" value={preview.total_payout_rows} />
                  </Col>
                  <Col span={8}>
                    <Statistic title="Selected Purchasers" value={`${selectedIds.length} / ${preview.purchasers.length}`} />
                  </Col>
                </Row>
              </Card>

              <Table
                rowKey="purchaser_id"
                dataSource={preview.purchasers}
                columns={previewColumns}
                pagination={false}
                size="small"
                style={{ marginBottom: 16 }}
                expandable={{ expandedRowRender }}
                rowSelection={{
                  selectedRowKeys: selectedIds,
                  onChange: (keys) => setSelectedIds(keys),
                }}
                summary={data => {
                  const sel = data.filter(r => selectedIds.includes(r.purchaser_id));
                  const totalGross = sel.reduce((s, r) => s + r.gross_payout, 0);
                  const totalPnl = sel.reduce((s, r) => s + r.gross_pnl, 0);
                  const totalComm = sel.reduce((s, r) => s + r.commission_amount, 0);
                  const totalExpenses = sel.reduce((s, r) => s + (r.expenses_total || 0), 0);
                  const totalNet = sel.reduce((s, r) => s + (r.net_commission ?? r.commission_amount), 0);
                  return (
                    <Table.Summary.Row style={{ fontWeight: 600, background: '#fafafa' }}>
                      <Table.Summary.Cell index={0} />
                      <Table.Summary.Cell index={1}>Selected Total</Table.Summary.Cell>
                      <Table.Summary.Cell index={2} align="right">
                        {sel.reduce((s, r) => s + r.payout_count, 0)}
                      </Table.Summary.Cell>
                      <Table.Summary.Cell index={3} align="right">{fmt(totalGross)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={4} align="right">{fmt(totalPnl)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={5} align="right">
                        <Text strong style={{ color: '#1890ff' }}>{fmt(totalComm)}</Text>
                      </Table.Summary.Cell>
                      <Table.Summary.Cell index={6} align="right">
                        {totalExpenses > 0 ? <Text type="danger">−{fmt(totalExpenses)}</Text> : '—'}
                      </Table.Summary.Cell>
                      <Table.Summary.Cell index={7} align="right">
                        <Text strong style={{ color: '#52c41a' }}>{fmt(totalNet)}</Text>
                      </Table.Summary.Cell>
                    </Table.Summary.Row>
                  );
                }}
              />

              <Alert
                type="info" showIcon style={{ marginBottom: 16 }}
                message={`${selectedIds.length} purchaser(s) selected — net commission total (after expenses): ${fmt(selectedCommission)}. Once confirmed, payout rows and expenses will be locked.`}
              />

              <div style={{ textAlign: 'right' }}>
                <Space>
                  <Button onClick={() => setPreview(null)}>Cancel</Button>
                  <Button
                    type="primary" icon={<CheckOutlined />}
                    loading={confirming}
                    disabled={selectedIds.length === 0}
                    onClick={handleGenerate}
                  >
                    Confirm & Generate ({selectedIds.length} purchaser{selectedIds.length !== 1 ? 's' : ''})
                  </Button>
                </Space>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
