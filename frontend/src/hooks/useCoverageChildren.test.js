import { renderHook, waitFor } from '@testing-library/react';
import useCoverageChildren from './useCoverageChildren';

const CHILDREN_RESPONSE = {
  parent: { slug: 'us', name: 'United States', iso_numeric: '840' },
  national: { sources: 6, policies: 6, top_policy_names: ['Federal Heat Reuse Act'] },
  children: [
    { slug: 'us-mn', name: 'Minnesota', kind: 'us_state', code: 'US-MN', sources: 3, policies: 5, top_policy_names: [] },
  ],
  totals: { sources: 8, policies: 11 },
};

afterEach(() => {
  jest.restoreAllMocks();
});

describe('useCoverageChildren', () => {
  it('does not fetch when slug is null', () => {
    global.fetch = jest.fn();
    const { result } = renderHook(() => useCoverageChildren(null));

    expect(global.fetch).not.toHaveBeenCalled();
    expect(result.current).toEqual({ data: null, error: null, isLoading: false });
  });

  it('fetches children for a slug and returns the parsed response', async () => {
    global.fetch = jest.fn(async (url) => {
      expect(String(url)).toContain('/api/coverage/children?parent=us');
      return { ok: true, json: async () => CHILDREN_RESPONSE };
    });

    const { result } = renderHook(() => useCoverageChildren('us'));

    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.data).toEqual(CHILDREN_RESPONSE));
    expect(result.current.error).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });

  it('surfaces a 404 (unknown parent) as an error, not a crash', async () => {
    global.fetch = jest.fn(async () => ({ ok: false, status: 404, text: async () => 'not found' }));

    const { result } = renderHook(() => useCoverageChildren('nowhere'));

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error.message).toContain('404');
    expect(result.current.data).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });

  it('refetches when the slug changes', async () => {
    const fetchMock = jest.fn(async (url) => {
      if (String(url).includes('parent=us')) return { ok: true, json: async () => CHILDREN_RESPONSE };
      return {
        ok: true,
        json: async () => ({
          parent: { slug: 'belgium', name: 'Belgium', iso_numeric: '056' },
          national: { sources: 0, policies: 0, top_policy_names: [] },
          children: [],
          totals: { sources: 0, policies: 0 },
        }),
      };
    });
    global.fetch = fetchMock;

    const { result, rerender } = renderHook(({ slug }) => useCoverageChildren(slug), {
      initialProps: { slug: 'us' },
    });
    await waitFor(() => expect(result.current.data?.parent.slug).toBe('us'));

    rerender({ slug: 'belgium' });
    await waitFor(() => expect(result.current.data?.parent.slug).toBe('belgium'));
  });

  it('carries a review param on the request when provided', async () => {
    const fetchMock = jest.fn(async (url) => {
      expect(new URL(String(url)).searchParams.get('review')).toBe('reviewed');
      return { ok: true, json: async () => CHILDREN_RESPONSE };
    });
    global.fetch = fetchMock;

    renderHook(() => useCoverageChildren('us', 'reviewed'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it('refetches when the review param changes', async () => {
    const fetchMock = jest.fn(async () => ({ ok: true, json: async () => CHILDREN_RESPONSE }));
    global.fetch = fetchMock;

    const { rerender } = renderHook(
      ({ review }) => useCoverageChildren('us', review),
      { initialProps: { review: 'all' } },
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    rerender({ review: 'reviewed' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});
