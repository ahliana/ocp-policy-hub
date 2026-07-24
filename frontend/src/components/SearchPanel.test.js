import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import SearchPanel, { summarizePlanSources } from './SearchPanel';

const CA_PLAN = {
    place: { kind: 'us_state', region_key: 'california', display: 'California', state_code: 'CA' },
    terms: [],
    targets: 'california,legiscan_api',
    channels: ['crawl', 'law_apis'],
    source_params: { state: 'CA' },
    sources: [
        {
            id: 'legiscan_api', name: 'LegiScan API', kind: 'law_api',
            description: 'Bills in all 50 US state legislatures', requires_key: true,
            key_present: true, key_env: 'LEGISCAN_API_KEY',
        },
        {
            id: 'ca_energy', name: 'California Energy Commission', kind: 'website',
            description: 'Government website: California Energy Commission',
            requires_key: false, key_present: true, key_env: null,
        },
    ],
    estimate: {
        legiscan: { max_queries: 30, remaining: 29882, used: 118, limit: 30000, month: '2026-07' },
        llm_ceiling_usd: 0.45,
        cost_level: 'standard',
    },
    warnings: [],
};

const UNKNOWN_PLAN = {
    place: { kind: 'unknown', region_key: '', display: 'Atlantis' },
    terms: [],
    targets: '',
    channels: [],
    source_params: {},
    sources: [],
    estimate: { legiscan: null, llm_ceiling_usd: 0, cost_level: 'standard' },
    warnings: ["Could not recognize 'Atlantis'. Try a country (\"Sweden\")."],
};

function mockFetch({ plan = CA_PLAN, scanStatus = 200 } = {}) {
    return jest.fn(async (url) => {
        const path = String(url);
        if (path.includes('/api/search/places')) {
            return { ok: true, json: async () => ({ places: ['California', 'Sweden'] }) };
        }
        if (path.includes('/api/search/plan')) {
            return { ok: true, json: async () => plan };
        }
        if (path.includes('/api/scans')) {
            return {
                ok: scanStatus === 200,
                status: scanStatus,
                json: async () => ({ scan_id: 'abc12345', domain_count: 2 }),
                text: async () => 'denied',
            };
        }
        return { ok: false, text: async () => 'not found' };
    });
}

class FakeWebSocket {
    constructor() {
        FakeWebSocket.instances.push(this);
    }

    close() {}

    emit(payload) {
        this.onmessage?.({ data: JSON.stringify(payload) });
    }
}
FakeWebSocket.instances = [];

beforeEach(() => {
    jest.useFakeTimers();
    global.WebSocket = FakeWebSocket;
    FakeWebSocket.instances = [];
});

afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
});

async function typePlace(value) {
    fireEvent.change(screen.getByLabelText('Place to search'), {
        target: { value },
    });
    await act(async () => {
        jest.advanceTimersByTime(500);
    });
}

describe('summarizePlanSources', () => {
    it('names law APIs and folds many websites into a count', () => {
        const sources = [
            { name: 'LegiScan API', kind: 'law_api' },
            { name: 'Site A', kind: 'website' },
            { name: 'Site B', kind: 'website' },
        ];
        expect(summarizePlanSources(sources)).toBe('LegiScan API · 2 government websites');
    });
});

