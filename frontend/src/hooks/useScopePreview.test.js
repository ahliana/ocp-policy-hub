import { renderHook, waitFor } from '@testing-library/react';
import useScopePreview from './useScopePreview';

function jsonResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const GERMANY_DOMAINS = [
  {
    id: 'gesetze_enefg',
    name: 'Germany Federal Law Database - EnEfG',
    region: ['eu', 'eu_central', 'germany'],
    source_type: 'crawl',
  },
  {
    id: 'hessen_recht',
    name: 'Hessenrecht - State Legislation Database',
    region: ['eu', 'eu_central', 'germany', 'hessen'],
    source_type: 'crawl',
  },
  {
    id: 'legiscan_api',
    name: 'LegiScan API (US state legislation)',
    region: ['us'],
    source_type: 'legiscan',
  },
  {
    id: 'eurlex_nim_tracker',
    name: 'EUR-Lex National Implementing Measures Tracker',
    region: ['eu'],
    source_type: 'eurlex_nim',
  },
];

function mockFetch() {
  return jest.fn(async (url) => {
    const parsed = new URL(String(url));
    if (parsed.pathname === '/api/domains') {
      return jsonResponse(200, { domains: GERMANY_DOMAINS });
    }
    return jsonResponse(404, {});
  });
}

afterEach(() => {
  jest.restoreAllMocks();
});

describe('useScopePreview (WP-28)', () => {
  it('does not fetch while inactive (closed)', () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;

    renderHook(() => useScopePreview({ selectedRegions: ['group:germany'], active: false }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('fetches lazily once it becomes active, not on selection changes while inactive', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;

    const { result, rerender } = renderHook(
      ({ selectedRegions, active }) => useScopePreview({ selectedRegions, active }),
      { initialProps: { selectedRegions: ['group:germany'], active: false } },
    );

    rerender({ selectedRegions: ['group:germany', 'group:nordic'], active: false });
    expect(fetchMock).not.toHaveBeenCalled();

    rerender({ selectedRegions: ['group:germany', 'group:nordic'], active: true });
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(fetchMock).toHaveBeenCalled();
  });

  it('groups resolved domains under plain-language channel headings', async () => {
    global.fetch = mockFetch();
    const { result } = renderHook(() => (
      useScopePreview({ selectedRegions: ['group:germany'], active: true })
    ));
    await waitFor(() => expect(result.current.status).toBe('ready'));

    const byId = Object.fromEntries(result.current.groups.map((g) => [g.id, g]));
    expect(byId.crawl.label).toBe('Government websites');
    expect(byId.crawl.entries.map((e) => e.name)).toEqual(expect.arrayContaining([
      'Germany Federal Law Database - EnEfG',
      'Hessenrecht - State Legislation Database',
    ]));
    expect(byId.law_apis.label).toBe('Law databases');
    expect(byId.law_apis.entries.map((e) => e.name)).toEqual(['LegiScan API (US state legislation)']);
    expect(byId.transposition.label).toBe('EU law trackers');
    expect(byId.transposition.entries.map((e) => e.name)).toEqual([
      'EUR-Lex National Implementing Measures Tracker',
    ]);
  });

  it('shows the most specific region next to each entry, formatted', async () => {
    global.fetch = mockFetch();
    const { result } = renderHook(() => (
      useScopePreview({ selectedRegions: ['group:germany'], active: true })
    ));
    await waitFor(() => expect(result.current.status).toBe('ready'));

    const crawlGroup = result.current.groups.find((g) => g.id === 'crawl');
    expect(crawlGroup.entries.find((e) => e.id === 'hessen_recht').region).toBe('Hessen');
    expect(crawlGroup.entries.find((e) => e.id === 'gesetze_enefg').region).toBe('Germany');
  });

  it('reports the total resolved domain count', async () => {
    global.fetch = mockFetch();
    const { result } = renderHook(() => (
      useScopePreview({ selectedRegions: ['group:germany'], active: true })
    ));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.totalCount).toBe(4);
  });

  it('is "empty" when nothing is selected, without fetching', () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;

    const { result } = renderHook(() => useScopePreview({ selectedRegions: [], active: true }));
    expect(result.current.status).toBe('empty');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('maps a fetch failure to an error status', async () => {
    global.fetch = jest.fn(async () => { throw new Error('network down'); });
    const { result } = renderHook(() => (
      useScopePreview({ selectedRegions: ['group:germany'], active: true })
    ));
    await waitFor(() => expect(result.current.status).toBe('error'));
  });
});
