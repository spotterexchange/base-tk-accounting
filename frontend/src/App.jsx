import { useState } from 'react';
import { Layout, Menu, Button, Tooltip } from 'antd';
import { TableOutlined, TeamOutlined, DollarOutlined, AuditOutlined, LogoutOutlined, UserOutlined } from '@ant-design/icons';
import ViewPayouts from './pages/ViewPayouts';
import PayoutPrep from './pages/PayoutPrep';
import PurchaserPayouts from './pages/PurchaserPayouts';
import Purchasers from './pages/Purchasers';
import Users from './pages/Users';
import Login from './pages/Login';
import { getToken, getUserName, getRole, clearAuth } from './auth';
import './App.css';

const { Content, Sider } = Layout;

const menuItems = [
  { key: 'view', icon: <TableOutlined />, label: 'Marketplace Payouts' },
  { key: 'prep', icon: <AuditOutlined />, label: 'Payout Prep' },
  { key: 'payouts', icon: <DollarOutlined />, label: 'Purchaser Payouts' },
];

const PAGES = {
  view: <ViewPayouts />,
  prep: <PayoutPrep />,
  payouts: <PurchaserPayouts />,
  purchasers: <Purchasers />,
  users: <Users />,
};

export default function App() {
  const [page, setPage] = useState('view');
  const [loggedIn, setLoggedIn] = useState(!!getToken());

  if (!loggedIn) {
    return <Login onLoggedIn={() => setLoggedIn(true)} />;
  }

  const handleLogout = () => {
    clearAuth();
    setLoggedIn(false);
  };

  // Purchasers stays visible to everyone (users manage expenses there);
  // the Users page is admin-only, so hide it from regular users entirely.
  // Computed per render so it picks up the role stored at login.
  const adminItems = [
    { key: 'purchasers', icon: <TeamOutlined />, label: 'Purchasers' },
    ...(getRole() === 'admin' ? [{ key: 'users', icon: <UserOutlined />, label: 'Users' }] : []),
  ];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="dark" width={220} style={{ display: 'flex', flexDirection: 'column', height: '100vh', position: 'sticky', top: 0, overflow: 'hidden' }}>
        <div style={{ color: 'white', fontWeight: 'bold', fontSize: 16, padding: '20px 16px 8px' }}>
          Reachpro Recon
        </div>
        <Menu
          theme="dark"
          selectedKeys={[page]}
          items={menuItems}
          onClick={({ key }) => setPage(key)}
        />
        <div style={{ marginTop: 'auto' }}>
          <div style={{ borderTop: '1px solid rgba(255,255,255,0.1)', padding: '8px 16px 4px', color: 'rgba(255,255,255,0.45)', fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Admin
          </div>
          <Menu
            theme="dark"
            selectedKeys={[page]}
            items={adminItems}
            onClick={({ key }) => setPage(key)}
          />
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            borderTop: '1px solid rgba(255,255,255,0.1)', padding: '10px 16px',
          }}>
            <span style={{ color: 'rgba(255,255,255,0.75)', fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {getUserName()}
            </span>
            <Tooltip title="Log out">
              <Button
                type="text" size="small" icon={<LogoutOutlined style={{ color: 'rgba(255,255,255,0.75)' }} />}
                onClick={handleLogout}
              />
            </Tooltip>
          </div>
        </div>
      </Sider>
      <Layout style={{ minWidth: 0 }}>
        <Content style={{ padding: 24, background: '#f5f5f5', minWidth: 0, width: '100%' }}>
          {PAGES[page]}
        </Content>
      </Layout>
    </Layout>
  );
}
