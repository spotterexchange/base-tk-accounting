import { Tabs } from 'antd';
import Adjustments from './Adjustments';
import SplitSales from './SplitSales';

/**
 * Pre-generation review: both tabs gate the accuracy of Generate Payouts -
 * adjustments need a reason/commission treatment, and multi-purchaser sales
 * need their splits confirmed.
 */
export default function PayoutPrep() {
  return (
    <Tabs
      defaultActiveKey="adjustments"
      items={[
        { key: 'adjustments', label: 'Adjustments', children: <Adjustments /> },
        { key: 'splits', label: 'Splits', children: <SplitSales /> },
      ]}
    />
  );
}
