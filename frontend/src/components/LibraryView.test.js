import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import LibraryView from './LibraryView';
import { setAdminToken } from '../utils/adminAuth';

const POLICY_NEW = {
  url: 'https://a.gov/1',
  policy_name: 'Alpha Act',
  jurisdiction: 'Germany',
  lifecycle_stage: 'proposed',
  review_status: 'new',
  relevance_score: 5,
  domain_id: 'de_bmwk',
  discovered_at: '2026-01-01T00:00:00',
  summary: 'A summary of Alpha',
  policy_type: 'law',
};

const POLICY_PROMOTED = {
  url: 'https://a.gov/2',
  policy_name: 'Beta Act',
  jurisdiction: 'France',
  lifecycle_stage: 'enacted',
  review_status: 'promoted',
  relevance_score: 9,
  domain_id: 'fr_gov',
  discovered_at: '2026-02-01T00:00:00',
  summary: 'A summary of Beta',
  policy_type: 'law',
};

const POLICY_REJECTED = {
  url: 'https://a.gov/3',
  policy_name: 'Gamma Act',
  jurisdiction: 'Austria',
  lifecycle_stage: 'unknown',
  review_status: 'rejected',
  relevance_score: 1,
  domain_id: 'at_ris',
  discovered_at: '2026-03-01T00:00:00',
  summary: 'A summary of Gamma',
  policy_type: 'law',
};

function libraryResponse(policies, total = policies.length) {
  return { ok: true, json: async () => ({ policies, total, limit: 25, offset: 0 }) };
}

function mockFetch({ policies = [POLICY_NEW, POLICY_PROMOTED, POLICY_REJECTED], patchOk = true } = {}) {
  return jest.fn(async (url, options = {}) => {
    const path = String(url);
    const method = options.method || 'GET';
    if (path.includes('/api/policies/library') && method === 'GET') {
      return libraryResponse(policies);
    }
    if (path.includes('/api/policies/review') && method === 'PATCH') {
      return { ok: patchOk, json: async () => ({}) };
    }
    return { ok: false, json: async () => ({}) };
  });
}

function libraryCalls(fetchMock) {
  return fetchMock.mock.calls.filter(([url]) => String(url).includes('/api/policies/library'));
}

afterEach(() => {
  jest.restoreAllMocks();
  setAdminToken(null);
});

describe('LibraryView data loading', () => {
  it('renders rows from a mocked fetch, sending the admin token header', async () => {
    setAdminToken('secret-token');
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<LibraryView />);

    await screen.findByText('Alpha Act');
    expect(screen.getByText('Beta Act')).toBeInTheDocument();
    expect(screen.getByText('Gamma Act')).toBeInTheDocument();

    const calls = libraryCalls(fetchMock);
    expect(calls.length).toBeGreaterThan(0);
    expect(calls[0][1]?.headers?.['X-Admin-Token']).toBe('secret-token');
  });

  it('shows an empty state when no policies match', async () => {
    global.fetch = mockFetch({ policies: [] });
    render(<LibraryView />);
    await screen.findByText(/no policies/i);
  });

  it('shows an error state when the fetch fails', async () => {
    global.fetch = jest.fn(async () => ({ ok: false, json: async () => ({}) }));
    render(<LibraryView />);
    await screen.findByRole('alert');
  });
});

describe('LibraryView sorting', () => {
  it('clicking a sortable header refetches with sort params and toggles direction', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<LibraryView />);
    await screen.findByText('Alpha Act');

    fireEvent.click(screen.getByRole('button', { name: 'Name' }));
    await waitFor(() => {
      const calls = libraryCalls(fetchMock);
      const last = String(calls[calls.length - 1][0]);
      expect(last).toEqual(expect.stringContaining('sort=name'));
      expect(last).toEqual(expect.stringContaining('sort_dir=asc'));
    });

    const nameHeader = screen.getByRole('columnheader', { name: 'Name' });
    expect(nameHeader).toHaveAttribute('aria-sort', 'ascending');

    fireEvent.click(screen.getByRole('button', { name: 'Name' }));
    await waitFor(() => {
      const calls = libraryCalls(fetchMock);
      const last = String(calls[calls.length - 1][0]);
      expect(last).toEqual(expect.stringContaining('sort_dir=desc'));
    });
    expect(screen.getByRole('columnheader', { name: 'Name' })).toHaveAttribute(
      'aria-sort', 'descending',
    );
  });
});

