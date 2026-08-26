import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import CostPlanner, { buildPlainTextTable } from './CostPlanner';
import { setAdminToken } from '../utils/adminAuth';

const GROUPS_RESPONSE = {
  quick: 'Quick scan',
  eu: 'European Union',
};

const PROJECTION_A = {
  items: [
    {
      group: 'quick',
      estimate_usd: 2.0,
      history: { runs: 3, mean_cost_usd: 2.5, last_cost_usd: 3.0 },
      per_month_usd: 10.83, // distinct from estimate/mean so row assertions are unambiguous
    },
  ],
  cadence: 'monthly',
  total_per_month_usd: 10.83,
};

const PROJECTION_B = {
  items: [
    {
      group: 'eu',
      estimate_usd: 4.0,
      history: null,
      per_month_usd: 4.0,
    },
  ],
  cadence: 'monthly',
  total_per_month_usd: 4.0,
};

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function mockFetch({ projectionByGroups = {}, scansHistory = { scans: [] } } = {}) {
  return jest.fn(async (url) => {
    const parsed = new URL(String(url));
    if (parsed.pathname.endsWith('/api/groups')) {
      return jsonResponse(200, GROUPS_RESPONSE);
    }
    if (parsed.pathname.endsWith('/api/cost-projection')) {
      const groups = parsed.searchParams.get('groups');
      const projection = projectionByGroups[groups];
      return projection ? jsonResponse(200, projection) : jsonResponse(400, { detail: 'Unknown group' });
    }
    if (parsed.pathname.endsWith('/api/scans/history')) {
      return jsonResponse(200, scansHistory);
    }
    return jsonResponse(404, {});
  });
}

// jsdom's <select> nodes are real DOM elements (unlike the synthetic event
// target), so selecting an option is: mark the matching real <option> node
// selected, then dispatch a change event on the <select> so React reads the
// now-accurate event.target.selectedOptions.
function selectOption(selectElement, value) {
  const option = Array.from(selectElement.options).find((o) => o.value === value);
  option.selected = true;
  fireEvent.change(selectElement);
}

async function selectScopeA(groupId) {
  const select = screen.getByLabelText('Scope groups (Scenario A)');
  selectOption(select, groupId);
}

beforeEach(() => {
  global.navigator.clipboard = { writeText: jest.fn(async () => {}) };
});

afterEach(() => {
  jest.restoreAllMocks();
  setAdminToken('');
});

