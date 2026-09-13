import { useEffect, useState, useCallback } from 'react';
import {
  Table, Select, Tag, Typography, Row, Col, Card, Statistic,
  Space, Button, Tooltip, DatePicker, Input, InputNumber, message, Modal, Collapse,
} from 'antd';
import { ReloadOutlined, LockOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import {
  getAdjustments, getAdjustmentFilterOptions, updateAdjustmentNotes, updateAdjustmentTreatment,
  updateAdjustmentReason, getNeedsOffset, ignoreNeedsOffset, unignoreNeedsOffset, addManualOffset,
} from '../api';
import { Alert } from 'antd';

const { Title, Text } = Typography;
const { Option } = Select;
const { RangePicker } = DatePicker;

const TREATMENT_OPTIONS = [
  { value: 'full_amount', label: 'Full Amount' },
  { value: 'full_amount_minus_cost', label: 'Full Amount Minus Cost' },
  { value: 'none', label: 'None' },
];

const REASON_OPTIONS = [
  { value: 'missed_ihd', label: 'Missed IHD' },
  { value: 'doe', label: 'DOE' },
  { value: 'cancelled_event', label: 'Cancelled Event' },
  { value: 'rejected_replacements', label: 'Rejected Replacements' },
  { value: 'other', label: 'Other' },
];
const REASONS_REQUIRING_NOTE = ['rejected_replacements', 'other'];

function money(v) {
  const n = Number(v);
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function NeedsOffsetCard({ onOffsetAdded }) {
  const [items, setItems] = useState([]);
  const [ignoreTarget, setIgnoreTarget] = useState(null);
  const [ignoreNote, setIgnoreNote] = useState('');
  const [offsetTarget, setOffsetTarget] = useState(null);
  const [offsetAmount, setOffsetAmount] = useState(null);
  const [offsetDate, setOffsetDate] = useState(null);
  const [offsetNote, setOffsetNote] = useState('');
  const [saving, setSaving] = useState(false);

  const load = () => getNeedsOffset().then(({ data }) => setItems(data)).catch(() => {});
  useEffect(() => { load(); }, []);

  const active = items.filter(i => !i.offset_resolution);
  const ignored = items.filter(i => i.offset_resolution);
  if (!items.length) return null;

  const openIgnore = (row) => { setIgnoreTarget(row); setIgnoreNote(''); };
  const openOffset = (row) => {
    setOffsetTarget(row);
    setOffsetAmount(-Number(row.net_unoffset));
    setOffsetDate(dayjs());
    setOffsetNote('');
  };

  const submitIgnore = async () => {
    if (!ignoreNote.trim()) { message.warning('A note explaining why this is OK is required.'); return; }
    setSaving(true);
    try {
      await ignoreNeedsOffset(ignoreTarget.sale_id, ignoreNote.trim());
      message.success(`${ignoreTarget.order_id} marked as OK / ignored`);
      setIgnoreTarget(null);
      load();
    } catch (e) {
      message.error(e?.response?.data?.detail || e?.message || 'Failed to ignore');
    } finally {
      setSaving(false);
    }
  };

  const submitOffset = async () => {
    if (!(offsetAmount < 0)) { message.warning('Offset amount must be negative.'); return; }
    if (!offsetDate) { message.warning('Pick the payment date the offset should count under.'); return; }
    setSaving(true);
    try {
      await addManualOffset(offsetTarget.sale_id, {
        amount: offsetAmount,
        payment_date: offsetDate.format('YYYY-MM-DD'),
        note: offsetNote.trim() || undefined,
      });
      message.success(`Offset of ${money(offsetAmount)} recorded for ${offsetTarget.order_id}`);
      setOffsetTarget(null);
      load();
      onOffsetAdded?.();
    } catch (e) {
      message.error(e?.response?.data?.detail || e?.message || 'Failed to add offset');
    } finally {
      setSaving(false);
    }
  };

  const unignore = async (row) => {
    try {
      await unignoreNeedsOffset(row.sale_id);
      message.success(`${row.order_id} back on the hunt list`);
      load();
    } catch (e) {
      message.error(e?.response?.data?.detail || e?.message || 'Failed to un-ignore');
    }
  };

  return (
    <>
      {active.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={`${active.length} cancelled sale${active.length > 1 ? 's' : ''} received payment with no offsetting adjustment`}
          description={
            <>
              <div style={{ marginBottom: 8 }}>
                The marketplace paid these sales but ReachPro marks them cancelled, and no negative
                adjustment has been imported. Verify the clawback in the marketplace's statements, then
                resolve each row: <b>Add offset</b> records the clawback here so it nets against the next
                commission run (commission math only — it is never pushed to ReachPro), or <b>Ignore</b>{' '}
                with a note if the payout is genuinely fine. A clawback line added manually to your next
                settlement file still works too and clears the row on import.
              </div>
              <Table
                rowKey="sale_id"
                size="small"
                pagination={false}
                dataSource={active}
                columns={[
                  { title: 'Order ID', dataIndex: 'order_id',
                    render: v => <Text copyable style={{ fontSize: 12 }}>{v}</Text> },
                  { title: 'Purchaser', dataIndex: 'purchaser_names', width: 130, render: v => v || '—' },
                  { title: 'Marketplace', dataIndex: 'marketplace', width: 110 },
                  { title: 'Event', dataIndex: 'event_name' },
                  { title: 'Status', dataIndex: 'fulfillment_status', width: 120,
                    render: v => <Tag color="red">{v}</Tag> },
                  { title: 'First Paid', dataIndex: 'first_paid', width: 100 },
                  { title: 'Unoffset $', dataIndex: 'net_unoffset', width: 110, align: 'right',
                    render: v => <Text strong type="danger">{money(v)}</Text> },
                  { title: 'In Commission', dataIndex: 'any_in_commission_batch', width: 110, align: 'center',
                    render: v => v ? <Tag color="orange">Yes</Tag> : <Tag>Not yet</Tag> },
                  { title: 'Resolve', key: 'resolve', width: 190,
                    render: (_, row) => (
                      <Space size={4}>
                        <Button size="small" type="primary" onClick={() => openOffset(row)}>Add offset</Button>
                        <Button size="small" onClick={() => openIgnore(row)}>Ignore</Button>
                      </Space>
                    ) },
                ]}
              />
            </>
          }
        />
      )}
      {ignored.length > 0 && (
        <Collapse
          size="small"
          style={{ marginBottom: 16 }}
          items={[{
            key: 'ignored',
            label: `${ignored.length} ignored cancelled-sale payment${ignored.length > 1 ? 's' : ''} (marked OK)`,
            children: (
              <Table
                rowKey="sale_id"
                size="small"
                pagination={false}
                dataSource={ignored}
                columns={[
                  { title: 'Order ID', dataIndex: 'order_id',
                    render: v => <Text copyable style={{ fontSize: 12 }}>{v}</Text> },
                  { title: 'Purchaser', dataIndex: 'purchaser_names', width: 130, render: v => v || '—' },
                  { title: 'Event', dataIndex: 'event_name' },
                  { title: 'Unoffset $', dataIndex: 'net_unoffset', width: 110, align: 'right',
                    render: v => <Text>{money(v)}</Text> },
                  { title: 'Note', dataIndex: 'offset_resolution_note' },
                  { title: 'Ignored On', dataIndex: 'offset_resolved_at', width: 110,
                    render: v => v ? dayjs(v).format('YYYY-MM-DD') : '' },
                  { title: '', key: 'undo', width: 90,
                    render: (_, row) => <Button size="small" onClick={() => unignore(row)}>Un-ignore</Button> },
                ]}
              />
            ),
          }]}
        />
      )}

      <Modal
        title={ignoreTarget ? `Ignore ${ignoreTarget.order_id}` : ''}
        open={!!ignoreTarget}
        onOk={submitIgnore}
        okText="Mark as OK"
        confirmLoading={saving}
        onCancel={() => setIgnoreTarget(null)}
      >
        <div style={{ marginBottom: 8 }}>
          This removes the sale from the hunt list without an offset — the{' '}
          {ignoreTarget && <Text strong>{money(ignoreTarget.net_unoffset)}</Text>} payout stays in
          commission as-is. Say why that's OK:
        </div>
        <Input.TextArea
          rows={3}
          value={ignoreNote}
          onChange={e => setIgnoreNote(e.target.value)}
          placeholder="e.g. Marketplace confirmed no clawback is coming; we keep the payout"
        />
      </Modal>

      <Modal
        title={offsetTarget ? `Add offset for ${offsetTarget.order_id}` : ''}
        open={!!offsetTarget}
        onOk={submitOffset}
        okText="Record offset"
        confirmLoading={saving}
        onCancel={() => setOffsetTarget(null)}
      >
        <div style={{ marginBottom: 12 }}>
          Records a negative Adjustment on this sale so the clawback nets against commission in the
          payout window covering the date below. It shows up in the Adjustments table (treatment
          defaults to Full Amount Minus Cost, editable there) but is <b>never pushed to ReachPro</b> —
          add a line to the next settlement file if ReachPro itself must show the clawback.
        </div>
        <Space direction="vertical" style={{ width: '100%' }}>
          <div>
            <Text type="secondary">Offset amount (negative)</Text>
            <InputNumber
              style={{ width: '100%' }}
              value={offsetAmount}
              onChange={setOffsetAmount}
              step={0.01}
              prefix="$"
            />
            {offsetTarget && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                Unoffset amount is {money(offsetTarget.net_unoffset)}; enter more negative if there's a penalty.
              </Text>
            )}
          </div>
          <div>
            <Text type="secondary">Payment date (decides which commission window it lands in)</Text>
            <DatePicker style={{ width: '100%' }} value={offsetDate} onChange={setOffsetDate} />
          </div>
          <div>
            <Text type="secondary">Note (optional)</Text>
            <Input.TextArea
              rows={2}
              value={offsetNote}
              onChange={e => setOffsetNote(e.target.value)}
              placeholder="e.g. Clawback per Lysted statement 8/20"
            />
          </div>
        </Space>
      </Modal>
    </>
  );
}

