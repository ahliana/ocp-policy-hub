import { renderHook, waitFor } from '@testing-library/react';
import useCostEstimate from './useCostEstimate';
import { setAdminToken } from '../utils/adminAuth';

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
});
