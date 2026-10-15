// InterviewPanel (/hr/panel) — PH4-O5. Who is carrying the interview load,
// when interviewers are free, and how consistently the panel scores. Three
// read-mostly tabs; the only writes here are a capacity limit and an
// availability window — nothing that touches a candidate's status.
// English-only by design (CLAUDE.md — the HR console is not translated).

import { useState } from 'react';
import { SegTabs } from '@/design/components/primitives';
import { PageHeader, Page } from '@/design/components/page';
import WorkloadTab from '@/components/panel/WorkloadTab';
import AvailabilityTab from '@/components/panel/AvailabilityTab';
import CalibrationTab from '@/components/panel/CalibrationTab';

const TABS = [
  { key: 'workload', label: 'Workload' },
  { key: 'availability', label: 'Availability' },
  { key: 'calibration', label: 'Calibration' },
] as const;

type TabKey = (typeof TABS)[number]['key'];

export default function InterviewPanel(): JSX.Element {
  const [tab, setTab] = useState<TabKey>('workload');

  return (
    <Page width="default">
      <PageHeader
        title="Interview panel"
        description="Who is carrying the interview load, when interviewers are free, and how consistently the panel scores."
      />

      <div className="max-w-full overflow-x-auto">
        <SegTabs tabs={[...TABS]} active={tab} onChange={(k) => setTab(k as TabKey)} />
      </div>

      <div>
        {tab === 'workload' ? <WorkloadTab /> : null}
        {tab === 'availability' ? <AvailabilityTab /> : null}
        {tab === 'calibration' ? <CalibrationTab /> : null}
      </div>
    </Page>
  );
}
