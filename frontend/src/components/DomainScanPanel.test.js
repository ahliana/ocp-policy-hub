import { render, screen, act } from '@testing-library/react';
import DomainScanPanel from './DomainScanPanel';

// RegionSelector fetches /api/groups etc. on mount - stub fetch so it
// resolves quietly to an empty tree; these tests only care about the
// scan-scope summary line, which is driven by props, not RegionSelector.
beforeEach(() => {
  global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({}) }));
});

afterEach(() => {
  jest.restoreAllMocks();
});

const BASE_PROPS = {
  selectedRegions: [],
  onSelectionChange: jest.fn(),
  mode: 'standard',
  onModeChange: jest.fn(),
  channels: ['crawl'],
  onChannelsChange: jest.fn(),
  costStatus: 'idle',
  costEstimateText: 'Select a scan target',
  sourceCount: null,
  isBusy: false,
  hasApiKey: true,
  isQueueRunning: false,
  queuedScanCount: 0,
  isScanRequestRunning: false,
  isScanRunning: false,
  onScan: jest.fn(),
  onStop: jest.fn(),
};

async function renderPanel(props = {}) {
  let utils;
  await act(async () => {
    utils = render(<DomainScanPanel {...BASE_PROPS} {...props} />);
  });
  return utils;
}

describe('DomainScanPanel scan-scope summary (WP-6)', () => {
  it('shows "nothing selected" when no scope is chosen', async () => {
    await renderPanel({ selectedRegions: [] });
    expect(screen.getByText(/Scanning: nothing selected - 0 sources/)).toBeInTheDocument();
  });

  it('reflects selected region/group labels, comma-joined', async () => {
    await renderPanel({ selectedRegions: ['group:eu', 'group:quick:region:us'] });
    expect(screen.getByText(/Scanning: EU, United States/)).toBeInTheDocument();
  });

  it('updates when the selection changes (rerender)', async () => {
    const { rerender } = await renderPanel({ selectedRegions: ['group:eu'] });
    expect(screen.getByText(/Scanning: EU -/)).toBeInTheDocument();

    await act(async () => {
      rerender(<DomainScanPanel {...BASE_PROPS} selectedRegions={['group:us']} />);
    });
    expect(screen.getByText(/Scanning: United States -/)).toBeInTheDocument();
    expect(screen.queryByText(/Scanning: EU -/)).not.toBeInTheDocument();
  });

  it('uses the estimate domain_count when available, over the selection-count fallback', async () => {
    await renderPanel({
      selectedRegions: ['group:eu', 'group:us', 'group:uk'],
      sourceCount: 42,
    });
    expect(screen.getByText(/42 sources/)).toBeInTheDocument();
  });

  it('falls back to the number of selected scope entries while the estimate is not ready', async () => {
    await renderPanel({
      selectedRegions: ['group:eu', 'group:us'],
      sourceCount: null,
      costStatus: 'loading',
      costEstimateText: 'Estimating...',
    });
    expect(screen.getByText(/2 sources/)).toBeInTheDocument();
  });

  it('singularizes "1 source"', async () => {
    await renderPanel({ selectedRegions: ['group:eu'], sourceCount: 1 });
    expect(screen.getByText(/1 source(?!s)/)).toBeInTheDocument();
  });

  it('includes the current cost-estimate text in the summary', async () => {
    await renderPanel({
      selectedRegions: ['group:eu'],
      sourceCount: 5,
      costEstimateText: '$1.23 (5 targets)',
    });
    expect(screen.getByText(/^Scanning:.*\$1\.23 \(5 targets\)$/)).toBeInTheDocument();
  });

  it('keeps the summary line adjacent to the Scan button in the DOM', async () => {
    await renderPanel({ selectedRegions: ['group:eu'] });
    const summary = screen.getByText(/^Scanning:/);
    const scanButton = screen.getByRole('button', { name: 'Scan', exact: true });
    expect(summary.nextElementSibling).toContainElement(scanButton);
  });
});
