import {
  render, screen, act, fireEvent, waitFor, within,
} from '@testing-library/react';
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

  it('keeps the summary line grouped with the Scan button (WP-29 scan-decision)', async () => {
    await renderPanel({ selectedRegions: ['group:eu'] });
    const summary = screen.getByText(/^Scanning:/);
    const scanButton = screen.getByRole('button', { name: 'Scan', exact: true });
    expect(summary.closest('.scan-decision')).toContainElement(scanButton);
  });
});

const READY_ESTIMATE = {
  estimated_cost_usd: 6.15,
  estimated_cost_low_usd: 4.2,
  estimated_cost_high_usd: 9.1,
  domain_count: 5,
  estimated_pages: 500,
  estimated_screening_calls: 75,
  estimated_analysis_calls: 30,
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

describe('DomainScanPanel cost funnel diagram (WP-38)', () => {
  it('renders the funnel inside a nested, closed-by-default "See it as a picture" note, with the estimate\'s numbers', async () => {
    await renderPanel({
      selectedRegions: ['group:eu'],
      costStatus: 'ready',
      costEstimate: READY_ESTIMATE,
    });

    const nestedSummary = screen.getByText('See it as a picture');
    const nestedDetails = nestedSummary.closest('details');
    expect(nestedDetails).not.toHaveAttribute('open');

    const outerDetails = screen.getByText('Why this price?').closest('details');
    expect(outerDetails).toContainElement(nestedDetails);

    expect(within(nestedDetails).getByRole('img')).toBeInTheDocument();
    expect(within(nestedDetails).getByText('5')).toBeInTheDocument();
    expect(within(nestedDetails).getByText('~500')).toBeInTheDocument();
    expect(within(nestedDetails).getByText('~75')).toBeInTheDocument();
    expect(within(nestedDetails).getByText('~30')).toBeInTheDocument();
  });

  it('has an aria-label summarizing the flow', async () => {
    await renderPanel({
      selectedRegions: ['group:eu'],
      costStatus: 'ready',
      costEstimate: READY_ESTIMATE,
    });

    expect(screen.getByRole('img')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('5 sources'),
    );
  });

  it('is absent when there is no ready estimate', async () => {
    await renderPanel({ selectedRegions: ['group:eu'], costEstimate: null, costStatus: 'idle' });
    expect(screen.queryByText('See it as a picture')).not.toBeInTheDocument();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });
});

describe('DomainScanPanel scan-decision grouping (WP-29)', () => {
  it('groups the scope summary, both HelpNotes, and the Scan/Stop buttons under one container', async () => {
    await renderPanel({
      selectedRegions: ['group:eu'],
      costStatus: 'ready',
      costEstimate: READY_ESTIMATE,
    });

    const group = screen.getByText(/^Scanning:/).closest('.scan-decision');
    expect(group).not.toBeNull();
    expect(group).toContainElement(screen.getByText('Why this price?'));
    expect(group).toContainElement(screen.getByText('Where will this search?'));
    expect(group).toContainElement(screen.getByRole('button', { name: 'Scan', exact: true }));
    expect(group).toContainElement(screen.getByRole('button', { name: 'Stop scan' }));
  });

  it('the "Where will this search?" note is closed by default', async () => {
    await renderPanel({ selectedRegions: ['group:eu'] });
    const details = screen.getByText('Where will this search?').closest('details');
    expect(details).not.toHaveAttribute('open');
  });
});

describe('DomainScanPanel "Which depth should I pick?" help note (WP-30a)', () => {
  it('renders beside the mode cards, closed by default', async () => {
    await renderPanel();
    const summary = screen.getByText('Which depth should I pick?');
    const details = summary.closest('details');
    expect(details).toHaveClass('help-note');
    expect(details).not.toHaveAttribute('open');
  });
});

describe('DomainScanPanel channel-name hotspots (WP-30b)', () => {
  it('attaches an InfoHotspot to each channel line in the "Why this price?" breakdown', async () => {
    await renderPanel({
      selectedRegions: ['group:eu'],
      costStatus: 'ready',
      costEstimate: READY_ESTIMATE,
    });

    expect(screen.getByRole('button', { name: 'More about government websites' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'More about law databases' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'More about EU law trackers' })).toBeInTheDocument();
  });
});

describe('DomainScanPanel scope preview - "Where will this search?" (WP-28)', () => {
  const GERMANY_DOMAINS = [
    {
      id: 'gesetze_enefg',
      name: 'Germany Federal Law Database - EnEfG',
      region: ['eu', 'eu_central', 'germany'],
      source_type: 'crawl',
    },
    {
      id: 'legiscan_api',
      name: 'LegiScan API (US state legislation)',
      region: ['us'],
      source_type: 'legiscan',
    },
  ];

  function mockFetchWithDomains() {
    return jest.fn(async (url) => {
      const parsed = new URL(String(url));
      if (parsed.pathname === '/api/domains' && parsed.searchParams.get('group') === 'germany') {
        return { ok: true, json: async () => ({ domains: GERMANY_DOMAINS }) };
      }
      return { ok: true, json: async () => ({}) };
    });
  }

  it('does not fetch the source list before the note is opened', async () => {
    const fetchMock = mockFetchWithDomains();
    global.fetch = fetchMock;
    await renderPanel({ selectedRegions: ['group:germany'] });

    const domainCalls = fetchMock.mock.calls.filter(
      ([url]) => new URL(String(url)).pathname === '/api/domains',
    );
    expect(domainCalls).toHaveLength(0);
  });

  it('fetches the resolved source list lazily, once the note is opened', async () => {
    const fetchMock = mockFetchWithDomains();
    global.fetch = fetchMock;
    await renderPanel({ selectedRegions: ['group:germany'] });

    fireEvent.click(screen.getByText('Where will this search?'));

    await waitFor(() => {
      const domainCalls = fetchMock.mock.calls.filter(
        ([url]) => new URL(String(url)).pathname === '/api/domains',
      );
      expect(domainCalls.length).toBeGreaterThan(0);
    });
  });

  it('groups resolved sources under plain-language channel headings with per-group and total counts', async () => {
    global.fetch = mockFetchWithDomains();
    await renderPanel({ selectedRegions: ['group:germany'] });

    fireEvent.click(screen.getByText('Where will this search?'));

    expect(await screen.findByText('Government websites (1)')).toBeInTheDocument();
    expect(screen.getByText('Germany Federal Law Database - EnEfG - Germany')).toBeInTheDocument();
    expect(screen.getByText('Law databases (1)')).toBeInTheDocument();
    expect(screen.getByText('LegiScan API (US state legislation) - United States')).toBeInTheDocument();
    expect(screen.getByText('2 sources total')).toBeInTheDocument();
  });

  it('attaches an InfoHotspot to each channel-name group heading (WP-30b)', async () => {
    global.fetch = mockFetchWithDomains();
    await renderPanel({ selectedRegions: ['group:germany'] });

    fireEvent.click(screen.getByText('Where will this search?'));

    expect(await screen.findByText('Government websites (1)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'More about Government websites' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'More about Law databases' })).toBeInTheDocument();
  });

  it('the total agrees with the cost estimate\'s domain_count when both are present', async () => {
    global.fetch = mockFetchWithDomains();
    await renderPanel({
      selectedRegions: ['group:germany'],
      costStatus: 'ready',
      costEstimate: { estimated_cost_usd: 1, domain_count: 2, target_count: 2 },
    });

    fireEvent.click(screen.getByText('Where will this search?'));

    const totalLine = await screen.findByText('2 sources total');
    expect(totalLine).toBeInTheDocument();
  });

  it('shows "Pick a place or sources first." when nothing is selected', async () => {
    global.fetch = mockFetchWithDomains();
    await renderPanel({ selectedRegions: [] });

    fireEvent.click(screen.getByText('Where will this search?'));

    expect(await screen.findByText('Pick a place or sources first.')).toBeInTheDocument();
  });
});
