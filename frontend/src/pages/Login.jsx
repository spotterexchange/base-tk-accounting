import { useState } from 'react';
import { Card, Form, Input, Button, Typography, Alert } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { login } from '../api';
import { setAuth } from '../auth';

const { Title, Text } = Typography;

export default function Login({ onLoggedIn }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async ({ email, password }) => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await login(email, password);
      setAuth(data);
      onLoggedIn();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: '#f5f5f5',
    }}>
      <Card style={{ width: 360 }}>
        <Title level={3} style={{ textAlign: 'center', marginBottom: 4 }}>Reachpro Recon</Title>
        <Text type="secondary" style={{ display: 'block', textAlign: 'center', marginBottom: 24 }}>
          Sign in to continue
        </Text>

        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}

        <Form layout="vertical" onFinish={handleSubmit} disabled={loading}>
          <Form.Item name="email" rules={[{ required: true, message: 'Email is required' }]}>
            <Input prefix={<UserOutlined />} placeholder="Email" autoComplete="username" size="large" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: 'Password is required' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="Password" autoComplete="current-password" size="large" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" block size="large" loading={loading}>
              Sign In
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
