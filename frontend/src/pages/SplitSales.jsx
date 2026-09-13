import { useEffect, useState } from 'react';
import { Table, InputNumber, Button, Typography, Alert, Space, Tag, Tooltip } from 'antd';
import { SaveOutlined, WarningOutlined } from '@ant-design/icons';
import { getSplitSales, updateSplits } from '../api';

const { Title, Text } = Typography;

const fmt = n => n == null ? '—' : `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function SplitSales() {
  const [sales, setSales] = useState([]);
  const [loading, setLoading] = useState(true);
  const [edits, setEdits] = useState({});   // { saleId: { purchaserId: pct (0-100) } }
  const [saving, setSaving] = useState({});
  const [errors, setErrors] = useState({});
  const [error, setError] = useState(null);

  useEffect(() => {
    getSplitSales()
      .then(({ data }) => setSales(data))
      .catch(() => setError('Failed to load split sales'))
      .finally(() => setLoading(false));
  }, []);

  const getEdits = (saleId, purchasers) =>
    edits[saleId] || Object.fromEntries(purchasers.map(p => [p.purchaser_id, +(p.split_pct * 100).toFixed(4)]));

  const handleChange = (saleId, purchaserId, val, purchasers) => {
    const current = getEdits(saleId, purchasers);
    setEdits(prev => ({ ...prev, [saleId]: { ...current, [purchaserId]: val ?? 0 } }));
    setErrors(prev => ({ ...prev, [saleId]: null }));
  };

  const isDirty = (saleId, purchasers) => {
    if (!edits[saleId]) return false;
    const e = edits[saleId];
    return purchasers.some(p => Math.abs((e[p.purchaser_id] ?? p.split_pct * 100) - p.split_pct * 100) > 0.001);
  };

  const getTotal = (saleId, purchasers) => {
    const e = getEdits(saleId, purchasers);
    return Object.values(e).reduce((s, v) => s + (v || 0), 0);
  };

  const handleSave = async (saleId, purchasers) => {
    const e = getEdits(saleId, purchasers);
    const total = Object.values(e).reduce((s, v) => s + (v || 0), 0);
    if (Math.abs(total - 100) > 0.1) {
      setErrors(prev => ({ ...prev, [saleId]: `Splits must total 100% (currently ${total.toFixed(2)}%)` }));
      return;
    }
    setSaving(prev => ({ ...prev, [saleId]: true }));
    try {
      const splits = purchasers.map(p => ({
        purchaser_id: p.purchaser_id,
        split_pct: (e[p.purchaser_id] || 0) / 100,
      }));
      await updateSplits(saleId, splits);
      // Update local state
      setSales(prev => prev.map(s => s.sale_id !== saleId ? s : {
        ...s,
        purchasers: s.purchasers.map(p => ({ ...p, split_pct: (e[p.purchaser_id] || 0) / 100 }))
      }));
      setEdits(prev => { const n = { ...prev }; delete n[saleId]; return n; });
    } catch (e) {
      setErrors(prev => ({ ...prev, [saleId]: e?.response?.data?.detail || 'Save failed' }));
    } finally {
      setSaving(prev => ({ ...prev, [saleId]: false }));
    }
  };

  const columns = [
    {
      title: 'Event',
      dataIndex: 'performer',
      render: (performer, row) => (
        <div>
          <div>{performer || '?'}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>{row.venue}</Text>
        </div>
      ),
    },
    { title: 'Date', dataIndex: 'event_date', width: 100 },
    { title: 'Sale ID', dataIndex: 'reachpro_sale_id', width: 110 },
    { title: 'Proceeds', dataIndex: 'proceeds', width: 110, align: 'right', render: fmt },
    { title: 'P&L', dataIndex: 'pnl', width: 100, align: 'right',
      render: v => <span style={{ color: v < 0 ? '#ff4d4f' : 'inherit' }}>{fmt(v)}</span> },
    {
      title: 'Split',
      key: 'split',
      render: (_, row) => {
        const saleId = row.sale_id;
        const purchasers = row.purchasers;
        const e = getEdits(saleId, purchasers);
        const total = getTotal(saleId, purchasers);
        const dirty = isDirty(saleId, purchasers);
        const totalOk = Math.abs(total - 100) <= 0.1;

        return (
          <div>
            {purchasers.map(p => (
              <div key={p.purchaser_id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <Text style={{ width: 120, fontSize: 13 }}>{p.name}</Text>
                <InputNumber
                  min={0} max={100} step={1}
                  value={e[p.purchaser_id] ?? +(p.split_pct * 100).toFixed(4)}
                  formatter={v => `${v}%`}
                  parser={v => v?.replace('%', '')}
                  onChange={v => handleChange(saleId, p.purchaser_id, v, purchasers)}
                  size="small"
                  style={{ width: 90 }}
                />
              </div>
            ))}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
              <Text type="secondary" style={{ width: 120, fontSize: 12 }}>
                Total: <span style={{ color: totalOk ? '#52c41a' : '#ff4d4f', fontWeight: 600 }}>{total.toFixed(0)}%</span>
              </Text>
              {dirty && (
                <Button
                  type="primary" size="small" icon={<SaveOutlined />}
                  loading={saving[saleId]}
                  disabled={!totalOk}
                  onClick={() => handleSave(saleId, purchasers)}
                >
                  Save
                </Button>
              )}
            </div>
            {errors[saleId] && (
              <Text type="danger" style={{ fontSize: 12 }}>{errors[saleId]}</Text>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto' }}>
      <Title level={3}>Split Sales</Title>
      <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
        These sales have multiple purchasers. Verify the split before generating payouts.
      </Text>

      {error && <Alert type="error" message={error} style={{ marginBottom: 16 }} />}

      <Table
        rowKey="sale_id"
        dataSource={sales}
        columns={columns}
        loading={loading}
        pagination={false}
        size="small"
      />
    </div>
  );
}
