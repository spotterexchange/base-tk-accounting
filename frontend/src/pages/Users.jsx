import { useEffect, useState } from 'react';
import {
  Table, Button, Tag, Space, Typography, Alert, Input, Select,
  Popconfirm, Modal, message,
} from 'antd';
import { PlusOutlined, KeyOutlined, UserAddOutlined } from '@ant-design/icons';
import { getUsers, createUser, updateUser, resetUserPassword } from '../api';

const { Title, Text } = Typography;

function generatePassword() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789';
  let pw = '';
  const buf = new Uint32Array(14);
  crypto.getRandomValues(buf);
  for (const n of buf) pw += chars[n % chars.length];
  return pw;
}

export default function Users() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [forbidden, setForbidden] = useState(false);

  // Add form
  const [newEmail, setNewEmail] = useState('');
  const [newName, setNewName] = useState('');
  const [newRole, setNewRole] = useState('user');
  const [newPassword, setNewPassword] = useState('');
  const [adding, setAdding] = useState(false);

  // Password reset modal
  const [resetTarget, setResetTarget] = useState(null);
  const [resetPw, setResetPw] = useState('');
  const [resetting, setResetting] = useState(false);

  const load = () => {
    setLoading(true);
    getUsers()
      .then(({ data }) => setUsers(data))
      .catch(e => {
        if (e?.response?.status === 403) setForbidden(true);
        else setError('Failed to load users');
      })
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const handleAdd = async () => {
    setAdding(true);
    setError(null);
    try {
      await createUser({
        email: newEmail.trim(), name: newName.trim(),
        password: newPassword, role: newRole,
      });
      message.success(`User ${newEmail.trim()} created`);
      setNewEmail(''); setNewName(''); setNewPassword(''); setNewRole('user');
      load();
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to create user');
    } finally {
      setAdding(false);
    }
  };

  const handleUpdate = async (userId, patch) => {
    setError(null);
    try {
      const { data } = await updateUser(userId, patch);
      setUsers(prev => prev.map(u => (u.user_id === userId ? data : u)));
    } catch (e) {
      setError(e?.response?.data?.detail || 'Update failed');
    }
  };

  const handleReset = async () => {
    setResetting(true);
    setError(null);
    try {
      await resetUserPassword(resetTarget.user_id, resetPw);
      message.success(`Password reset for ${resetTarget.email}`);
      setResetTarget(null);
      setResetPw('');
    } catch (e) {
      setError(e?.response?.data?.detail || 'Password reset failed');
    } finally {
      setResetting(false);
    }
  };

  if (forbidden) {
    return (
      <Alert type="warning" showIcon
        message="Admin access required"
        description="Your account doesn't have the admin role. Ask an admin to manage users or to grant you access." />
    );
  }

  const columns = [
    { title: 'Email', dataIndex: 'email' },
    { title: 'Name', dataIndex: 'name',
      render: (v, row) => (
        <Text editable={{ onChange: val => { if (val.trim() && val !== row.name) handleUpdate(row.user_id, { name: val }); } }}>
          {v}
        </Text>
      ) },
    { title: 'Role', dataIndex: 'role', width: 130,
      render: (v, row) => (
        <Select size="small" value={v} style={{ width: 100 }}
          onChange={val => handleUpdate(row.user_id, { role: val })}
          options={[{ value: 'user', label: 'user' }, { value: 'admin', label: 'admin' }]} />
      ) },
    { title: 'Status', dataIndex: 'is_active', width: 110, align: 'center',
      render: v => v ? <Tag color="green">Active</Tag> : <Tag color="red">Disabled</Tag> },
    { title: 'Created', dataIndex: 'created_at', width: 120,
      render: v => (v || '').slice(0, 10) },
    { title: 'Actions', key: 'actions', width: 250,
      render: (_, row) => (
        <Space>
          <Button size="small" icon={<KeyOutlined />}
            onClick={() => { setResetTarget(row); setResetPw(generatePassword()); }}>
            Reset Password
          </Button>
          {row.is_active ? (
            <Popconfirm title={`Disable ${row.email}? They won't be able to log in.`}
              onConfirm={() => handleUpdate(row.user_id, { is_active: false })}
              okText="Disable" okType="danger">
              <Button size="small" danger>Disable</Button>
            </Popconfirm>
          ) : (
            <Button size="small" onClick={() => handleUpdate(row.user_id, { is_active: true })}>
              Enable
            </Button>
          )}
        </Space>
      ) },
  ];

  const canAdd = newEmail.trim().includes('@') && newName.trim() && newPassword.length >= 8;

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      <Title level={3}>Users</Title>
      <Text type="secondary">
        Logins for the accounting dashboard. Admins can manage users and see this page;
        passwords are shown only when set — store them in a password manager.
      </Text>

      {error && (
        <Alert type="error" message={error} closable onClose={() => setError(null)}
          style={{ margin: '16px 0' }} />
      )}

      <Table
        rowKey="user_id"
        dataSource={users}
        columns={columns}
        loading={loading}
        pagination={false}
        size="small"
        style={{ margin: '16px 0' }}
      />

      <Title level={5}><UserAddOutlined /> Add User</Title>
      <Space wrap align="start">
        <Input placeholder="email@company.com" value={newEmail}
          onChange={e => setNewEmail(e.target.value)} style={{ width: 220 }} />
        <Input placeholder="Full name" value={newName}
          onChange={e => setNewName(e.target.value)} style={{ width: 160 }} />
        <Select value={newRole} onChange={setNewRole} style={{ width: 100 }}
          options={[{ value: 'user', label: 'user' }, { value: 'admin', label: 'admin' }]} />
        <Space.Compact>
          <Input placeholder="Password (min 8 chars)" value={newPassword}
            onChange={e => setNewPassword(e.target.value)} style={{ width: 220 }} />
          <Button onClick={() => setNewPassword(generatePassword())}>Generate</Button>
        </Space.Compact>
        <Button type="primary" icon={<PlusOutlined />} loading={adding}
          disabled={!canAdd} onClick={handleAdd}>
          Add User
        </Button>
      </Space>

      <Modal
        title={resetTarget ? `Reset password — ${resetTarget.email}` : ''}
        open={!!resetTarget}
        onCancel={() => { setResetTarget(null); setResetPw(''); }}
        onOk={handleReset}
        okText="Reset Password"
        confirmLoading={resetting}
        okButtonProps={{ disabled: resetPw.length < 8 }}
      >
        <Text type="secondary">
          Set the new password below (a generated one is pre-filled). Copy it before
          confirming — it won't be shown again.
        </Text>
        <Space.Compact style={{ width: '100%', marginTop: 12 }}>
          <Input value={resetPw} onChange={e => setResetPw(e.target.value)} />
          <Button onClick={() => setResetPw(generatePassword())}>Generate</Button>
          <Button onClick={() => { navigator.clipboard.writeText(resetPw); message.success('Copied'); }}>
            Copy
          </Button>
        </Space.Compact>
      </Modal>
    </div>
  );
}
