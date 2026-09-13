import { useState } from 'react';
import { Tabs } from 'antd';
import GeneratePayouts from './GeneratePayouts';
import PayoutHistory from './PayoutHistory';

/**
 * The purchaser-payout lifecycle in one place: generate a commission batch,
 * then review/commit/roll back past batches. History is remounted on every
 * visit so a payout generated in the other tab always shows up immediately;
 * Generate stays mounted so its wizard state survives a peek at History.
 */
export default function PurchaserPayouts() {
  const [activeKey, setActiveKey] = useState('generate');

  return (
    <Tabs
      activeKey={activeKey}
      onChange={setActiveKey}
      items={[
        { key: 'generate', label: 'Generate', children: <GeneratePayouts /> },
        { key: 'history', label: 'History', children: activeKey === 'history' ? <PayoutHistory /> : null },
      ]}
    />
  );
}
