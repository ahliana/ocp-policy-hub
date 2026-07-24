import { render, screen, waitFor } from '@testing-library/react';
import App from './App';

test('renders app heading', () => {
  render(<App />);
  const linkElement = screen.getByRole('heading', { level: 1, name: /Policy Pulse/i });
  expect(linkElement).toBeInTheDocument();
});

// --- Public review visibility: initial toggle position from /health ---

function mockFetch(publicReviewVisibility) {
  return jest.fn(async (url) => {
    const path = String(url);
    if (path.includes('/health')) {
      return {
        ok: true,
        json: async () => ({
          status: 'ok',
          admin_required: false,
          public_review_visibility: publicReviewVisibility,
        }),
      };
    }
    if (path.includes('/api/coverage')) {
      return {
        ok: true,
        json: async () => ({ countries: [], supranational: [], totals: { sources: 0, policies: 0 } }),
      };
    }
    if (path.includes('/api/policies')) {
      return { ok: true, json: async () => ({ policies: [], count: 0 }) };
    }
    if (path.includes('/api/tags')) {
      return { ok: true, json: async () => ({}) };
    }
    return { ok: false, json: async () => ({}) };
  });
}

afterEach(() => {
  jest.restoreAllMocks();
});

describe('App public view toggle initial position', () => {
  it('shows the toggle with "All finds" active under default_all', async () => {
    global.fetch = mockFetch('default_all');
    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'All finds' })).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'All finds' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Reviewed only' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('shows the toggle with "Reviewed only" active under default_reviewed', async () => {
    global.fetch = mockFetch('default_reviewed');
    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Reviewed only' })).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Reviewed only' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('hides the toggle entirely under reviewed_only', async () => {
    global.fetch = mockFetch('reviewed_only');
    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1, name: /Policy Pulse/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'All finds' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reviewed only' })).not.toBeInTheDocument();
  });
});
