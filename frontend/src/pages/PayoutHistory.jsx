import { useState, useEffect, useCallback } from 'react';
import { Table, Tag, Button, Space, Typography, Popconfirm, message, Checkbox } from 'antd';
import { DownloadOutlined, CheckOutlined, RollbackOutlined } from '@ant-design/icons';
import {
  getPurchaserPayouts,
  downloadPurchaserPayout,
  commitPurchaserPayout,
  rollbackPurchaserPayout,
} from '../api';

const { Title } = Typography;
const fmt = (n) => n == null ? '—' : `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const STATUS_COLOR = {
  pending_review: 'orange',
  committed: 'green',
  rolled_back: 'default',
};
const STATUS_LABEL = {
  pending_review: 'Pending Review',
  committed: 'Committed',
  rolled_back: 'Rolled Back',
};

export default function PayoutHistory() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState({});
  const [showRolledBack, setShowRolledBack] = useState(false);

  const load = useCallback(async (includeRolledBack) => {
    setLoading(true);
    try {
      const { data } = await getPurchaserPayouts(includeRolledBack);
      setRows(data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(showRolledBack); }, [load, showRolledBack]);

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
      message.error('Download failed');
    }
  };

  const handleAction = async (id, action) => {
    setActionLoading(prev => ({ ...prev, [id]: action }));
    try {
      if (action === 'commit') await commitPurchaserPayout(id);
      else await rollbackPurchaserPayout(id);
      message.success(action === 'commit' ? 'Payout committed' : 'Payout rolled back');
      load(showRolledBack);
    } catch (e) {
      message.error(e?.response?.data?.detail || `${action} failed`);
    } finally {
      setActionLoading(prev => { const n = { ...prev }; delete n[id]; return n; });
    }
  };

  const columns = [
    {
      title: 'Purchaser',
      dataIndex: 'purchaser_name',
      sorter: (a, b) => a.purchaser_name.localeCompare(b.purchaser_name),
    },
    {
      title: 'Payout Date',
      dataIndex: 'payout_date',
      width: 120,
      sorter: (a, b) => (a.payout_date || '').localeCompare(b.payout_date || ''),
    },
    {
      title: 'Lines',
      dataIndex: 'line_count',
      width: 80,
      align: 'right',
    },
    {
      title: 'Gross P&L',
      dataIndex: 'gross_pnl',
      width: 140,
      align: 'right',
      render: fmt,
    },
    {
      title: 'Commission',
      dataIndex: 'commission_amount',
      width: 120,
      align: 'right',
      render: fmt,
    },
    {
      title: 'Expenses',
      dataIndex: 'expenses_total',
      width: 110,
      align: 'right',
      render: v => Number(v) > 0
        ? <Typography.Text type="danger">−{fmt(v)}</Typography.Text>
        : <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: 'Net Commission',
      dataIndex: 'net_commission',
      width: 140,
      align: 'right',
      render: (v, row) => <Typography.Text strong style={{ color: '#52c41a' }}>{fmt(v ?? row.commission_amount)}</Typography.Text>,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 140,
      render: s => <Tag color={STATUS_COLOR[s] || 'default'}>{STATUS_LABEL[s] || s}</Tag>,
      filters: Object.entries(STATUS_LABEL).map(([v, t]) => ({ text: t, value: v })),
      onFilter: (val, record) => record.status === val,
    },
    {
      title: 'Generated',
      dataIndex: 'created_at',
      width: 160,
      render: v => v ? new Date(v).toLocaleString() : '—',
      sorter: (a, b) => (a.created_at || '').localeCompare(b.created_at || ''),
      defaultSortOrder: 'descend',
    },
    {
      title: 'Actions',
      width: 260,
      render: (_, record) => {
        const id = record.purchaser_payout_id;
        const busy = actionLoading[id];
        const isPending = record.status === 'pending_review';
        const isCommitted = record.status === 'committed';
        return (
          <Space>
            <Button
              size="small"
              icon={<DownloadOutlined />}
              onClick={() => handleDownload(record)}
              disabled={record.status === 'rolled_back'}
            >
              Download
            </Button>
            {isPending && (
              <Popconfirm
                title="Commit this payout?"
                description="This marks it as final. You will not be able to roll it back after."
                onConfirm={() => handleAction(id, 'commit')}
                okText="Commit"
                okType="primary"
              >
                <Button
                  size="small"
                  type="primary"
                  icon={<CheckOutlined />}
                  loading={busy === 'commit'}
                >
                  Commit
                </Button>
              </Popconfirm>
            )}
            {isPending && (
              <Popconfirm
                title="Roll back this payout?"
                description="This will unlock all payout rows so they can be re-processed."
                onConfirm={() => handleAction(id, 'rollback')}
                okText="Roll Back"
                okType="danger"
              >
                <Button
                  size="small"
                  danger
                  icon={<RollbackOutlined />}
                  loading={busy === 'rollback'}
                >
                  Roll Back
                </Button>
              </Popconfirm>
            )}
            {isCommitted && (
              <Tag color="green" style={{ marginLeft: 4 }}>Final</Tag>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <div style={{ width: '100%' }}>
      <Title level={3}>Payout History</Title>
      <Checkbox
        checked={showRolledBack}
        onChange={e => setShowRolledBack(e.target.checked)}
        style={{ marginBottom: 12 }}
      >
        Show rolled back payouts
      </Checkbox>
      <Table
        rowKey="purchaser_payout_id"
        dataSource={rows}
        columns={columns}
        loading={loading}
        pagination={{ pageSize: 50 }}
        size="small"
        rowClassName={record => record.status === 'rolled_back' ? 'row-muted' : ''}
      />
    </div>
  );
}
