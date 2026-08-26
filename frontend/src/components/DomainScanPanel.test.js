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
  costEstimate: null,
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

const READY_ESTIMATE = {
  estimated_cost_usd: 6.15,
  estimated_cost_low_usd: 4.2,
  estimated_cost_high_usd: 9.1,
  domain_count: 5,
  auditor_cost_usd: 0.35,
  assumptions: [
    'Assumes 100 pages per government website (measured from recent scans).',
    'Assumes 20% of pages need a full AI read (assumed).',
  ],
  channels: {
    crawl: {
      domain_count: 3, estimated_items_or_pages: 300, screening_calls: 50,
      analysis_calls: 20, cost_usd: 5.0, cost_low_usd: 3.5, cost_high_usd: 7.5,
    },
    law_apis: {
      domain_count: 1, estimated_items_or_pages: 150, screening_calls: 20,
      analysis_calls: 8, cost_usd: 0.9, cost_low_usd: 0.6, cost_high_usd: 1.3,
    },
    transposition: {
      domain_count: 1, estimated_items_or_pages: 50, screening_calls: 5,
      analysis_calls: 2, cost_usd: 0.25, cost_low_usd: 0.1, cost_high_usd: 0.3,
    },
  },
};

describe('DomainScanPanel "Why this price?" cost breakdown (WP-26)', () => {
  it('does not render the breakdown when there is no ready estimate', async () => {
    await renderPanel({ selectedRegions: ['group:eu'], costEstimate: null, costStatus: 'idle' });
    expect(screen.queryByText('Why this price?')).not.toBeInTheDocument();
  });

  it('does not render the breakdown while the estimate is still loading', async () => {
    await renderPanel({
      selectedRegions: ['group:eu'],
      costEstimate: null,
      costStatus: 'loading',
      costEstimateText: 'Estimating...',
    });
    expect(screen.queryByText('Why this price?')).not.toBeInTheDocument();
  });

  it('renders a closed-by-default expander with per-channel lines and assumptions', async () => {
    await renderPanel({
      selectedRegions: ['group:eu'],
      costStatus: 'ready',
      costEstimate: READY_ESTIMATE,
    });

    const summary = screen.getByText('Why this price?');
    const details = summary.closest('details');
    expect(details).toHaveClass('cost-breakdown');
    expect(details).not.toHaveAttribute('open');

    expect(screen.getByText(
      '3 government websites - about 300 pages checked, ~50 get a fast AI pass, '
      + '~20 get a full AI read - $5.00 (range $3.50-$7.50)',
    )).toBeInTheDocument();
    expect(screen.getByText(
      '1 law databases - about 150 entries checked, ~20 get a fast AI pass, '
      + '~8 get a full AI read - $0.90 (range $0.60-$1.30)',
    )).toBeInTheDocument();
    expect(screen.getByText(
      '1 EU law trackers - about 50 entries checked, ~5 get a fast AI pass, '
      + '~2 get a full AI read - $0.25 (range $0.10-$0.30)',
    )).toBeInTheDocument();

    expect(screen.getByText('Report generation: $0.35')).toBeInTheDocument();

    expect(screen.getByText('What we assumed')).toBeInTheDocument();
    expect(screen.getByText('Assumes 100 pages per government website (measured from recent scans).'))
      .toBeInTheDocument();
    expect(screen.getByText('Assumes 20% of pages need a full AI read (assumed).')).toBeInTheDocument();
  });

  it('renders only the channels present in the estimate', async () => {
    const partialEstimate = {
      ...READY_ESTIMATE,
      channels: { crawl: READY_ESTIMATE.channels.crawl },
    };
    await renderPanel({
      selectedRegions: ['group:eu'],
      costStatus: 'ready',
      costEstimate: partialEstimate,
    });

    expect(screen.getByText(/3 government websites/)).toBeInTheDocument();
    expect(screen.queryByText(/law databases/)).not.toBeInTheDocument();
    expect(screen.queryByText(/EU law trackers/)).not.toBeInTheDocument();
  });

  it('does not render the breakdown when the ready estimate has no channels breakdown', async () => {
    await renderPanel({
      selectedRegions: ['group:eu'],
      costStatus: 'ready',
      costEstimate: { estimated_cost_usd: 4.2, domain_count: 5, target_count: 5 },
    });
    expect(screen.queryByText('Why this price?')).not.toBeInTheDocument();
  });

  it('avoids plain-language jargon words in the breakdown copy', async () => {
    const { container } = await renderPanel({
      selectedRegions: ['group:eu'],
      costStatus: 'ready',
      costEstimate: READY_ESTIMATE,
    });
    const text = container.textContent;
    expect(text).not.toMatch(/\bLLM\b/i);
    expect(text).not.toMatch(/\btoken\b/i);
    expect(text).not.toMatch(/\bAPI\b/);
  });
});
