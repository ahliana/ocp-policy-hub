import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import PolicyList, { filterByLifecycle } from './PolicyList';

const policies = [
  { policy_name: 'A', lifecycle_stage: 'proposed' },
  { policy_name: 'B', lifecycle_stage: 'consultation' },
  { policy_name: 'C', lifecycle_stage: 'in_committee' },
  { policy_name: 'D', lifecycle_stage: 'passed' },
  { policy_name: 'E', lifecycle_stage: 'transposition_notified' },
  { policy_name: 'F', lifecycle_stage: 'enacted' },
  { policy_name: 'G', lifecycle_stage: 'amended' },
  { policy_name: 'H', lifecycle_stage: 'unknown' },
  { policy_name: 'I' },
];

describe('filterByLifecycle', () => {
  it('returns every policy for mode "all"', () => {
    expect(filterByLifecycle(policies, 'all')).toEqual(policies);
  });

  it('returns only upcoming-stage policies for mode "upcoming"', () => {
    const result = filterByLifecycle(policies, 'upcoming');
    expect(result.map((policy) => policy.policy_name)).toEqual(['A', 'B', 'C', 'D', 'E']);
  });

  it('returns only enacted-stage policies for mode "enacted"', () => {
    const result = filterByLifecycle(policies, 'enacted');
    expect(result.map((policy) => policy.policy_name)).toEqual(['F', 'G']);
  });

  it('excludes unknown or missing stages from upcoming and enacted modes', () => {
    const upcomingNames = filterByLifecycle(policies, 'upcoming').map((policy) => policy.policy_name);
    const enactedNames = filterByLifecycle(policies, 'enacted').map((policy) => policy.policy_name);

    expect(upcomingNames).not.toEqual(expect.arrayContaining(['H', 'I']));
    expect(enactedNames).not.toEqual(expect.arrayContaining(['H', 'I']));
  });
});

// --- Place-filter mode (WorldMap/CountryView "View found policies" -> here) ---

const ALL_POLICIES = [
  {
    url: 'https://a.gov/1', policy_name: 'Federal Heat Reuse Act', jurisdiction: 'US',
    relevance_score: 9, scan_id: 's1', domain_id: 'd1',
  },
  {
    url: 'https://a.gov/2', policy_name: 'Minnesota Thermal Pilot', jurisdiction: 'Minnesota, USA',
    relevance_score: 5, scan_id: 's2', domain_id: 'd2',
  },
  {
    url: 'https://a.gov/3', policy_name: 'Sweden Heat Rule', jurisdiction: 'Sweden',
    relevance_score: 6, scan_id: 's3', domain_id: 'd3',
  },
];

const US_PLACE_POLICIES = [ALL_POLICIES[0], ALL_POLICIES[1]];

const CONSULTATION_POLICIES = [
  {
    url: 'https://a.gov/4', policy_name: 'Open Comment Draft Rule', jurisdiction: 'Norway',
    relevance_score: 4, scan_id: 's4', domain_id: 'd4', lifecycle_stage: 'consultation',
  },
];

function mockFetch({
  placePolicies = US_PLACE_POLICIES, placeOk = true, searchOk = true,
  consultationPolicies = CONSULTATION_POLICIES, consultationOk = true,
} = {}) {
  return jest.fn(async (url) => {
    const s = String(url);
    if (s.includes('/api/policies/search')) {
      if (!searchOk) {
        return { ok: false, status: 500, text: async () => 'search failed' };
      }
      const q = (new URL(s).searchParams.get('q') || '').toLowerCase();
      const matches = ALL_POLICIES.filter((p) => p.policy_name.toLowerCase().includes(q));
      return { ok: true, json: async () => ({ policies: matches, total: matches.length, query: q }) };
    }
    if (s.includes('/api/policies') && s.includes('lifecycle_stage=consultation')) {
      return consultationOk
        ? { ok: true, json: async () => ({ policies: consultationPolicies, count: consultationPolicies.length }) }
        : { ok: false, status: 500, text: async () => 'server error' };
    }
    if (s.includes('/api/policies') && s.includes('place=')) {
      return placeOk
        ? { ok: true, json: async () => ({ policies: placePolicies, count: placePolicies.length }) }
        : { ok: false, status: 404, text: async () => 'not found' };
    }
    if (s.includes('/api/policies')) {
      return { ok: true, json: async () => ({ policies: ALL_POLICIES, count: ALL_POLICIES.length }) };
    }
    if (s.includes('/api/tags')) {
      return { ok: true, json: async () => ({}) };
    }
    return { ok: false, text: async () => 'not found' };
  });
}