describe('CostPlanner fetching', () => {
  it('fetches /api/groups on mount for the scope options', async () => {
    global.fetch = mockFetch();
    render(<CostPlanner />);

    await waitFor(() => expect(screen.getByText(/Quick scan/)).toBeInTheDocument());
    expect(screen.getByText(/European Union/)).toBeInTheDocument();
  });

  it('requests the projection with the selected groups and cadence', async () => {
    global.fetch = mockFetch({ projectionByGroups: { quick: PROJECTION_A } });
    render(<CostPlanner />);
    await waitFor(() => expect(screen.getByText(/Quick scan/)).toBeInTheDocument());

    await selectScopeA('quick');

    await waitFor(() => {
      const calls = global.fetch.mock.calls.map(([url]) => String(url));
      expect(calls.some((url) => url.includes('/api/cost-projection') && url.includes('groups=quick') && url.includes('cadence=monthly'))).toBe(true);
    });
  });

  it('re-requests when cadence changes', async () => {
    global.fetch = mockFetch({ projectionByGroups: { quick: PROJECTION_A } });
    render(<CostPlanner />);
    await waitFor(() => expect(screen.getByText(/Quick scan/)).toBeInTheDocument());
    await selectScopeA('quick');
    await waitFor(() => expect(screen.getByText('quick')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Cadence'), { target: { value: 'weekly' } });

    await waitFor(() => {
      const calls = global.fetch.mock.calls.map(([url]) => String(url));
      expect(calls.some((url) => url.includes('cadence=weekly'))).toBe(true);
    });
  });
});

describe('CostPlanner table rows', () => {
  it('renders a row per group and a totals row', async () => {
    global.fetch = mockFetch({ projectionByGroups: { quick: PROJECTION_A } });
    render(<CostPlanner />);
    await waitFor(() => expect(screen.getByText(/Quick scan/)).toBeInTheDocument());
    await selectScopeA('quick');

    await waitFor(() => expect(screen.getByText('quick')).toBeInTheDocument());
    const row = screen.getByText('quick').closest('tr');
    expect(within(row).getByText('$2.00')).toBeInTheDocument(); // estimate
    expect(within(row).getByText('$2.50')).toBeInTheDocument(); // actual mean
    expect(within(row).getByText('$10.83')).toBeInTheDocument(); // projected/month

    const totalRow = screen.getByText('Total').closest('tr');
    expect(within(totalRow).getByText('$10.83')).toBeInTheDocument();
  });

  it('renders the table inside an admin-table-wrap container (WP-15)', async () => {
    global.fetch = mockFetch({ projectionByGroups: { quick: PROJECTION_A } });
    render(<CostPlanner />);
    await waitFor(() => expect(screen.getByText(/Quick scan/)).toBeInTheDocument());
    await selectScopeA('quick');

    await waitFor(() => expect(screen.getByText('quick')).toBeInTheDocument());
    const table = screen.getByRole('table');
    expect(table.closest('.admin-table-wrap')).not.toBeNull();
  });

  it('shows "-" for actual mean when there is no history', async () => {
    global.fetch = mockFetch({ projectionByGroups: { eu: PROJECTION_B } });
    render(<CostPlanner />);
    await waitFor(() => expect(screen.getByText(/European Union/)).toBeInTheDocument());
    await selectScopeA('eu');

    await waitFor(() => expect(screen.getByText('eu')).toBeInTheDocument());
    const row = screen.getByText('eu').closest('tr');
    expect(within(row).getByText('-')).toBeInTheDocument();
  });

  it('marks no-history scopes and shows the formula caveat note', async () => {
    global.fetch = mockFetch({ projectionByGroups: { eu: PROJECTION_B } });
    render(<CostPlanner />);
    await waitFor(() => expect(screen.getByText(/European Union/)).toBeInTheDocument());
    await selectScopeA('eu');

    await waitFor(() => expect(screen.getByText('eu')).toBeInTheDocument());
    expect(screen.getByRole('note')).toHaveTextContent(/no completed scans recorded yet/);
    expect(screen.getByText('eu').closest('td')).toHaveTextContent('*');
  });

  it('states in the no-history note that two real scans replace the formula (WP-18)', async () => {
    global.fetch = mockFetch({ projectionByGroups: { eu: PROJECTION_B } });
    render(<CostPlanner />);
    await waitFor(() => expect(screen.getByText(/European Union/)).toBeInTheDocument());
    await selectScopeA('eu');

    await waitFor(() => expect(screen.getByText('eu')).toBeInTheDocument());
    expect(screen.getByRole('note')).toHaveTextContent(
      /After a scope has completed two real scans, recorded costs replace the formula automatically\./,
    );
  });

  it('shows no caveat note when every scope has history', async () => {
    global.fetch = mockFetch({ projectionByGroups: { quick: PROJECTION_A } });
    render(<CostPlanner />);
    await waitFor(() => expect(screen.getByText(/Quick scan/)).toBeInTheDocument());
    await selectScopeA('quick');

    await waitFor(() => expect(screen.getByText('quick')).toBeInTheDocument());
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
  });
});

describe('CostPlanner empty state (WP-18)', () => {
  it('shows a pick-a-scope message and no table when nothing is selected', async () => {
    global.fetch = mockFetch();
    render(<CostPlanner />);
    await waitFor(() => expect(screen.getByText(/Quick scan/)).toBeInTheDocument());

    expect(screen.getByText('Pick one or more scope groups to see projected costs.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('replaces the empty state with the table once a scope has a projection', async () => {
    global.fetch = mockFetch({ projectionByGroups: { quick: PROJECTION_A } });
    render(<CostPlanner />);
    await waitFor(() => expect(screen.getByText(/Quick scan/)).toBeInTheDocument());
    expect(screen.getByText('Pick one or more scope groups to see projected costs.')).toBeInTheDocument();

    await selectScopeA('quick');

    await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
    expect(screen.queryByText('Pick one or more scope groups to see projected costs.')).not.toBeInTheDocument();
  });
});

describe('CostPlanner compare scenario', () => {
  it('fetches and renders a second scenario once compare is enabled', async () => {
    global.fetch = mockFetch({
      projectionByGroups: { quick: PROJECTION_A, eu: PROJECTION_B },
    });
    render(<CostPlanner />);
    await waitFor(() => expect(screen.getByText(/Quick scan/)).toBeInTheDocument());
    await selectScopeA('quick');
    await waitFor(() => expect(screen.getByText('quick')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('Compare with a second scope'));
    const scopeB = await screen.findByLabelText('Scope groups (Scenario B)');
    selectOption(scopeB, 'eu');

    await waitFor(() => expect(screen.getByText('eu')).toBeInTheDocument());
    expect(screen.getAllByText('Scenario A').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Scenario B').length).toBeGreaterThan(0);
  });
});

describe('buildPlainTextTable (Copy as text)', () => {
  it('renders an aligned plain-text table with headers, rows, and a total', () => {
    const text = buildPlainTextTable(
      [{ name: 'Scenario A', projection: PROJECTION_A }],
      'monthly',
    );
    const lines = text.split('\n');

    expect(lines[0]).toBe('Scenario    Group  Est/run  Actual mean  Projected/monthly');
    expect(lines[1]).toContain('quick');
    expect(lines[1]).toContain('$2.00');
    expect(lines[1]).toContain('$2.50');
    expect(lines[1]).toContain('$10.83');
    expect(lines[2]).toContain('TOTAL');
    expect(lines[2]).toContain('$10.83');
    // No markdown table pipes - this goes straight into a plain-text email.
    expect(text).not.toContain('|');
  });

  it('includes both scenarios when comparing', () => {
    const text = buildPlainTextTable(
      [
        { name: 'Scenario A', projection: PROJECTION_A },
        { name: 'Scenario B', projection: PROJECTION_B },
      ],
      'monthly',
    );
    expect(text).toContain('Scenario A');
    expect(text).toContain('Scenario B');
  });

  it('skips scenarios with no projection yet', () => {
    const text = buildPlainTextTable(
      [{ name: 'Scenario A', projection: null }],
      'monthly',
    );
    expect(text.split('\n')).toHaveLength(1); // header only
  });

  it('appends the no-history caveat and stars unhistoried groups in the text', () => {
    const text = buildPlainTextTable(
      [{ name: 'Scenario A', projection: PROJECTION_B }],
      'monthly',
    );
    expect(text).toContain('eu *');
    expect(text).toContain('no completed scans recorded yet');
  });

  it('omits the caveat when every group has history', () => {
    const text = buildPlainTextTable(
      [{ name: 'Scenario A', projection: PROJECTION_A }],
      'monthly',
    );
    expect(text).not.toContain('no completed scans recorded yet');
    expect(text).not.toContain('*');
  });

  it('writes the built text to navigator.clipboard on Copy as text', async () => {
    global.fetch = mockFetch({ projectionByGroups: { quick: PROJECTION_A } });
    render(<CostPlanner />);
    await waitFor(() => expect(screen.getByText(/Quick scan/)).toBeInTheDocument());
    await selectScopeA('quick');
    await waitFor(() => expect(screen.getByText('quick')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Copy as text' }));

    await waitFor(() => expect(global.navigator.clipboard.writeText).toHaveBeenCalledTimes(1));
    const copiedText = global.navigator.clipboard.writeText.mock.calls[0][0];
    expect(copiedText).toContain('quick');
    expect(copiedText).toContain('$10.83');
    expect(copiedText).not.toContain('|');
  });
});

const SCAN_ROWS = [
  {
    scan_id: 'scan-1',
    domain_group: 'quick-daily',
    status: 'completed',
    started_at: '2026-08-20T10:00:00Z',
    completed_at: '2026-08-20T10:30:00Z',
    cost_usd: 5.5,
    estimated_cost_usd: 5.0,
    estimated_low_usd: 4.0,
    estimated_high_usd: 6.0,
  },
  {
    scan_id: 'scan-2',
    domain_group: 'eu-full',
    status: 'completed_budget_reached',
    started_at: '2026-08-19T09:00:00Z',
    completed_at: '2026-08-19T09:45:00Z',
    cost_usd: 8.0,
    estimated_cost_usd: 10.0,
    estimated_low_usd: 8.0,
    estimated_high_usd: 12.0,
  },
  {
    scan_id: 'scan-3',
    domain_group: 'us-federal',
    status: 'failed',
    started_at: '2026-08-18T09:00:00Z',
    completed_at: null,
    cost_usd: null,
    estimated_cost_usd: null,
    estimated_low_usd: null,
    estimated_high_usd: null,
  },
];

describe('CostPlanner recent scans (WP-24)', () => {
  it('shows the empty state when there is no scan history', async () => {
    global.fetch = mockFetch();
    render(<CostPlanner />);

    expect(await screen.findByText(
      'No scans recorded yet - the first scheduled scan will appear here.',
    )).toBeInTheDocument();
    expect(screen.queryByText('quick-daily')).not.toBeInTheDocument();
  });

  it('renders a row per scan with Date, Scope, Status, Estimated, Actual and Difference', async () => {
    global.fetch = mockFetch({ scansHistory: { scans: SCAN_ROWS } });
    render(<CostPlanner />);

    const row = await screen.findByText('quick-daily').then((cell) => cell.closest('tr'));
    expect(within(row).getByText('completed')).toBeInTheDocument();
    expect(within(row).getByText('$5.00')).toBeInTheDocument(); // estimated
    expect(within(row).getByText('$5.50')).toBeInTheDocument(); // actual
    expect(within(row).getByText('+10%')).toBeInTheDocument(); // (5.5-5.0)/5.0
  });

  it('shows a negative difference with a minus sign, no double sign', async () => {
    global.fetch = mockFetch({ scansHistory: { scans: SCAN_ROWS } });
    render(<CostPlanner />);

    const row = await screen.findByText('eu-full').then((cell) => cell.closest('tr'));
    expect(within(row).getByText('-20%')).toBeInTheDocument(); // (8.0-10.0)/10.0
    expect(within(row).queryByText('+-20%')).not.toBeInTheDocument();
  });

  it('labels the completed_budget_reached status in plain language', async () => {
    global.fetch = mockFetch({ scansHistory: { scans: SCAN_ROWS } });
    render(<CostPlanner />);

    const row = await screen.findByText('eu-full').then((cell) => cell.closest('tr'));
    expect(within(row).getByText('completed (budget cap reached)')).toBeInTheDocument();
  });

  it('shows "-" for Estimated and Difference on a legacy row with no estimate on file', async () => {
    global.fetch = mockFetch({ scansHistory: { scans: SCAN_ROWS } });
    render(<CostPlanner />);

    const row = await screen.findByText('us-federal').then((cell) => cell.closest('tr'));
    expect(within(row).getByText('failed')).toBeInTheDocument();
    // Estimated, Actual, and Difference are each "-" for this row.
    expect(within(row).getAllByText('-')).toHaveLength(3);
  });

  it('shows the rolling-accuracy sentence once at least two rows have both figures', async () => {
    global.fetch = mockFetch({ scansHistory: { scans: SCAN_ROWS } });
    render(<CostPlanner />);

    // Two qualifying rows (scan-1: +10%, scan-2: -20%) - largest abs diff is 20%.
    expect(await screen.findByText('Estimates were within 20% of actual cost on the last 2 scans.'))
      .toBeInTheDocument();
  });

  it('does not show the rolling-accuracy sentence when fewer than two rows qualify', async () => {
    global.fetch = mockFetch({ scansHistory: { scans: [SCAN_ROWS[0], SCAN_ROWS[2]] } });
    render(<CostPlanner />);

    await screen.findByText('quick-daily');
    expect(screen.queryByText(/Estimates were within/)).not.toBeInTheDocument();
  });

  it('requests scan history with admin headers', async () => {
    setAdminToken('secret-token');
    global.fetch = mockFetch({ scansHistory: { scans: SCAN_ROWS } });
    render(<CostPlanner />);

    await waitFor(() => {
      const call = global.fetch.mock.calls.find(([url]) => String(url).includes('/api/scans/history'));
      expect(call).toBeDefined();
      expect(call[1].headers['X-Admin-Token']).toBe('secret-token');
    });
  });
});
