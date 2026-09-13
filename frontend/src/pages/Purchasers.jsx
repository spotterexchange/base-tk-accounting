import { useEffect, useState } from 'react';
import {
  Table, InputNumber, Button, Tag, Space, Typography, Alert, Tooltip,
  Input, Popconfirm, DatePicker, Tabs
} from 'antd';
import {
  SaveOutlined, WarningOutlined, PlusOutlined, DeleteOutlined, EditOutlined,
  LockOutlined, PercentageOutlined, WalletOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import {
  getPurchasers, updatePurchaser, getOverrides, createOverride, deleteOverride,
  getExpenses, createExpense, updateExpense, deleteExpense,
} from '../api';
import { getRole } from '../auth';

// Regular users can view everything and manage expenses; changing commission
// terms (default %, override rules) is admin-only - enforced server-side,
// and the edit controls are hidden here to match.
const isAdmin = () => getRole() === 'admin';

const { Title, Text } = Typography;

function OverridesPanel({ purchaserId }) {
  const [overrides, setOverrides] = useState(null);
  const [newTag, setNewTag] = useState('');
  const [newBefore, setNewBefore] = useState(null);
  const [newFrom, setNewFrom] = useState(null);
  const [newPct, setNewPct] = useState(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    getOverrides(purchaserId).then(({ data }) => setOverrides(data));
  }, [purchaserId]);

  const canAdd = (newTag.trim() || newBefore || newFrom) && newPct !== null;

  const handleAdd = async () => {
    if (!canAdd) return;
    setAdding(true);
    try {
      const { data } = await createOverride(purchaserId, {
        event_tag: newTag.trim() || null,
        purchased_before: newBefore ? newBefore.format('YYYY-MM-DD') : null,
        purchased_from: newFrom ? newFrom.format('YYYY-MM-DD') : null,
        commission_pct: newPct,
      });
      setOverrides(prev => {
        const idx = prev.findIndex(o => o.override_id === data.override_id);
        return idx >= 0 ? prev.map(o => o.override_id === data.override_id ? data : o) : [...prev, data];
      });
      setNewTag('');
      setNewBefore(null);
      setNewFrom(null);
      setNewPct(null);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to save override');
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (overrideId) => {
    await deleteOverride(purchaserId, overrideId);
    setOverrides(prev => prev.filter(o => o.override_id !== overrideId));
  };

  if (overrides === null) return <Text type="secondary">Loading...</Text>;

  return (
    <div style={{ padding: '4px 24px 8px 24px' }}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        A rule matches when all its set conditions hold: tag text found in the event name or the
        sale's ReachPro tags, and/or the sale's purchase date in range (no purchase date counts as
        older than any date). Tag rules outrank date-only rules; otherwise the default % applies.
        {!isAdmin() && <> <b>Viewing only — commission rules are managed by admins.</b></>}
      </Text>

      {error && <Alert type="error" message={error} closable onClose={() => setError(null)} style={{ margin: '8px 0' }} />}

      <table style={{ marginTop: 8, borderCollapse: 'collapse', width: '100%', maxWidth: 820 }}>
        <thead>
          <tr style={{ background: '#fafafa' }}>
            <th style={thStyle}>Event / Sale Tag</th>
            <th style={thStyle}>Purchased Before</th>
            <th style={thStyle}>Purchased On/After</th>
            <th style={thStyle}>Commission %</th>
            <th style={thStyle}></th>
          </tr>
        </thead>
        <tbody>
          {overrides.map(o => (
            <tr key={o.override_id}>
              <td style={tdStyle}>{o.event_tag || <Text type="secondary">any</Text>}</td>
              <td style={tdStyle}>{o.purchased_before || <Text type="secondary">—</Text>}</td>
              <td style={tdStyle}>{o.purchased_from || <Text type="secondary">—</Text>}</td>
              <td style={tdStyle}>{(o.commission_pct * 100).toFixed(2)}%</td>
              <td style={tdStyle}>
                {isAdmin() && (
                  <Popconfirm title="Delete this override?" onConfirm={() => handleDelete(o.override_id)} okText="Delete" okType="danger">
                    <Button type="text" danger size="small" icon={<DeleteOutlined />} />
                  </Popconfirm>
                )}
              </td>
            </tr>
          ))}
          {isAdmin() && <tr>
            <td style={tdStyle}>
              <Input
                placeholder="e.g. World Cup (optional)"
                value={newTag}
                onChange={e => setNewTag(e.target.value)}
                onPressEnter={handleAdd}
                size="small"
                style={{ width: 180 }}
              />
            </td>
            <td style={tdStyle}>
              <DatePicker size="small" value={newBefore} onChange={setNewBefore}
                placeholder="optional" style={{ width: 130 }} />
            </td>
            <td style={tdStyle}>
              <DatePicker size="small" value={newFrom} onChange={setNewFrom}
                placeholder="optional" style={{ width: 130 }} />
            </td>
            <td style={tdStyle}>
              <InputNumber
                min={0} max={100} step={0.5}
                value={newPct !== null ? newPct * 100 : null}
                formatter={v => v !== undefined && v !== null ? `${v}%` : ''}
                parser={v => v?.replace('%', '')}
                onChange={v => setNewPct(v !== null ? v / 100 : null)}
                onPressEnter={handleAdd}
                size="small"
                style={{ width: 100 }}
                placeholder="0%"
              />
            </td>
            <td style={tdStyle}>
              <Button
                type="primary" size="small" icon={<PlusOutlined />}
                loading={adding}
                disabled={!canAdd}
                onClick={handleAdd}
              >
                Add
              </Button>
            </td>
          </tr>}
        </tbody>
      </table>
    </div>
  );
}

const thStyle = { padding: '4px 12px', textAlign: 'left', fontWeight: 600, fontSize: 12, borderBottom: '1px solid #f0f0f0' };
const tdStyle = { padding: '4px 12px', borderBottom: '1px solid #f0f0f0' };

function ExpensesPanel({ purchaserId }) {
  const [expenses, setExpenses] = useState(null);
  const [newDate, setNewDate] = useState(null);
  const [newDesc, setNewDesc] = useState('');
  const [newAmount, setNewAmount] = useState(null);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editRow, setEditRow] = useState({});
  const [error, setError] = useState(null);

  const load = () => getExpenses(purchaserId).then(({ data }) => setExpenses(data));
  useEffect(() => { load(); }, [purchaserId]);

  const handleAdd = async () => {
    if (!newDate || !newDesc.trim() || newAmount === null) return;
    setAdding(true);
    try {
      await createExpense({
        purchaser_id: purchaserId,
        expense_date: newDate.format('YYYY-MM-DD'),
        description: newDesc.trim(),
        amount: newAmount,
      });
      setNewDate(null); setNewDesc(''); setNewAmount(null);
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to add expense');
    } finally {
      setAdding(false);
    }
  };

  const handleSaveEdit = async () => {
    try {
      await updateExpense(editingId, {
        expense_date: editRow.expense_date,
        description: editRow.description,
        amount: editRow.amount,
      });
      setEditingId(null);
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to save expense');
    }
  };

  const handleDelete = async (id) => {
    try {
      await deleteExpense(id);
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to delete expense');
    }
  };

  if (expenses === null) return <Text type="secondary">Loading...</Text>;

  const columns = [
    { title: 'Date', dataIndex: 'expense_date', width: 130,
      render: (v, row) => row.expense_id === editingId
        ? <DatePicker size="small" value={dayjs(editRow.expense_date)}
            onChange={d => setEditRow(prev => ({ ...prev, expense_date: d.format('YYYY-MM-DD') }))} />
        : v },
    { title: 'Description', dataIndex: 'description',
      render: (v, row) => row.expense_id === editingId
        ? <Input.TextArea size="small" autoSize value={editRow.description}
            onChange={e => setEditRow(prev => ({ ...prev, description: e.target.value }))} />
        : <span style={{ whiteSpace: 'pre-wrap' }}>{v}</span> },
    { title: 'Amount', dataIndex: 'amount', width: 120, align: 'right',
      render: (v, row) => row.expense_id === editingId
        ? <InputNumber size="small" prefix="$" value={editRow.amount} min={0} step={0.01}
            onChange={val => setEditRow(prev => ({ ...prev, amount: val }))} />
        : `$${Number(v).toFixed(2)}` },
    { title: 'Entered By', dataIndex: 'created_by', width: 110,
      render: v => v || '—' },
    { title: 'Batch', key: 'batch', width: 160,
      render: (_, row) => row.locked
        ? <Tooltip title="Locked: claimed by this commission batch. Roll the batch back to edit.">
            <Tag icon={<LockOutlined />} color="blue">
              Batch #{row.included_in_payout_id} ({row.batch_status})
            </Tag>
          </Tooltip>
        : <Tag>Unclaimed</Tag> },
    { title: '', key: 'actions', width: 110,
      render: (_, row) => {
        if (row.locked) return null;
        if (row.expense_id === editingId) {
          return (
            <Space>
              <Button type="primary" size="small" icon={<SaveOutlined />} onClick={handleSaveEdit} />
              <Button size="small" onClick={() => setEditingId(null)}>✕</Button>
            </Space>
          );
        }
        return (
          <Space>
            <Button type="text" size="small" icon={<EditOutlined />}
              onClick={() => { setEditingId(row.expense_id); setEditRow({ expense_date: row.expense_date, description: row.description, amount: Number(row.amount) }); }} />
            <Popconfirm title="Delete this expense?" onConfirm={() => handleDelete(row.expense_id)} okText="Delete" okType="danger">
              <Button type="text" danger size="small" icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        );
      } },
  ];

  return (
    <div style={{ padding: '4px 24px 8px 24px' }}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        Itemized on the commission report; each expense reduces P&L, so commission
        drops by this purchaser's commission % of the amount
      </Text>

      {error && <Alert type="error" message={error} closable onClose={() => setError(null)} style={{ margin: '8px 0' }} />}

      <Table
        rowKey="expense_id"
        dataSource={expenses}
        columns={columns}
        size="small"
        pagination={expenses.length > 10 ? { pageSize: 10 } : false}
        style={{ marginTop: 8, maxWidth: 950 }}
        locale={{ emptyText: 'No expenses recorded' }}
      />

      <Space align="start" style={{ marginTop: 12 }} wrap>
        <DatePicker placeholder="Expense date" value={newDate} onChange={setNewDate} />
        <Input.TextArea
          placeholder="Description (free-form)"
          value={newDesc}
          onChange={e => setNewDesc(e.target.value)}
          autoSize={{ minRows: 1, maxRows: 4 }}
          style={{ width: 420 }}
        />
        <InputNumber prefix="$" placeholder="Amount" min={0} step={0.01}
          value={newAmount} onChange={setNewAmount} style={{ width: 130 }} />
        <Button type="primary" icon={<PlusOutlined />} loading={adding}
          disabled={!newDate || !newDesc.trim() || newAmount === null}
          onClick={handleAdd}>
          Add Expense
        </Button>
      </Space>
    </div>
  );
}

export default function Purchasers() {
  const [purchasers, setPurchasers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [edits, setEdits] = useState({});
  const [saving, setSaving] = useState({});
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    getPurchasers()
      .then(({ data }) => setPurchasers(data))
      .catch(() => setError('Failed to load purchasers'))
      .finally(() => setLoading(false));
  }, []);

  const handleCommissionChange = (id, val) => {
    setEdits(prev => ({ ...prev, [id]: val }));
  };

  const handleSave = async (id, currentPct) => {
    const pct = edits[id];
    if (pct === undefined) return;
    setSaving(prev => ({ ...prev, [id]: true }));
    try {
      const { data } = await updatePurchaser(id, { default_commission_pct: pct / 100 });
      setPurchasers(prev => prev.map(p => p.purchaser_id === id ? { ...p, ...data } : p));
      setEdits(prev => { const n = { ...prev }; delete n[id]; return n; });
    } catch (e) {
      setError('Failed to save');
    } finally {
      setSaving(prev => ({ ...prev, [id]: false }));
    }
  };

  const needsAttention = purchasers.filter(p => !p.default_commission_pct);

  const columns = [
    {
      title: 'Name',
      dataIndex: 'name',
      sorter: (a, b) => a.name.localeCompare(b.name),
      render: (name, row) => (
        <Space>
          {name}
          {!row.default_commission_pct && (
            <Tooltip title="Commission not set">
              <WarningOutlined style={{ color: '#faad14' }} />
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: 'Type',
      dataIndex: 'type',
      width: 100,
      render: t => <Tag>{t || '—'}</Tag>,
    },
    {
      title: 'Default Commission %',
      dataIndex: 'default_commission_pct',
      width: 220,
      render: (val, row) => {
        const id = row.purchaser_id;
        const displayVal = edits[id] !== undefined ? edits[id] : (val ? val * 100 : 0);
        const dirty = edits[id] !== undefined;
        if (!isAdmin()) {
          return <Text>{(val ? val * 100 : 0).toFixed(2)}%</Text>;
        }
        return (
          <Space>
            <InputNumber
              min={0} max={100} step={0.5}
              value={displayVal}
              formatter={v => `${v}%`}
              parser={v => v?.replace('%', '')}
              onChange={v => handleCommissionChange(id, v)}
              style={{ width: 100 }}
            />
            {dirty && (
              <Button
                type="primary" size="small" icon={<SaveOutlined />}
                loading={saving[id]}
                onClick={() => handleSave(id, val)}
              >
                Save
              </Button>
            )}
          </Space>
        );
      },
    },
    {
      title: 'Sales',
      dataIndex: 'sale_count',
      width: 80,
      sorter: (a, b) => a.sale_count - b.sale_count,
    },
    {
      title: 'Notes',
      dataIndex: 'notes',
      render: n => n || '—',
    },
  ];

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      <Title level={3}>Purchasers</Title>

      {error && (
        <Alert type="error" message={error} closable onClose={() => setError(null)} style={{ marginBottom: 16 }} />
      )}

      {needsAttention.length > 0 && (
        <Alert
          type="warning" showIcon style={{ marginBottom: 16 }}
          message={
            <span>
              <strong>{needsAttention.length} purchaser{needsAttention.length > 1 ? 's' : ''}</strong> have no commission rate set.
              Set their commission % before generating payout reports.
            </span>
          }
        />
      )}

      <Table
        rowKey="purchaser_id"
        dataSource={purchasers}
        columns={columns}
        loading={loading}
        pagination={{ pageSize: 50 }}
        rowClassName={row => !row.default_commission_pct ? 'row-warning' : ''}
        size="small"
        expandable={{
          expandedRowRender: row => (
            <Tabs
              size="small"
              style={{ margin: '4px 24px' }}
              items={[
                {
                  key: 'expenses',
                  label: <span><WalletOutlined /> Expenses</span>,
                  children: <ExpensesPanel purchaserId={row.purchaser_id} />,
                },
                {
                  key: 'overrides',
                  label: <span><PercentageOutlined /> Commission Overrides</span>,
                  children: <OverridesPanel purchaserId={row.purchaser_id} />,
                },
              ]}
            />
          ),
          rowExpandable: () => true,
        }}
      />
    </div>
  );
}
