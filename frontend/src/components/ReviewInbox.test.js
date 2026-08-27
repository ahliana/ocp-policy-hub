import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import ReviewInbox, { sortEarlyFirst, sortNewestFirst } from './ReviewInbox';
import { setAdminToken } from '../utils/adminAuth';

const NEW_POLICIES = [
    {
        url: 'https://a.gov/old',
        policy_name: 'Older Act',
        jurisdiction: 'Sweden',
        lifecycle_stage: 'enacted',
        discovered_at: '2026-07-10T08:00:00',
        review_status: 'new',
    },
    {
        url: 'https://a.gov/new',
        policy_name: 'Thermal Energy Network Bill',
        jurisdiction: 'New Jersey, USA',
        lifecycle_stage: 'proposed',
        discovered_at: '2026-07-15T21:49:00',
        review_status: 'new',
    },
];

// Four items, already newest-first, mixing early and non-early stages so the
// "Early signals first" toggle's grouping (and its stability within each
// group) can be verified against a case with more than one item per group.
const MIXED_STAGE_POLICIES = [
    {
        url: 'https://a.gov/newest-non-early',
        policy_name: 'Newest Non-Early Act',
        jurisdiction: 'Norway',
        lifecycle_stage: 'enacted',
        discovered_at: '2026-07-20T08:00:00',
        review_status: 'new',
    },
    {
        url: 'https://a.gov/newer-early',
        policy_name: 'Newer Early Bill',
        jurisdiction: 'Sweden',
        lifecycle_stage: 'consultation',
        discovered_at: '2026-07-15T08:00:00',
        review_status: 'new',
    },
    {
        url: 'https://a.gov/older-early',
        policy_name: 'Older Early Bill',
        jurisdiction: 'Denmark',
        lifecycle_stage: 'proposed',
        discovered_at: '2026-07-12T08:00:00',
        review_status: 'new',
    },
    {
        url: 'https://a.gov/oldest-non-early',
        policy_name: 'Oldest Non-Early Act',
        jurisdiction: 'Finland',
        lifecycle_stage: 'enacted',
        discovered_at: '2026-07-10T08:00:00',
        review_status: 'new',
    },
];

function mockFetch({ patchOk = true, newPolicies = NEW_POLICIES } = {}) {
    return jest.fn(async (url, options = {}) => {
        const path = String(url);
        if (path.includes('review_status=new')) {
            return { ok: true, json: async () => ({ policies: newPolicies, count: newPolicies.length }) };
        }
        if (path.includes('review_status=promoted')) {
            return { ok: true, json: async () => ({ policies: [], count: 11 }) };
        }
        if (path.includes('/api/settings/sheet')) {
            return {
                ok: true,
                json: async () => ({ configured: true, url: 'https://docs.google.com/spreadsheets/d/x' }),
            };
        }
        if (path.includes('/api/policies/review') && options.method === 'PATCH') {
            return { ok: patchOk, json: async () => ({}) };
        }
        return { ok: false, json: async () => ({}) };
    });
}

afterEach(() => {
    jest.restoreAllMocks();
    setAdminToken(null);
    window.sessionStorage.clear();
});

describe('sortNewestFirst', () => {
    it('orders by discovered_at descending', () => {
        const sorted = sortNewestFirst(NEW_POLICIES);
        expect(sorted[0].url).toBe('https://a.gov/new');
    });
});

describe('sortEarlyFirst', () => {
    it('groups early-stage items first, preserving newest-first order within each group', () => {
        const sorted = sortEarlyFirst(MIXED_STAGE_POLICIES);
        expect(sorted.map((p) => p.url)).toEqual([
            'https://a.gov/newer-early',
            'https://a.gov/older-early',
            'https://a.gov/newest-non-early',
            'https://a.gov/oldest-non-early',
        ]);
    });

    it('leaves an empty list unaffected', () => {
        expect(sortEarlyFirst([])).toEqual([]);
    });
});