afterEach(() => {
  jest.restoreAllMocks();
});

describe('PolicyList place-filter mode', () => {
  it('shows every policy in the normal all-policies view when no place is requested', async () => {
    global.fetch = mockFetch();
    render(<PolicyList />);

    expect(await screen.findByText('Federal Heat Reuse Act')).toBeInTheDocument();
    expect(screen.getByText('Sweden Heat Rule')).toBeInTheDocument();
    expect(screen.getByText('Showing 3 of 3 policies')).toBeInTheDocument();
    expect(screen.queryByText(/policies$/, { selector: '.policy-place-chip' })).not.toBeInTheDocument();
  });

  it('an external place request fetches /api/policies?place=<slug>, scopes the list, and shows the active-filter chip', async () => {
    global.fetch = mockFetch();
    render(
      <PolicyList externalPlace={{ slug: 'us', name: 'United States', nonce: 1 }} />,
    );

    expect(await screen.findByText('United States - 2 policies')).toBeInTheDocument();
    expect(screen.getByText('Federal Heat Reuse Act')).toBeInTheDocument();
    expect(screen.getByText('Minnesota Thermal Pilot')).toBeInTheDocument();
    expect(screen.queryByText('Sweden Heat Rule')).not.toBeInTheDocument();
    expect(screen.getByText('Showing 2 of 2 policies')).toBeInTheDocument();

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/policies?place=us'),
    );
  });

  it('scrolls the list into view when the place filter activates', async () => {
    global.fetch = mockFetch();
    const scrollSpy = jest.fn();
    window.HTMLElement.prototype.scrollIntoView = scrollSpy;

    render(
      <PolicyList externalPlace={{ slug: 'us', name: 'United States', nonce: 1 }} />,
    );

    await screen.findByText('United States - 2 policies');
    expect(scrollSpy).toHaveBeenCalled();
  });

  it('clearing the chip returns to the all-policies view with the search filter intact', async () => {
    global.fetch = mockFetch();
    render(
      <PolicyList externalPlace={{ slug: 'us', name: 'United States', nonce: 1 }} />,
    );

    await screen.findByText('United States - 2 policies');

    fireEvent.change(screen.getByLabelText('Search policies'), { target: { value: 'Minnesota' } });
    await waitFor(
      () => expect(screen.getByText('Showing 1 of 1 policies')).toBeInTheDocument(),
      { timeout: 2000 },
    );

    fireEvent.click(screen.getByRole('button', { name: 'Clear United States filter' }));

    await waitFor(() => {
      expect(screen.queryByText('United States - 2 policies')).not.toBeInTheDocument();
    });
    // The search filter ("Minnesota") is still applied to the full list.
    expect(screen.getByText('Minnesota Thermal Pilot')).toBeInTheDocument();
    expect(screen.queryByText('Federal Heat Reuse Act')).not.toBeInTheDocument();
    expect(screen.queryByText('Sweden Heat Rule')).not.toBeInTheDocument();
  });

  it('a fresh nonce for the same place re-fetches', async () => {
    global.fetch = mockFetch();
    const { rerender } = render(
      <PolicyList externalPlace={{ slug: 'us', name: 'United States', nonce: 1 }} />,
    );
    await screen.findByText('United States - 2 policies');
    const callsAfterFirst = global.fetch.mock.calls.filter((call) => String(call[0]).includes('place=us')).length;

    rerender(
      <PolicyList externalPlace={{ slug: 'us', name: 'United States', nonce: 2 }} />,
    );

    await waitFor(() => {
      const callsAfterSecond = global.fetch.mock.calls.filter((call) => String(call[0]).includes('place=us')).length;
      expect(callsAfterSecond).toBeGreaterThan(callsAfterFirst);
    });
  });

  it('surfaces an error without crashing when the place fetch fails', async () => {
    global.fetch = mockFetch({ placeOk: false });
    render(
      <PolicyList externalPlace={{ slug: 'atlantis', name: 'Atlantis', nonce: 1 }} />,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load policies for Atlantis.');
  });
});

// --- Server-backed search box ---