describe('LibraryView filters', () => {
  it('changing the review-status filter refetches with the param', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<LibraryView />);
    await screen.findByText('Alpha Act');

    fireEvent.change(screen.getByLabelText(/review status/i), { target: { value: 'promoted' } });
    await waitFor(() => {
      const calls = libraryCalls(fetchMock);
      expect(String(calls[calls.length - 1][0])).toEqual(
        expect.stringContaining('review_status=promoted'),
      );
    });
  });

  it('changing the lifecycle filter refetches with the param', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<LibraryView />);
    await screen.findByText('Alpha Act');

    fireEvent.change(screen.getByLabelText(/lifecycle/i), { target: { value: 'enacted' } });
    await waitFor(() => {
      const calls = libraryCalls(fetchMock);
      expect(String(calls[calls.length - 1][0])).toEqual(
        expect.stringContaining('lifecycle_stage=enacted'),
      );
    });
  });

  it('debounces the jurisdiction text filter', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<LibraryView />);
    await screen.findByText('Alpha Act');

    const input = screen.getByLabelText(/jurisdiction/i);
    fireEvent.change(input, { target: { value: 'germ' } });

    await waitFor(
      () => {
        const calls = libraryCalls(fetchMock);
        expect(String(calls[calls.length - 1][0])).toEqual(
          expect.stringContaining('jurisdiction=germ'),
        );
      },
      { timeout: 2000 },
    );
  });

  it('Clear resets the filters and refetches unfiltered', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<LibraryView />);
    await screen.findByText('Alpha Act');

    fireEvent.change(screen.getByLabelText(/review status/i), { target: { value: 'promoted' } });
    await waitFor(() => {
      expect(String(libraryCalls(fetchMock).pop()[0])).toContain('review_status=promoted');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    await waitFor(() => {
      expect(String(libraryCalls(fetchMock).pop()[0])).not.toContain('review_status=');
    });
  });
});

describe('LibraryView row actions', () => {
  it('Promote sends a PATCH with the right body and the row updates', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<LibraryView />);
    await screen.findByText('Alpha Act');

    const row = screen.getByText('Alpha Act').closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: 'Promote' }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([url, options]) => String(url).includes('/api/policies/review') && options?.method === 'PATCH',
      );
      expect(patchCall).toBeDefined();
      expect(JSON.parse(patchCall[1].body)).toEqual({
        url: 'https://a.gov/1', review_status: 'promoted',
      });
    });
  });

  it('Reject opens an inline reason prompt and sends it with the PATCH', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<LibraryView />);
    await screen.findByText('Alpha Act');

    const row = screen.getByText('Alpha Act').closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: 'Reject' }));

    const reasonInput = await screen.findByLabelText(/reason/i);
    fireEvent.change(reasonInput, { target: { value: 'Duplicate entry' } });
    fireEvent.click(screen.getByRole('button', { name: /confirm reject/i }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([url, options]) => String(url).includes('/api/policies/review') && options?.method === 'PATCH',
      );
      expect(patchCall).toBeDefined();
      expect(JSON.parse(patchCall[1].body)).toEqual({
        url: 'https://a.gov/1', review_status: 'rejected', reason: 'Duplicate entry',
      });
    });
  });

  it('Restore is available on rejected rows and sends review_status=new', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<LibraryView />);
    await screen.findByText('Gamma Act');

    const row = screen.getByText('Gamma Act').closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: 'Restore' }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([url, options]) => String(url).includes('/api/policies/review') && options?.method === 'PATCH',
      );
      expect(patchCall).toBeDefined();
      expect(JSON.parse(patchCall[1].body)).toEqual({
        url: 'https://a.gov/3', review_status: 'new',
      });
    });
  });

  it('clicking the row name expands the SavedPolicy detail card', async () => {
    global.fetch = mockFetch();
    render(<LibraryView />);
    await screen.findByText('Alpha Act');

    expect(screen.queryByText('A summary of Alpha')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Alpha Act' }));
    expect(await screen.findByText('A summary of Alpha')).toBeInTheDocument();
  });
});

describe('LibraryView pagination', () => {
  it('shows Showing X-Y of Z and disables Previous on the first page', async () => {
    const manyPolicies = Array.from({ length: 25 }, (_, i) => ({
      ...POLICY_NEW, url: `https://a.gov/p${i}`, policy_name: `Policy ${i}`,
    }));
    global.fetch = jest.fn(async (url, options = {}) => {
      if (String(url).includes('/api/policies/library')) {
        return { ok: true, json: async () => ({ policies: manyPolicies, total: 40, limit: 25, offset: 0 }) };
      }
      return { ok: false, json: async () => ({}) };
    });
    render(<LibraryView />);

    await screen.findByText(/Showing 1-25 of 40/);
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Next' })).not.toBeDisabled();
  });

  it('Next advances the offset and refetches', async () => {
    const fetchMock = jest.fn(async (url) => {
      if (String(url).includes('offset=25')) {
        return { ok: true, json: async () => ({ policies: [POLICY_PROMOTED], total: 40, limit: 25, offset: 25 }) };
      }
      return { ok: true, json: async () => ({ policies: [POLICY_NEW], total: 40, limit: 25, offset: 0 }) };
    });
    global.fetch = fetchMock;
    render(<LibraryView />);
    await screen.findByText(/Showing 1-25 of 40/);

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText(/Showing 26-40 of 40/);
  });
});
