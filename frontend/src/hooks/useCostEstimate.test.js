import { act, configure, renderHook, waitFor } from '@testing-library/react';
import useCostEstimate from './useCostEstimate';
import { setAdminToken } from '../utils/adminAuth';

// The hook debounces its fetch by 300ms (WP-17); give waitFor enough real-time
// headroom above that so a slow CI machine doesn't turn the debounce itself
// into a flaky timeout.
configure({ asyncUtilTimeout: 3000 });

const ESTIMATE_RESPONSE = {
  domain_count: 5,
  estimated_pages: 500,
  estimated_keyword_passes: 50,
  estimated_screening_calls: 50,
  estimated_analysis_calls: 25,
  estimated_cost_usd: 4.2,
};

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

// WP-27: every non-discover selection now fetches BOTH the standard
// (deep=false) and deep (deep=true) estimate in the same debounced window,
// so a plain response-per-call mock (used by most tests below, which don't
// care which of the pair they're answering) resolves to the same body for
// both calls.
function deepParam(url) {
  return new URL(String(url)).searchParams.get('deep');
}

afterEach(() => {
  jest.restoreAllMocks();
  setAdminToken('');
});

describe('useCostEstimate', () => {
  it('attaches admin headers on the estimate request', async () => {
    setAdminToken('secret-token');
    const fetchMock = jest.fn(async () => jsonResponse(200, ESTIMATE_RESPONSE));
    global.fetch = fetchMock;

    renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, options] = fetchMock.mock.calls[0];
    expect(options.headers['X-Admin-Token']).toBe('secret-token');
  });

  it('makes exactly one aggregated call per channel (standard + deep) for a multi-region selection', async () => {
    const fetchMock = jest.fn(async () => jsonResponse(200, ESTIMATE_RESPONSE));
    global.fetch = fetchMock;

    const usRegions = Array.from({ length: 50 }, (_, i) => `region:us-state-${i}`);
    const { result } = renderHook(() => (
      useCostEstimate({ selectedRegions: usRegions, mode: 'standard' })
    ));

    await waitFor(() => expect(result.current.costStatus).toBe('ready'));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('joins the selected targets into a single comma-separated domains param on every call', async () => {
    const fetchMock = jest.fn(async (url) => {
      expect(new URL(String(url)).searchParams.get('domains')).toBe('california,legiscan_api');
      return jsonResponse(200, ESTIMATE_RESPONSE);
    });
    global.fetch = fetchMock;

    const { result } = renderHook(() => (
      useCostEstimate({ selectedRegions: ['region:california', 'legiscan_api'], mode: 'standard' })
    ));

    await waitFor(() => expect(result.current.costStatus).toBe('ready'));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  describe('parallel standard + deep fetch (WP-27)', () => {
    it('fires one deep=false call and one deep=true call in the same window, regardless of the selected mode', async () => {
      const fetchMock = jest.fn(async () => jsonResponse(200, ESTIMATE_RESPONSE));
      global.fetch = fetchMock;

      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      await waitFor(() => expect(result.current.costStatus).toBe('ready'));
      expect(fetchMock).toHaveBeenCalledTimes(2);
      const params = fetchMock.mock.calls.map(([url]) => deepParam(url));
      expect(params.sort()).toEqual([null, 'true'].sort()); // one omits deep, one sets deep=true
    });

    it('fires the same pair of calls in deep mode too', async () => {
      const fetchMock = jest.fn(async () => jsonResponse(200, ESTIMATE_RESPONSE));
      global.fetch = fetchMock;

      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'deep' }));

      await waitFor(() => expect(result.current.costStatus).toBe('ready'));
      expect(fetchMock).toHaveBeenCalledTimes(2);
      const params = fetchMock.mock.calls.map(([url]) => deepParam(url));
      expect(params.sort()).toEqual([null, 'true'].sort());
    });

    it('exposes standardEstimate and deepEstimate once ready', async () => {
      const fetchMock = jest.fn(async (url) => (
        deepParam(url) === 'true'
          ? jsonResponse(200, { ...ESTIMATE_RESPONSE, estimated_cost_usd: 9.0 })
          : jsonResponse(200, { ...ESTIMATE_RESPONSE, estimated_cost_usd: 4.2 })
      ));
      global.fetch = fetchMock;

      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      await waitFor(() => expect(result.current.costStatus).toBe('ready'));
      expect(result.current.standardEstimate.estimated_cost_usd).toBe(4.2);
      expect(result.current.deepEstimate.estimated_cost_usd).toBe(9.0);
    });

    it('costEstimate (selected mode) matches standardEstimate in standard mode', async () => {
      const fetchMock = jest.fn(async (url) => (
        deepParam(url) === 'true'
          ? jsonResponse(200, { ...ESTIMATE_RESPONSE, estimated_cost_usd: 9.0 })
          : jsonResponse(200, { ...ESTIMATE_RESPONSE, estimated_cost_usd: 4.2 })
      ));
      global.fetch = fetchMock;

      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      await waitFor(() => expect(result.current.costStatus).toBe('ready'));
      expect(result.current.costEstimate.estimated_cost_usd).toBe(4.2);
    });

    it('costEstimate (selected mode) matches deepEstimate in deep mode', async () => {
      const fetchMock = jest.fn(async (url) => (
        deepParam(url) === 'true'
          ? jsonResponse(200, { ...ESTIMATE_RESPONSE, estimated_cost_usd: 9.0 })
          : jsonResponse(200, { ...ESTIMATE_RESPONSE, estimated_cost_usd: 4.2 })
      ));
      global.fetch = fetchMock;

      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'deep' }));

      await waitFor(() => expect(result.current.costStatus).toBe('ready'));
      expect(result.current.costEstimate.estimated_cost_usd).toBe(9.0);
    });

    it('standardEstimate and deepEstimate are null in discover mode (no fetch)', async () => {
      const fetchMock = jest.fn();
      global.fetch = fetchMock;

      const { result } = renderHook(() => (
        useCostEstimate({ selectedRegions: ['quick'], mode: 'discover' })
      ));

      await waitFor(() => expect(result.current.costStatus).toBe('discover'));
      expect(fetchMock).not.toHaveBeenCalled();
      expect(result.current.standardEstimate).toBeNull();
      expect(result.current.deepEstimate).toBeNull();
    });

    it('standardEstimate and deepEstimate are null while idle (nothing selected)', () => {
      const { result } = renderHook(() => useCostEstimate({ selectedRegions: [], mode: 'standard' }));

      expect(result.current.standardEstimate).toBeNull();
      expect(result.current.deepEstimate).toBeNull();
    });

    it('standardEstimate and deepEstimate are null again after an error response', async () => {
      global.fetch = jest.fn(async () => jsonResponse(400, {}));
      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      await waitFor(() => expect(result.current.costStatus).toBe('bad_scope'));
      expect(result.current.standardEstimate).toBeNull();
      expect(result.current.deepEstimate).toBeNull();
    });
  });

  it('shows an explanatory line in discover mode without calling fetch', async () => {
    const fetchMock = jest.fn();
    global.fetch = fetchMock;

    const { result } = renderHook(() => (
      useCostEstimate({ selectedRegions: ['quick'], mode: 'discover' })
    ));

    await waitFor(() => expect(result.current.costStatus).toBe('discover'));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.costEstimateText).not.toBe('Cost estimates are only available in standard mode.');
    expect(result.current.costEstimateText.length).toBeGreaterThan(0);
  });

  it('shows an explanatory idle message when nothing is selected (WP-17)', () => {
    const { result } = renderHook(() => useCostEstimate({ selectedRegions: [], mode: 'standard' }));

    expect(result.current.costStatus).toBe('idle');
    expect(result.current.costEstimateText).toBe(
      'Pick a place or sources above to see the cost before anything runs.',
    );
  });

  it('maps a 401 to a sign-in message', async () => {
    global.fetch = jest.fn(async () => jsonResponse(401, {}));
    const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

    await waitFor(() => expect(result.current.costEstimateText).toBe('Sign in as admin to see estimates.'));
  });

  it('maps a 403 to a sign-in message', async () => {
    global.fetch = jest.fn(async () => jsonResponse(403, {}));
    const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

    await waitFor(() => expect(result.current.costEstimateText).toBe('Sign in as admin to see estimates.'));
  });

  it('maps a 400 to an unknown-scope message', async () => {
    global.fetch = jest.fn(async () => jsonResponse(400, {}));
    const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

    await waitFor(() => expect(result.current.costEstimateText).toBe('Unknown scan scope.'));
  });

  it('maps a network failure to "Estimate unavailable"', async () => {
    global.fetch = jest.fn(async () => { throw new Error('network down'); });
    const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

    await waitFor(() => expect(result.current.costEstimateText).toBe('Estimate unavailable'));
  });

  it('maps an unexpected 500 to "Estimate unavailable"', async () => {
    global.fetch = jest.fn(async () => jsonResponse(500, {}));
    const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

    await waitFor(() => expect(result.current.costEstimateText).toBe('Estimate unavailable'));
  });

  it('renders the numeric estimate on success', async () => {
    global.fetch = jest.fn(async () => jsonResponse(200, ESTIMATE_RESPONSE));
    const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

    await waitFor(() => expect(result.current.costStatus).toBe('ready'));
    expect(result.current.costEstimateText).toBe('$4.20 (5 targets)');
  });

  it('the literal standard-mode-only string is gone from the codebase behavior', async () => {
    global.fetch = jest.fn(async () => jsonResponse(200, ESTIMATE_RESPONSE));
    const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'deep' }));

    await waitFor(() => expect(result.current.costStatus).toBe('ready'));
    expect(result.current.costEstimateText).not.toBe('Cost estimates are only available in standard mode.');
  });

  describe('domainCount (WP-6 scan-scope summary)', () => {
    it('is null while loading', () => {
      global.fetch = jest.fn(() => new Promise(() => {})); // never resolves
      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      expect(result.current.domainCount).toBeNull();
    });

    it('is null when idle (nothing selected)', () => {
      const { result } = renderHook(() => useCostEstimate({ selectedRegions: [], mode: 'standard' }));
      expect(result.current.domainCount).toBeNull();
    });

    it('exposes the estimate response domain_count once ready', async () => {
      global.fetch = jest.fn(async () => jsonResponse(200, ESTIMATE_RESPONSE));
      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      await waitFor(() => expect(result.current.costStatus).toBe('ready'));
      expect(result.current.domainCount).toBe(5);
    });

    it('is null again after an error response', async () => {
      global.fetch = jest.fn(async () => jsonResponse(400, {}));
      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      await waitFor(() => expect(result.current.costStatus).toBe('bad_scope'));
      expect(result.current.domainCount).toBeNull();
    });
  });

  describe('cost range formatting (WP-26)', () => {
    it('formats a range when low/high are present and differ', async () => {
      const response = {
        ...ESTIMATE_RESPONSE,
        estimated_cost_usd: 6.15,
        estimated_cost_low_usd: 4.2,
        estimated_cost_high_usd: 9.1,
      };
      global.fetch = jest.fn(async () => jsonResponse(200, response));
      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      await waitFor(() => expect(result.current.costStatus).toBe('ready'));
      expect(result.current.costEstimateText).toBe('$4.20-$9.10 (typically ~$6.15) (5 targets)');
    });

    it('falls back to the point-estimate text when the band fields are absent (backward compatible)', async () => {
      global.fetch = jest.fn(async () => jsonResponse(200, ESTIMATE_RESPONSE));
      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      await waitFor(() => expect(result.current.costStatus).toBe('ready'));
      expect(result.current.costEstimateText).toBe('$4.20 (5 targets)');
    });

    it('falls back to the point-estimate text when low equals high (no real spread)', async () => {
      const response = {
        ...ESTIMATE_RESPONSE,
        estimated_cost_usd: 4.2,
        estimated_cost_low_usd: 4.2,
        estimated_cost_high_usd: 4.2,
      };
      global.fetch = jest.fn(async () => jsonResponse(200, response));
      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      await waitFor(() => expect(result.current.costStatus).toBe('ready'));
      expect(result.current.costEstimateText).toBe('$4.20 (5 targets)');
    });
  });

  describe('raw costEstimate exposure (WP-26)', () => {
    const CHANNELS_RESPONSE = {
      ...ESTIMATE_RESPONSE,
      auditor_cost_usd: 0.35,
      assumptions: ['Assumes 100 pages per site (measured).'],
      channels: {
        crawl: {
          domain_count: 5, estimated_items_or_pages: 500, screening_calls: 50,
          analysis_calls: 25, cost_usd: 4.2, cost_low_usd: 3.0, cost_high_usd: 6.0,
        },
      },
    };

    it('exposes the raw estimate object, including channels and assumptions, once ready', async () => {
      global.fetch = jest.fn(async () => jsonResponse(200, CHANNELS_RESPONSE));
      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      await waitFor(() => expect(result.current.costStatus).toBe('ready'));
      expect(result.current.costEstimate.channels).toEqual(CHANNELS_RESPONSE.channels);
      expect(result.current.costEstimate.assumptions).toEqual(CHANNELS_RESPONSE.assumptions);
      expect(result.current.costEstimate.auditor_cost_usd).toBe(0.35);
    });

    it('is null while idle (nothing selected)', () => {
      const { result } = renderHook(() => useCostEstimate({ selectedRegions: [], mode: 'standard' }));
      expect(result.current.costEstimate).toBeNull();
    });

    it('is null again after an error response', async () => {
      global.fetch = jest.fn(async () => jsonResponse(400, {}));
      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      await waitFor(() => expect(result.current.costStatus).toBe('bad_scope'));
      expect(result.current.costEstimate).toBeNull();
    });
  });

  describe('debounce (WP-17)', () => {
    afterEach(() => {
      jest.useRealTimers();
    });

    it('coalesces two rapid selection changes into exactly one pair of fetch calls', () => {
      jest.useFakeTimers();
      const fetchMock = jest.fn(() => new Promise(() => {})); // never resolves - only call count matters here
      global.fetch = fetchMock;

      const { rerender } = renderHook(
        ({ selectedRegions }) => useCostEstimate({ selectedRegions, mode: 'standard' }),
        { initialProps: { selectedRegions: ['quick'] } },
      );

      // First change starts a 300ms timer; a second change 100ms later must
      // cancel it and start a fresh one, rather than adding another pair of
      // calls.
      act(() => { jest.advanceTimersByTime(100); });
      rerender({ selectedRegions: ['quick', 'eu'] });

      act(() => { jest.advanceTimersByTime(299); });
      expect(fetchMock).not.toHaveBeenCalled();

      act(() => { jest.advanceTimersByTime(1); });
      expect(fetchMock).toHaveBeenCalledTimes(2);

      for (const [calledUrl] of fetchMock.mock.calls) {
        expect(new URL(String(calledUrl)).searchParams.get('domains')).toBe('quick,eu');
      }
    });

    it('fires immediately-set costStatus of "loading" before the debounced fetch resolves', () => {
      jest.useFakeTimers();
      const fetchMock = jest.fn(() => new Promise(() => {}));
      global.fetch = fetchMock;

      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      expect(result.current.costStatus).toBe('loading');
      expect(fetchMock).not.toHaveBeenCalled();

      act(() => { jest.advanceTimersByTime(300); });
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
  });
});