describe('PolicyList server-backed search', () => {
  afterEach(() => {
    jest.useRealTimers();
  });

  it('shows the updated search placeholder', async () => {
    global.fetch = mockFetch();
    render(<PolicyList />);
    await screen.findByText('Federal Heat Reuse Act');

    expect(screen.getByPlaceholderText('Search policies...')).toBeInTheDocument();
  });

  it('debounces the search request by 300ms, sending only one fetch for rapid typing', async () => {
    global.fetch = mockFetch();
    render(<PolicyList />);
    await screen.findByText('Federal Heat Reuse Act');

    jest.useFakeTimers();
    const input = screen.getByLabelText('Search policies');
    fireEvent.change(input, { target: { value: 's' } });
    fireEvent.change(input, { target: { value: 'sw' } });
    fireEvent.change(input, { target: { value: 'swe' } });

    const searchCalls = () =>
      global.fetch.mock.calls.filter((call) => String(call[0]).includes('/api/policies/search'));

    act(() => {
      jest.advanceTimersByTime(299);
    });
    expect(searchCalls()).toHaveLength(0);

    act(() => {
      jest.advanceTimersByTime(1);
    });
    jest.useRealTimers();

    await waitFor(() => {
      expect(searchCalls()).toHaveLength(1);
    });
    expect(searchCalls()[0][0]).toEqual(expect.stringContaining('q=swe'));
  });

  it('URL-encodes unicode characters in the search query', async () => {
    global.fetch = mockFetch();
    render(<PolicyList />);
    await screen.findByText('Federal Heat Reuse Act');

    fireEvent.change(screen.getByLabelText('Search policies'), { target: { value: 'Abwärme' } });

    await waitFor(
      () => {
        const searchCall = global.fetch.mock.calls.find((call) =>
          String(call[0]).includes('/api/policies/search'),
        );
        expect(searchCall).toBeDefined();
        expect(String(searchCall[0])).toContain('q=Abw%C3%A4rme');
      },
      { timeout: 2000 },
    );
  });

  it('replaces the shown list with search results once 2+ characters are entered', async () => {
    global.fetch = mockFetch();
    render(<PolicyList />);
    await screen.findByText('Federal Heat Reuse Act');

    fireEvent.change(screen.getByLabelText('Search policies'), { target: { value: 'sw' } });

    // "Sweden Heat Rule" is already in the baseline list, so the narrowing
    // signal is the OTHER policies disappearing once search results land.
    await waitFor(
      () => expect(screen.queryByText('Federal Heat Reuse Act')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );
    expect(screen.getByText('Sweden Heat Rule')).toBeInTheDocument();
    expect(screen.getByText('Showing 1 of 1 policies')).toBeInTheDocument();
  });

  it('restores the full list without an extra baseline fetch when cleared below 2 characters', async () => {
    global.fetch = mockFetch();
    render(<PolicyList />);
    await screen.findByText('Federal Heat Reuse Act');

    const baselineCallCount = () =>
      global.fetch.mock.calls.filter(
        (call) => String(call[0]).includes('/api/policies') && !String(call[0]).includes('search'),
      ).length;
    const baselineCallsBefore = baselineCallCount();

    fireEvent.change(screen.getByLabelText('Search policies'), { target: { value: 'sw' } });
    await waitFor(
      () => expect(screen.queryByText('Federal Heat Reuse Act')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );

    fireEvent.change(screen.getByLabelText('Search policies'), { target: { value: 's' } });

    await waitFor(() => {
      expect(screen.getByText('Federal Heat Reuse Act')).toBeInTheDocument();
    });
    expect(screen.getByText('Minnesota Thermal Pilot')).toBeInTheDocument();
    expect(screen.getByText('Sweden Heat Rule')).toBeInTheDocument();
    expect(baselineCallCount()).toBe(baselineCallsBefore);
  });

  it('shows the existing error affordance without crashing when the search fetch fails', async () => {
    global.fetch = mockFetch({ searchOk: false });
    render(<PolicyList />);
    await screen.findByText('Federal Heat Reuse Act');

    fireEvent.change(screen.getByLabelText('Search policies'), { target: { value: 'sw' } });

    await waitFor(
      () => {
        expect(
          screen.getByText('Could not load data. Check that the backend is running, then refresh.'),
        ).toBeInTheDocument();
      },
      { timeout: 2000 },
    );
  });
});

// --- "Open for comment" lifecycle chip (server-side lifecycle_stage) ---

describe('PolicyList "Open for comment" chip', () => {
  it('renders alongside the existing All/Upcoming/Enacted chips (unchanged UX)', async () => {
    global.fetch = mockFetch();
    render(<PolicyList />);
    await screen.findByText('Federal Heat Reuse Act');

    expect(screen.getByRole('button', { name: 'All' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Upcoming' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Enacted' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open for comment' })).toBeInTheDocument();
  });

  it('fetches /api/policies?lifecycle_stage=consultation server-side when clicked', async () => {
    global.fetch = mockFetch();
    render(<PolicyList />);
    await screen.findByText('Federal Heat Reuse Act');

    fireEvent.click(screen.getByRole('button', { name: 'Open for comment' }));

    await screen.findByText('Open Comment Draft Rule');
    expect(screen.queryByText('Federal Heat Reuse Act')).not.toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/policies?lifecycle_stage=consultation'),
    );
  });

  it('returns to the normal full list when All is clicked again', async () => {
    global.fetch = mockFetch();
    render(<PolicyList />);
    await screen.findByText('Federal Heat Reuse Act');

    fireEvent.click(screen.getByRole('button', { name: 'Open for comment' }));
    await screen.findByText('Open Comment Draft Rule');

    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    await screen.findByText('Federal Heat Reuse Act');
    expect(screen.getByText('Sweden Heat Rule')).toBeInTheDocument();
  });

  it('surfaces an error without crashing when the consultation fetch fails', async () => {
    global.fetch = mockFetch({ consultationOk: false });
    render(<PolicyList />);
    await screen.findByText('Federal Heat Reuse Act');

    fireEvent.click(screen.getByRole('button', { name: 'Open for comment' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/open.for.comment/i);
    });
  });
});

// --- Public review visibility (WP-3): every fetch carries ?review= ---

describe('PolicyList public review visibility', () => {
  function policiesCalls(fetchMock) {
    return fetchMock.mock.calls
      .map(([url]) => String(url))
      .filter((url) => url.includes('/api/policies') && !url.includes('/api/tags'));
  }

  it('defaults the baseline fetch to review=all', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<PolicyList />);
    await screen.findByText('Federal Heat Reuse Act');

    const calls = policiesCalls(fetchMock);
    expect(calls.some((url) => new URL(url).searchParams.get('review') === 'all')).toBe(true);
  });

  it('sends review=reviewed on the baseline fetch when publicView is reviewed', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<PolicyList publicView="reviewed" />);
    await waitFor(() => {
      const calls = policiesCalls(fetchMock);
      expect(calls.some((url) => new URL(url).searchParams.get('review') === 'reviewed')).toBe(true);
    });
  });

  it('refetches with the new review param when publicView changes', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    const { rerender } = render(<PolicyList publicView="all" />);
    await screen.findByText('Federal Heat Reuse Act');

    rerender(<PolicyList publicView="reviewed" />);

    await waitFor(() => {
      const calls = policiesCalls(fetchMock);
      expect(calls.some((url) => new URL(url).searchParams.get('review') === 'reviewed')).toBe(true);
    });
  });

  it('carries review on the place-filter fetch', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(
      <PolicyList
        externalPlace={{ slug: 'us', name: 'United States', nonce: 1 }}
        publicView="reviewed"
      />,
    );

    await screen.findByText('United States - 2 policies');
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/policies?place=us'),
    );
    const placeCall = fetchMock.mock.calls.find(([url]) => String(url).includes('place=us'));
    expect(new URL(placeCall[0]).searchParams.get('review')).toBe('reviewed');
  });

  it('carries review on the open-for-comment (consultation) fetch', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<PolicyList publicView="reviewed" />);
    await screen.findByText('Federal Heat Reuse Act');

    fireEvent.click(screen.getByRole('button', { name: 'Open for comment' }));
    await screen.findByText('Open Comment Draft Rule');

    const consultationCall = fetchMock.mock.calls.find(
      ([url]) => String(url).includes('lifecycle_stage=consultation'),
    );
    expect(new URL(consultationCall[0]).searchParams.get('review')).toBe('reviewed');
  });

  it('carries review on the free-text search fetch', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<PolicyList publicView="reviewed" />);
    await screen.findByText('Federal Heat Reuse Act');

    fireEvent.change(screen.getByLabelText('Search policies'), { target: { value: 'sw' } });

    await waitFor(() => {
      const searchCall = fetchMock.mock.calls.find(
        ([url]) => String(url).includes('/api/policies/search'),
      );
      expect(searchCall).toBeDefined();
      expect(new URL(searchCall[0]).searchParams.get('review')).toBe('reviewed');
    }, { timeout: 2000 });
  });
});
