import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import SourcesPanel from './SourcesPanel';

const SOURCES_RESPONSE = {
  sources: [
    {
      id: 'sweden_energy', name: 'Swedish Energy Agency', type: 'crawl',
      region: ['sweden', 'nordic'], enabled_in_yaml: true, enabled_override: null,
      effective_enabled: true, key_status: null,
    },
    {
      id: 'riksdagen_api', name: 'Riksdagen Open Data API', type: 'riksdagen',
      region: ['sweden'], enabled_in_yaml: true, enabled_override: null,
      effective_enabled: true, key_status: { required_env: null, configured: true },
    },
    {
      id: 'legiscan_api', name: 'LegiScan', type: 'legiscan',
      region: ['us'], enabled_in_yaml: true, enabled_override: false,
      effective_enabled: false, key_status: { required_env: 'LEGISCAN_API_KEY', configured: false },
    },
  ],
  count: 3,
};

function jsonResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function mockFetch({ onPut } = {}) {
  return jest.fn(async (url, options) => {
    const parsed = new URL(String(url));
    if (parsed.pathname === '/api/sources/status') {
      return jsonResponse(200, SOURCES_RESPONSE);
    }
    const putMatch = parsed.pathname.match(/^\/api\/sources\/([^/]+)\/enabled$/);
    if (putMatch && options?.method === 'PUT') {
      if (onPut) return onPut(putMatch[1], JSON.parse(options.body));
      return jsonResponse(200, { id: putMatch[1], enabled_override: JSON.parse(options.body).enabled });
    }
    return jsonResponse(404, {});
  });
}

afterEach(() => {
  jest.restoreAllMocks();
});

describe('SourcesPanel fetching and summary', () => {
  it('fetches /api/sources/status on mount and shows the summary line', async () => {
    global.fetch = mockFetch();
    render(<SourcesPanel />);

    await waitFor(() => expect(screen.getByText(/3 sources/)).toBeInTheDocument());
    expect(screen.getByText(/2 enabled/)).toBeInTheDocument();
    expect(screen.getByText(/2 API connectors/)).toBeInTheDocument();
  });

  it('labels keyless connectors "No key needed", not configured or missing', async () => {
    global.fetch = mockFetch();
    render(<SourcesPanel />);

    await waitFor(() => expect(screen.getByText('Riksdagen Open Data API')).toBeInTheDocument());
    const keylessRow = screen.getByText('Riksdagen Open Data API').closest('tr')
      || screen.getByText('Riksdagen Open Data API').closest('li');
    expect(within(keylessRow).getByText('No key needed')).toBeInTheDocument();
    const keyedRow = screen.getByText('LegiScan').closest('tr')
      || screen.getByText('LegiScan').closest('li');
    expect(within(keyedRow).getByText('Key missing')).toBeInTheDocument();
    expect(screen.getByText(/1 missing keys?/)).toBeInTheDocument();
  });

  it('renders a row per source with name, type badge, and regions', async () => {
    global.fetch = mockFetch();
    render(<SourcesPanel />);

    await waitFor(() => expect(screen.getByText('Swedish Energy Agency')).toBeInTheDocument());
    const row = screen.getByText('Swedish Energy Agency').closest('tr');
    expect(within(row).getByText('crawl')).toBeInTheDocument();
    expect(within(row).getByText(/sweden/)).toBeInTheDocument();
  });

  it('shows a key-status badge for connectors and none for crawl rows', async () => {
    global.fetch = mockFetch();
    render(<SourcesPanel />);
    await waitFor(() => expect(screen.getByText('LegiScan')).toBeInTheDocument());

    const legiscanRow = screen.getByText('LegiScan').closest('tr');
    expect(within(legiscanRow).getByText(/missing/i)).toBeInTheDocument();

    const riksdagenRow = screen.getByText('Riksdagen Open Data API').closest('tr');
    expect(within(riksdagenRow).getByText('No key needed')).toBeInTheDocument();
  });
});