describe('SearchPanel plan preview', () => {
    it('shows sources, cost ceiling, and LegiScan budget for a known place', async () => {
        global.fetch = mockFetch();
        render(<SearchPanel hasApiKey isBusy={false} />);

        await typePlace('California');

        await waitFor(() => {
            expect(screen.getByText(/Will search:/)).toBeInTheDocument();
        });
        expect(screen.getByText(/LegiScan API/)).toBeInTheDocument();
        expect(screen.getByText(/up to ~\$0.45/)).toBeInTheDocument();
        expect(screen.getByText(/29,882/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Search' })).toBeEnabled();
    });

    it('disambiguates the model cost level and points at Advanced for scan modes', async () => {
        global.fetch = mockFetch();
        render(<SearchPanel hasApiKey isBusy={false} />);

        await typePlace('California');

        await waitFor(() => {
            expect(screen.getByText(/model cost level: standard, set in API key settings/))
                .toBeInTheDocument();
        });
        expect(screen.getByText(/Want a different scan mode \(discover, deep\)/))
            .toBeInTheDocument();
    });

    it('explains an unrecognized place and keeps Search disabled', async () => {
        global.fetch = mockFetch({ plan: UNKNOWN_PLAN });
        render(<SearchPanel hasApiKey isBusy={false} />);

        await typePlace('Atlantis');

        await waitFor(() => {
            expect(screen.getByText(/Could not recognize 'Atlantis'/)).toBeInTheDocument();
        });
        expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled();
    });

    it('does not scold while the user is still typing toward a suggestion', async () => {
        // "cal" is on its way to "California" - suppress the unknown-place
        // warning while a suggestion still prefix-matches the input.
        global.fetch = mockFetch({ plan: UNKNOWN_PLAN });
        render(<SearchPanel hasApiKey isBusy={false} />);

        await typePlace('cal');

        await waitFor(() => {
            expect(screen.getByRole('combobox')).toHaveValue('cal');
        });
        expect(screen.queryByText(/Could not recognize/)).not.toBeInTheDocument();
    });
});

describe('SearchPanel run', () => {
    it('submits ONE consolidated scan with the plan targets and source params', async () => {
        const fetchMock = mockFetch();
        global.fetch = fetchMock;
        render(<SearchPanel hasApiKey isBusy={false} />);

        await typePlace('California');
        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Search' })).toBeEnabled();
        });

        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Search' }));
        });

        const scanCall = fetchMock.mock.calls.find(([url]) =>
            String(url).endsWith('/api/scans'));
        expect(scanCall).toBeTruthy();
        const body = JSON.parse(scanCall[1].body);
        expect(body.domains).toBe('california,legiscan_api');
        expect(body.channels).toEqual(['crawl', 'law_apis']);
        expect(body.source_params).toEqual({ state: 'CA' });
        expect(FakeWebSocket.instances).toHaveLength(1);
    });

    it('survives per-source errors and completes with a summary', async () => {
        global.fetch = mockFetch();
        render(<SearchPanel hasApiKey isBusy={false} />);

        await typePlace('California');
        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Search' })).toBeEnabled();
        });
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Search' }));
        });

        const ws = FakeWebSocket.instances[0];
        await act(async () => {
            ws.emit({ type: 'error', domain_id: 'nyserda', data: { error: 'Playwright is required' } });
            ws.emit({ type: 'policy_found', data: { policy_name: 'Heat Act' } });
            ws.emit({ type: 'scan_complete', data: { total_policies: 1 } });
        });

        expect(screen.getByText(/Done: 1 policy found/)).toBeInTheDocument();
        expect(screen.getByText(/1 of the sources had errors/)).toBeInTheDocument();
        expect(screen.queryByText(/could not be completed/)).not.toBeInTheDocument();
        // The errors are inspectable, not a mystery number.
        expect(screen.getByText('1 source error - show details')).toBeInTheDocument();
        expect(screen.getByText(/nyserda/)).toBeInTheDocument();
        expect(screen.getByText(/Playwright is required/)).toBeInTheDocument();
    });

    it('shows live activity: progress bar, current source, page heartbeat', async () => {
        global.fetch = mockFetch();
        render(<SearchPanel hasApiKey isBusy={false} />);

        await typePlace('California');
        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Search' })).toBeEnabled();
        });
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Search' }));
        });

        const ws = FakeWebSocket.instances[0];
        await act(async () => {
            ws.emit({ type: 'domain_started', data: { domain_name: 'LegiScan API' } });
            ws.emit({ type: 'page_fetched', data: { url: 'https://a.gov/1' } });
            ws.emit({ type: 'page_fetched', data: { url: 'https://a.gov/2' } });
            ws.emit({ type: 'domain_complete', data: { pages: 2, policies: 0 } });
        });

        expect(screen.getByRole('progressbar')).toBeInTheDocument();
        expect(screen.getByText(/Now checking:/)).toHaveTextContent('LegiScan API');
        expect(screen.getByText(/2 pages checked/)).toBeInTheDocument();
    });

    it('reassures instead of going silent when events stall', async () => {
        global.fetch = mockFetch();
        render(<SearchPanel hasApiKey isBusy={false} />);

        await typePlace('California');
        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Search' })).toBeEnabled();
        });
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Search' }));
        });

        await act(async () => {
            jest.advanceTimersByTime(50000);
        });

        expect(screen.getByText(/Still working/)).toBeInTheDocument();
    });

    it('explains a 401 as a sign-in problem', async () => {
        global.fetch = mockFetch({ scanStatus: 401 });
        render(<SearchPanel hasApiKey isBusy={false} />);

        await typePlace('California');
        await waitFor(() => {
            expect(screen.getByRole('button', { name: 'Search' })).toBeEnabled();
        });
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Search' }));
        });

        expect(
            screen.getByText(/administrator sign-in. Open Settings/),
        ).toBeInTheDocument();
    });

    it('shows the administrator indicator when the gate is active', async () => {
        global.fetch = mockFetch();
        render(<SearchPanel hasApiKey isBusy={false} adminRequired />);
        expect(
            screen.getByText(/Signed in as administrator/),
        ).toBeInTheDocument();
    });
});