describe('ReviewInbox', () => {
    it('shows the queue newest first with an early-signal chip', async () => {
        global.fetch = mockFetch();
        render(<ReviewInbox isAdmin={false} />);

        await waitFor(() => {
            expect(screen.getByText('New finds to review (2)')).toBeInTheDocument();
        });
        const items = screen.getAllByRole('listitem');
        expect(items[0]).toHaveTextContent('Thermal Energy Network Bill');
        expect(items[0]).toHaveTextContent('Early signal');
        expect(items[1]).not.toHaveTextContent('Early signal');
        expect(screen.getByText(/11 promoted to the database/)).toBeInTheDocument();
    });

    it('attaches an InfoHotspot to the Early signal chip (WP-30b)', async () => {
        global.fetch = mockFetch();
        render(<ReviewInbox isAdmin={false} />);

        await waitFor(() => {
            expect(screen.getByText('New finds to review (2)')).toBeInTheDocument();
        });
        const items = screen.getAllByRole('listitem');
        expect(within(items[0]).getByRole('button', { name: 'What Early signal means' }))
            .toBeInTheDocument();
        expect(within(items[1]).queryByRole('button', { name: 'What Early signal means' }))
            .not.toBeInTheDocument();
    });

    it('hides admin actions for readers', async () => {
        global.fetch = mockFetch();
        render(<ReviewInbox isAdmin={false} />);

        await waitFor(() => {
            expect(screen.getByText('New finds to review (2)')).toBeInTheDocument();
        });
        expect(screen.queryByText('Mark reviewed')).not.toBeInTheDocument();
        expect(screen.queryByText('Open review sheet')).not.toBeInTheDocument();
    });

    it('attaches the admin token to the new/promoted queue fetches', async () => {
        // Under a reviewed_only public visibility posture, these GET calls
        // must carry the admin header so the inbox still sees review_status
        // =new items instead of a clamped view (see src/api/review_visibility.py).
        setAdminToken('secret-token');
        const fetchMock = mockFetch();
        global.fetch = fetchMock;
        render(<ReviewInbox isAdmin />);

        await waitFor(() => {
            expect(screen.getByText('New finds to review (2)')).toBeInTheDocument();
        });

        const queueCalls = fetchMock.mock.calls.filter(
            ([url]) => String(url).includes('review_status='),
        );
        expect(queueCalls.length).toBeGreaterThan(0);
        for (const [, options] of queueCalls) {
            expect(options?.headers?.['X-Admin-Token']).toBe('secret-token');
        }
    });

    it('lets an admin open the sheet and mark items reviewed', async () => {
        const fetchMock = mockFetch();
        global.fetch = fetchMock;
        render(<ReviewInbox isAdmin />);

        await waitFor(() => {
            expect(screen.getByText('Open review sheet')).toBeInTheDocument();
        });
        expect(screen.getByText('Open review sheet')).toHaveAttribute(
            'href', 'https://docs.google.com/spreadsheets/d/x',
        );

        fireEvent.click(screen.getAllByText('Mark reviewed')[0]);
        await waitFor(() => {
            expect(screen.getAllByRole('listitem')).toHaveLength(1);
        });
        const patchCall = fetchMock.mock.calls.find(
            ([, options]) => options?.method === 'PATCH',
        );
        expect(JSON.parse(patchCall[1].body)).toEqual({
            url: 'https://a.gov/new',
            review_status: 'reviewed',
        });
    });

    it('defaults the "Early signals first" toggle off, leaving newest-first order unchanged', async () => {
        global.fetch = mockFetch({ newPolicies: MIXED_STAGE_POLICIES });
        render(<ReviewInbox isAdmin={false} />);

        await waitFor(() => {
            expect(screen.getByText('New finds to review (4)')).toBeInTheDocument();
        });
        expect(screen.getByRole('checkbox', { name: 'Early signals first' })).not.toBeChecked();
        const items = screen.getAllByRole('listitem');
        expect(items.map((item) => item.textContent)).toEqual([
            expect.stringContaining('Newest Non-Early Act'),
            expect.stringContaining('Newer Early Bill'),
            expect.stringContaining('Older Early Bill'),
            expect.stringContaining('Oldest Non-Early Act'),
        ]);
    });

    it('sorts early-stage items first when the toggle is turned on', async () => {
        global.fetch = mockFetch({ newPolicies: MIXED_STAGE_POLICIES });
        render(<ReviewInbox isAdmin={false} />);

        await waitFor(() => {
            expect(screen.getByText('New finds to review (4)')).toBeInTheDocument();
        });
        fireEvent.click(screen.getByRole('checkbox', { name: 'Early signals first' }));

        const items = screen.getAllByRole('listitem');
        expect(items.map((item) => item.textContent)).toEqual([
            expect.stringContaining('Newer Early Bill'),
            expect.stringContaining('Older Early Bill'),
            expect.stringContaining('Newest Non-Early Act'),
            expect.stringContaining('Oldest Non-Early Act'),
        ]);
    });

    it('persists the toggle preference in sessionStorage across remounts', async () => {
        global.fetch = mockFetch({ newPolicies: MIXED_STAGE_POLICIES });
        const { unmount } = render(<ReviewInbox isAdmin={false} />);

        await waitFor(() => {
            expect(screen.getByText('New finds to review (4)')).toBeInTheDocument();
        });
        fireEvent.click(screen.getByRole('checkbox', { name: 'Early signals first' }));
        expect(window.sessionStorage.getItem('review-inbox-early-first')).toBe('true');
        unmount();

        global.fetch = mockFetch({ newPolicies: MIXED_STAGE_POLICIES });
        render(<ReviewInbox isAdmin={false} />);
        await waitFor(() => {
            expect(screen.getByText('New finds to review (4)')).toBeInTheDocument();
        });
        expect(screen.getByRole('checkbox', { name: 'Early signals first' })).toBeChecked();
    });

    it('does not render the toggle in the empty state', async () => {
        global.fetch = mockFetch({ newPolicies: [] });
        render(<ReviewInbox isAdmin={false} />);

        await waitFor(() => {
            expect(screen.getByText(/All caught up/)).toBeInTheDocument();
        });
        expect(screen.queryByRole('checkbox', { name: 'Early signals first' })).not.toBeInTheDocument();
    });
});