describe('SourcesPanel filters', () => {
  it('filters by text search on name', async () => {
    global.fetch = mockFetch();
    render(<SourcesPanel />);
    await waitFor(() => expect(screen.getByText('LegiScan')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/search/i), { target: { value: 'legiscan' } });

    expect(screen.getByText('LegiScan')).toBeInTheDocument();
    expect(screen.queryByText('Swedish Energy Agency')).not.toBeInTheDocument();
  });

  it('filters by type', async () => {
    global.fetch = mockFetch();
    render(<SourcesPanel />);
    await waitFor(() => expect(screen.getByText('LegiScan')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/type/i), { target: { value: 'crawl' } });

    expect(screen.getByText('Swedish Energy Agency')).toBeInTheDocument();
    expect(screen.queryByText('LegiScan')).not.toBeInTheDocument();
  });

  it('filters by region text', async () => {
    global.fetch = mockFetch();
    render(<SourcesPanel />);
    await waitFor(() => expect(screen.getByText('LegiScan')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/region/i), { target: { value: 'us' } });

    expect(screen.getByText('LegiScan')).toBeInTheDocument();
    expect(screen.queryByText('Swedish Energy Agency')).not.toBeInTheDocument();
  });

  it('filters to missing-keys-only', async () => {
    global.fetch = mockFetch();
    render(<SourcesPanel />);
    await waitFor(() => expect(screen.getByText('LegiScan')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText(/missing keys only/i));

    expect(screen.getByText('LegiScan')).toBeInTheDocument();
    expect(screen.queryByText('Swedish Energy Agency')).not.toBeInTheDocument();
    expect(screen.queryByText('Riksdagen Open Data API')).not.toBeInTheDocument();
  });
});

describe('SourcesPanel toggle', () => {
  it('optimistically toggles then confirms on success', async () => {
    global.fetch = mockFetch();
    render(<SourcesPanel />);
    await waitFor(() => expect(screen.getByText('Swedish Energy Agency')).toBeInTheDocument());

    const toggle = screen.getByRole('button', { name: /toggle swedish energy agency/i });
    expect(toggle).toHaveTextContent(/enabled/i);

    fireEvent.click(toggle);

    await waitFor(() => expect(toggle).toHaveTextContent(/disabled/i));
    const calls = global.fetch.mock.calls.map(([url, options]) => ({ url: String(url), options }));
    const putCall = calls.find((c) => c.url.includes('/api/sources/sweden_energy/enabled'));
    expect(putCall).toBeTruthy();
    expect(JSON.parse(putCall.options.body)).toEqual({ enabled: false });
  });

  it('reverts the optimistic toggle when the PUT fails', async () => {
    global.fetch = mockFetch({ onPut: () => jsonResponse(500, { detail: 'boom' }) });
    render(<SourcesPanel />);
    await waitFor(() => expect(screen.getByText('Swedish Energy Agency')).toBeInTheDocument());

    const toggle = screen.getByRole('button', { name: /toggle swedish energy agency/i });
    fireEvent.click(toggle);

    await waitFor(() => expect(toggle).toHaveTextContent(/enabled/i));
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});

describe('SourcesPanel note', () => {
  it('shows a note that changes apply to future scans', async () => {
    global.fetch = mockFetch();
    render(<SourcesPanel />);
    await waitFor(() => expect(screen.getByText('Swedish Energy Agency')).toBeInTheDocument());
    expect(screen.getByText(/future scans/i)).toBeInTheDocument();
  });
});

describe('SourcesPanel pagination', () => {
  it('paginates at 50 rows per page', async () => {
    const many = {
      sources: Array.from({ length: 120 }, (_, i) => ({
        id: `d${i}`, name: `Domain ${i}`, type: 'crawl', region: [],
        enabled_in_yaml: true, enabled_override: null, effective_enabled: true, key_status: null,
      })),
      count: 120,
    };
    global.fetch = jest.fn(async (url) => {
      const parsed = new URL(String(url));
      if (parsed.pathname === '/api/sources/status') return jsonResponse(200, many);
      return jsonResponse(404, {});
    });
    render(<SourcesPanel />);

    await waitFor(() => expect(screen.getByText('Domain 0')).toBeInTheDocument());
    expect(screen.queryByText('Domain 55')).not.toBeInTheDocument();
    expect(screen.getByText(/page 1 of 3/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /next/i }));

    await waitFor(() => expect(screen.getByText('Domain 55')).toBeInTheDocument());
    expect(screen.queryByText('Domain 0')).not.toBeInTheDocument();
  });
});
