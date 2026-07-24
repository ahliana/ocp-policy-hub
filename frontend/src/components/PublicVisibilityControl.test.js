import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import PublicVisibilityControl from './PublicVisibilityControl';
import { setAdminToken } from '../utils/adminAuth';

function mockFetch({ mode = 'default_all', putOk = true } = {}) {
    let currentMode = mode;
    return jest.fn(async (url, options = {}) => {
        const path = String(url);
        if (path.includes('/api/settings/public-visibility') && (!options.method || options.method === 'GET')) {
            return { ok: true, json: async () => ({ mode: currentMode }) };
        }
        if (path.includes('/api/settings/public-visibility') && options.method === 'PUT') {
            if (!putOk) return { ok: false, json: async () => ({}) };
            currentMode = JSON.parse(options.body).mode;
            return { ok: true, json: async () => ({ mode: currentMode }) };
        }
        return { ok: false, json: async () => ({}) };
    });
}

afterEach(() => {
    jest.restoreAllMocks();
    setAdminToken(null);
});

describe('PublicVisibilityControl', () => {
    it('loads the current mode and checks the matching radio', async () => {
        global.fetch = mockFetch({ mode: 'default_reviewed' });
        render(<PublicVisibilityControl />);

        await waitFor(() => {
            expect(screen.getByRole('radio', { name: /reviewed only by default/i })).toBeChecked();
        });
        expect(screen.getByRole('radio', { name: /all finds by default/i })).not.toBeChecked();
    });

    it('saves the new mode via PUT with the admin header', async () => {
        setAdminToken('secret-token');
        const fetchMock = mockFetch({ mode: 'default_all' });
        global.fetch = fetchMock;
        render(<PublicVisibilityControl />);

        await waitFor(() => {
            expect(screen.getByRole('radio', { name: /all finds by default/i })).toBeChecked();
        });

        fireEvent.click(screen.getByRole('radio', { name: /reviewed only.*hide the switch/i }));

        await waitFor(() => {
            const putCall = fetchMock.mock.calls.find(([, options]) => options?.method === 'PUT');
            expect(putCall).toBeDefined();
            expect(JSON.parse(putCall[1].body)).toEqual({ mode: 'reviewed_only' });
            expect(putCall[1].headers['X-Admin-Token']).toBe('secret-token');
        });

        expect(await screen.findByText(/saved/i)).toBeInTheDocument();
        expect(screen.getByRole('radio', { name: /reviewed only.*hide the switch/i })).toBeChecked();
    });

    it('shows an error if saving fails', async () => {
        const fetchMock = mockFetch({ mode: 'default_all', putOk: false });
        global.fetch = fetchMock;
        render(<PublicVisibilityControl />);

        await waitFor(() => {
            expect(screen.getByRole('radio', { name: /all finds by default/i })).toBeChecked();
        });

        fireEvent.click(screen.getByRole('radio', { name: /reviewed only by default, visitors can switch/i }));

        expect(await screen.findByRole('alert')).toHaveTextContent(/could not save/i);
    });
});
