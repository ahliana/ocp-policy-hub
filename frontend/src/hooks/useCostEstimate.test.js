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

  it('makes exactly one aggregated call for a multi-region selection', async () => {
    const fetchMock = jest.fn(async () => jsonResponse(200, ESTIMATE_RESPONSE));
    global.fetch = fetchMock;

    const usRegions = Array.from({ length: 50 }, (_, i) => `region:us-state-${i}`);
    const { result } = renderHook(() => (
      useCostEstimate({ selectedRegions: usRegions, mode: 'standard' })
    ));

    await waitFor(() => expect(result.current.costStatus).toBe('ready'));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('joins the selected targets into a single comma-separated domains param', async () => {
    const fetchMock = jest.fn(async (url) => {
      expect(new URL(String(url)).searchParams.get('domains')).toBe('california,legiscan_api');
      return jsonResponse(200, ESTIMATE_RESPONSE);
    });
    global.fetch = fetchMock;

    renderHook(() => (
      useCostEstimate({ selectedRegions: ['region:california', 'legiscan_api'], mode: 'standard' })
    ));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it('requests deep=true when in deep mode', async () => {
    const fetchMock = jest.fn(async (url) => {
      expect(new URL(String(url)).searchParams.get('deep')).toBe('true');
      return jsonResponse(200, ESTIMATE_RESPONSE);
    });
    global.fetch = fetchMock;

    renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'deep' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it('does not send deep=true in standard mode', async () => {
    const fetchMock = jest.fn(async (url) => {
      expect(new URL(String(url)).searchParams.get('deep')).toBeNull();
      return jsonResponse(200, ESTIMATE_RESPONSE);
    });
    global.fetch = fetchMock;

    renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
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

  describe('debounce (WP-17)', () => {
    afterEach(() => {
      jest.useRealTimers();
    });

    it('coalesces two rapid selection changes into exactly one fetch call', () => {
      jest.useFakeTimers();
      const fetchMock = jest.fn(() => new Promise(() => {})); // never resolves - only call count matters here
      global.fetch = fetchMock;

      const { rerender } = renderHook(
        ({ selectedRegions }) => useCostEstimate({ selectedRegions, mode: 'standard' }),
        { initialProps: { selectedRegions: ['quick'] } },
      );

      // First change starts a 300ms timer; a second change 100ms later must
      // cancel it and start a fresh one, rather than adding a second call.
      act(() => { jest.advanceTimersByTime(100); });
      rerender({ selectedRegions: ['quick', 'eu'] });

      act(() => { jest.advanceTimersByTime(299); });
      expect(fetchMock).not.toHaveBeenCalled();

      act(() => { jest.advanceTimersByTime(1); });
      expect(fetchMock).toHaveBeenCalledTimes(1);

      const [calledUrl] = fetchMock.mock.calls[0];
      expect(new URL(String(calledUrl)).searchParams.get('domains')).toBe('quick,eu');
    });

    it('fires immediately-set costStatus of "loading" before the debounced fetch resolves', () => {
      jest.useFakeTimers();
      const fetchMock = jest.fn(() => new Promise(() => {}));
      global.fetch = fetchMock;

      const { result } = renderHook(() => useCostEstimate({ selectedRegions: ['quick'], mode: 'standard' }));

      expect(result.current.costStatus).toBe('loading');
      expect(fetchMock).not.toHaveBeenCalled();

      act(() => { jest.advanceTimersByTime(300); });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
  });
});
