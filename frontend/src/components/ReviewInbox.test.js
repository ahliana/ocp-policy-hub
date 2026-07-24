import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ReviewInbox, { sortNewestFirst } from './ReviewInbox';
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

function mockFetch({ patchOk = true } = {}) {
    return jest.fn(async (url, options = {}) => {
        const path = String(url);
        if (path.includes('review_status=new')) {
            return { ok: true, json: async () => ({ policies: NEW_POLICIES, count: 2 }) };
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
});

describe('sortNewestFirst', () => {
    it('orders by discovered_at descending', () => {
        const sorted = sortNewestFirst(NEW_POLICIES);
        expect(sorted[0].url).toBe('https://a.gov/new');
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
});