export default function Adjustments() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [options, setOptions] = useState({ purchasers: [], marketplaces: [] });
  const [savingId, setSavingId] = useState(null);
  const [notesDraft, setNotesDraft] = useState({});

  const [filters, setFilters] = useState({
    purchaser_id: undefined,
    marketplace_id: undefined,
    treatment: undefined,
    date_from: undefined,
    date_to: undefined,
  });

  const load = useCallback(async (currentFilters = filters) => {
    setLoading(true);
    try {
      const params = {};
      if (currentFilters.purchaser_id != null) params.purchaser_id = currentFilters.purchaser_id;
      if (currentFilters.marketplace_id != null) params.marketplace_id = currentFilters.marketplace_id;
      if (currentFilters.treatment != null) params.treatment = currentFilters.treatment;
      if (currentFilters.date_from) params.date_from = currentFilters.date_from;
      if (currentFilters.date_to) params.date_to = currentFilters.date_to;
      const { data } = await getAdjustments(params);
      setRows(data);
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    getAdjustmentFilterOptions().then(({ data }) => setOptions(data));
    load(filters);
  }, []);

  const setFilter = (key, val) => {
    const next = { ...filters, [key]: val };
    setFilters(next);
    load(next);
  };

  const resetFilters = () => {
    const next = { purchaser_id: undefined, marketplace_id: undefined, treatment: undefined, date_from: undefined, date_to: undefined };
    setFilters(next);
    load(next);
  };

  const handleTreatmentChange = async (payoutId, treatment) => {
    setSavingId(payoutId);
    try {
      await updateAdjustmentTreatment(payoutId, treatment);
      // Selecting a treatment does not lock it — locking only happens once
      // Generate Payouts actually consumes the row into a commission payout.
      setRows(prev => prev.map(r => r.payout_id === payoutId ? { ...r, adjustment_commission_treatment: treatment } : r));
      message.success('Commission treatment saved');
    } catch (e) {
      message.error(e?.response?.data?.detail || e?.message || 'Failed to save treatment');
    } finally {
      setSavingId(null);
    }
  };

  const handleReasonChange = async (payoutId, reason) => {
    setSavingId(payoutId);
    try {
      const { data } = await updateAdjustmentReason(payoutId, reason);
      setRows(prev => prev.map(r => r.payout_id === payoutId
        ? {
            ...r,
            adjustment_reason: reason,
            // Cancelled Event defaults the commission treatment server-side -
            // mirror that here so the Treatment column reflects it immediately.
            adjustment_commission_treatment: data.adjustment_commission_treatment || r.adjustment_commission_treatment,
          }
        : r));
      message.success('Adjustment reason saved');
    } catch (e) {
      message.error(e?.response?.data?.detail || e?.message || 'Failed to save reason');
    } finally {
      setSavingId(null);
    }
  };

  const handleSaveNotes = async (payoutId) => {
    const notes = notesDraft[payoutId];
    if (notes == null) return;
    setSavingId(payoutId);
    try {
      await updateAdjustmentNotes(payoutId, notes);
      setRows(prev => prev.map(r => r.payout_id === payoutId ? { ...r, adjustment_review_notes: notes } : r));
      message.success('Notes saved');
    } catch (e) {
      message.error(e?.response?.data?.detail || e?.message || 'Failed to save notes');
    } finally {
      setSavingId(null);
    }
  };

  const totalAmount = rows.reduce((sum, r) => sum + Number(r.amount), 0);
  const unsetCount = rows.filter(r => !r.adjustment_commission_treatment).length;
  const lockedCount = rows.filter(r => r.locked).length;

  const columns = [
    { title: 'Marketplace', dataIndex: 'marketplace_name', width: 110, fixed: 'left',
      render: v => <Tag color="blue">{v}</Tag> },
    { title: 'Order ID', dataIndex: 'marketplace_order_id', width: 130, ellipsis: true },
    { title: 'Event', dataIndex: 'event_name', width: 180, ellipsis: true,
      render: (v, r) => <Tooltip title={r.venue}>{v || '—'}</Tooltip> },
    { title: 'Event Date', dataIndex: 'event_date', width: 100,
      render: v => v ? dayjs(v).format('MM/DD/YYYY') : '—' },
    { title: 'Purchaser(s)', dataIndex: 'purchaser_names', width: 150, ellipsis: true,
      render: v => v || <Text type="secondary">—</Text> },
    { title: 'Amount', dataIndex: 'amount', width: 110, align: 'right',
      render: v => {
        const n = Number(v);
        return <Text type={n < 0 ? 'danger' : undefined}>{money(n)}</Text>;
      } },
    {
      title: 'Adjustment Reason', dataIndex: 'adjustment_reason', width: 210,
      render: (v, r) => {
        const needsNote = REASONS_REQUIRING_NOTE.includes(v) && !(r.adjustment_review_notes || '').trim();
        return (
          <Tooltip title={needsNote ? 'This reason requires a note - add one in the Notes column.' : undefined}>
            <Select
              size="small" style={{ width: 190 }}
              value={v || undefined}
              placeholder="Not yet set"
              status={needsNote ? 'error' : undefined}
              disabled={r.locked || savingId === r.payout_id}
              onChange={(val) => handleReasonChange(r.payout_id, val)}
            >
              {REASON_OPTIONS.map(o => <Option key={o.value} value={o.value}>{o.label}</Option>)}
            </Select>
          </Tooltip>
        );
      },
    },
    {
      title: 'Commission Treatment', dataIndex: 'adjustment_commission_treatment', width: 210,
      render: (v, r) => (
        <Select
          size="small" style={{ width: 190 }}
          value={v || undefined}
          placeholder="Not yet reviewed"
          disabled={r.locked || savingId === r.payout_id}
          onChange={(val) => handleTreatmentChange(r.payout_id, val)}
        >
          {TREATMENT_OPTIONS.map(o => <Option key={o.value} value={o.value}>{o.label}</Option>)}
        </Select>
      ),
    },
    {
      title: 'Locked', dataIndex: 'locked', width: 80, align: 'center',
      render: v => v ? <Tooltip title="Already counted in a commission payout — treatment can no longer change"><LockOutlined /></Tooltip> : null,
    },
    {
      title: 'Notes', dataIndex: 'adjustment_review_notes', width: 260,
      render: (v, r) => {
        const draft = notesDraft[r.payout_id] ?? v ?? '';
        const changed = notesDraft[r.payout_id] != null && notesDraft[r.payout_id] !== (v ?? '');
        return (
          <Space.Compact style={{ width: '100%' }}>
            <Input
              size="small"
              value={draft}
              placeholder="Add a note..."
              onChange={(e) => setNotesDraft(prev => ({ ...prev, [r.payout_id]: e.target.value }))}
              onPressEnter={() => handleSaveNotes(r.payout_id)}
            />
            {changed && (
              <Button size="small" type="primary" loading={savingId === r.payout_id} onClick={() => handleSaveNotes(r.payout_id)}>
                Save
              </Button>
            )}
          </Space.Compact>
        );
      },
    },
    { title: 'Qty', dataIndex: 'quantity', width: 60, align: 'right',
      render: v => v ?? <Text type="secondary">—</Text> },
    { title: 'Proceeds', dataIndex: 'proceeds', width: 110, align: 'right',
      render: v => v != null ? money(v) : <Text type="secondary">—</Text> },
    { title: 'Cost', dataIndex: 'cost', width: 90, align: 'right', render: v => money(v) },
    { title: 'Sale P&L', dataIndex: 'sale_pnl', width: 100, align: 'right',
      render: v => {
        if (v == null) return <Text type="secondary">—</Text>;
        const n = Number(v);
        return <Text type={n < 0 ? 'danger' : undefined}>{money(n)}</Text>;
      } },
    { title: 'Fulfillment Status', dataIndex: 'fulfillment_status', width: 130,
      render: v => v || <Text type="secondary">—</Text> },
    { title: 'Commission Status', dataIndex: 'commission_status', width: 140,
      render: v => v || <Text type="secondary">—</Text> },
    { title: 'Payment Date', dataIndex: 'effective_payment_date', width: 110,
      render: v => v ? dayjs(v).format('MM/DD/YYYY') : <Text type="secondary">—</Text> },
    { title: 'Source File', dataIndex: 'source_file', width: 200, ellipsis: true,
      render: v => <Tooltip title={v}><Text style={{ fontSize: 12 }}>{v}</Text></Tooltip> },
  ];

  return (
    <div style={{ width: '100%' }}>
      <Title level={3}>Review Adjustments</Title>
      <Text type="secondary">
        All payouts labeled as an Adjustment (chargebacks, credits, corrections), linked to their sale.
        Choose how each should count toward commission — once a treatment is used in a generated commission
        payout, it locks and can't be changed. Notes can always be edited.
      </Text>

      <div style={{ marginTop: 16 }}>
        <NeedsOffsetCard onOffsetAdded={() => load(filters)} />
      </div>

      <Row gutter={16} style={{ margin: '16px 0' }}>
        <Col span={6}><Card><Statistic title="Total Rows" value={rows.length} /></Card></Col>
        <Col span={6}><Card><Statistic title="Total Amount" value={totalAmount} precision={2} prefix="$" /></Card></Col>
        <Col span={6}><Card><Statistic title="Not Yet Reviewed" value={unsetCount} valueStyle={{ color: unsetCount ? '#faad14' : undefined }} /></Card></Col>
        <Col span={6}><Card><Statistic title="Locked (Already Counted)" value={lockedCount} /></Card></Col>
      </Row>

      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            allowClear placeholder="Purchaser" style={{ width: 180 }}
            value={filters.purchaser_id}
            onChange={v => setFilter('purchaser_id', v)}
            showSearch optionFilterProp="children"
          >
            {options.purchasers.map(p => <Option key={p.id} value={p.id}>{p.name}</Option>)}
          </Select>

          <Select
            allowClear placeholder="Marketplace" style={{ width: 160 }}
            value={filters.marketplace_id}
            onChange={v => setFilter('marketplace_id', v)}
          >
            {options.marketplaces.map(m => <Option key={m.id} value={m.id}>{m.name}</Option>)}
          </Select>

          <Select
            allowClear placeholder="Treatment" style={{ width: 180 }}
            value={filters.treatment}
            onChange={v => setFilter('treatment', v)}
          >
            <Option value="unset">Not Yet Reviewed</Option>
            {TREATMENT_OPTIONS.map(o => <Option key={o.value} value={o.value}>{o.label}</Option>)}
          </Select>

          <RangePicker
            value={filters.date_from && filters.date_to ? [dayjs(filters.date_from), dayjs(filters.date_to)] : null}
            onChange={(range) => {
              const next = {
                ...filters,
                date_from: range ? range[0].format('YYYY-MM-DD') : undefined,
                date_to: range ? range[1].format('YYYY-MM-DD') : undefined,
              };
              setFilters(next);
              load(next);
            }}
          />

          <Button icon={<ReloadOutlined />} onClick={resetFilters}>Reset</Button>
        </Space>
      </Card>

      <Table
        dataSource={rows}
        columns={columns}
        rowKey="payout_id"
        loading={loading}
        scroll={{ x: 2480 }}
        size="small"
        pagination={{ pageSize: 50, showTotal: (t) => `${t.toLocaleString()} adjustments` }}
      />
    </div>
  );
}
