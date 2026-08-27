import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import LeadsInbox from './LeadsInbox';
import { setAdminToken } from '../utils/adminAuth';

const URL_TIP = {
  lead_id: 'tip-url-1',
  title: 'Denmark heat mandate',
  source_url: 'https://news.example/article',
  snippet: 'A note about it',
  origin: 'community',
  status: 'new',
};

const NOTE_ONLY_TIP = {
  lead_id: 'tip-note-1',
  title: 'Heard Ohio is drafting something',
  source_url: '',
  snippet: 'Heard Ohio is drafting something',
  origin: 'community',
  status: 'new',
};

function mockFetch(tips = [URL_TIP, NOTE_ONLY_TIP], { signalsStatus } = {}) {
  return jest.fn(async (url, options = {}) => {
    const s = String(url);
    const method = options.method || 'GET';
    if (s.includes('/api/signals/status')) {
      if (signalsStatus === undefined) {
        return { ok: false, status: 403, json: async () => ({}) };
      }
      if (signalsStatus.status && signalsStatus.status !== 200) {
        return { ok: false, status: signalsStatus.status, json: async () => ({}) };
      }
      return { ok: true, json: async () => signalsStatus.body, headers: options.headers };
    }
    if (s.includes('/api/tips') && method === 'GET') {
      return { ok: true, json: async () => ({ leads: tips, count: tips.length }) };
    }
    if (s.includes('/api/tips') && method === 'POST' && !s.includes('/dismiss') && !s.includes('/chase')) {
      return { ok: true, json: async () => ({ lead_id: 'new-tip', status: 'new' }) };
    }
    if (s.includes('/chase')) {
      return { ok: true, json: async () => ({ lead_id: 'x', status: 'chased', analysis: {} }) };
    }
    if (s.includes('/dismiss')) {
      return { ok: true, json: async () => ({ lead_id: 'x', status: 'dismissed' }) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });
}

afterEach(() => {
  jest.restoreAllMocks();
  setAdminToken(null);
});

describe('LeadsInbox uses Tips vocabulary and /api/tips', () => {
  it('fetches from /api/tips on mount (all non-dismissed tips, not just new)', async () => {
    global.fetch = mockFetch();
    render(<LeadsInbox />);

    await screen.findByText('Denmark heat mandate');
    const getCall = global.fetch.mock.calls.find((call) => String(call[0]).includes('/api/tips')
      && !String(call[0]).includes('chase') && !String(call[0]).includes('dismiss')
      && (call[1] === undefined || (call[1].method || 'GET') === 'GET'));
    expect(getCall).toBeDefined();
    // Not filtered to status=new - chased tips must still load so their
    // outcome can be shown (see "LeadsInbox chase outcomes" below).
    expect(String(getCall[0])).not.toContain('status=new');
  });

  it('shows "Tips" in the header, not "Leads"', async () => {
    global.fetch = mockFetch();
    render(<LeadsInbox />);

    await screen.findByText('Denmark heat mandate');
    expect(screen.getByText(/Tips/)).toBeInTheDocument();
    expect(screen.queryByText(/^Leads/)).not.toBeInTheDocument();
  });

  it('submits a new tip via POST /api/tips', async () => {
    global.fetch = mockFetch();
    render(<LeadsInbox />);
    await screen.findByText('Denmark heat mandate');

    fireEvent.change(screen.getByLabelText('Policy URL'), {
      target: { value: 'https://example.gov/new-law' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Suggest/ }));

    await waitFor(() => {
      const postCall = global.fetch.mock.calls.find(
        (call) => String(call[0]).includes('/api/tips') && call[1]?.method === 'POST'
          && !String(call[0]).includes('chase') && !String(call[0]).includes('dismiss'),
      );
      expect(postCall).toBeDefined();
    });
  });

  it('chases a tip via POST /api/tips/{id}/chase', async () => {
    global.fetch = mockFetch();
    render(<LeadsInbox />);
    await screen.findByText('Denmark heat mandate');

    const chaseButtons = screen.getAllByRole('button', { name: /Chase/ });
    fireEvent.click(chaseButtons[0]);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/tips/tip-url-1/chase'),
        expect.objectContaining({ method: 'POST' }),
      );
    });
  });

  it('dismisses a tip via POST /api/tips/{id}/dismiss', async () => {
    global.fetch = mockFetch();
    render(<LeadsInbox />);
    await screen.findByText('Denmark heat mandate');

    const dismissButtons = screen.getAllByRole('button', { name: /Dismiss/ });
    fireEvent.click(dismissButtons[0]);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/tips/tip-url-1/dismiss'),
        expect.objectContaining({ method: 'POST' }),
      );
    });
  });
});

describe('LeadsInbox note-only tips (hearsay)', () => {
  it('marks a note-only tip as hearsay and hides its Chase button', async () => {
    global.fetch = mockFetch();
    render(<LeadsInbox />);

    await screen.findByText('Heard Ohio is drafting something');
    const noteCard = screen.getByText('Heard Ohio is drafting something').closest('li');
    expect(noteCard).toHaveTextContent(/hearsay/i);
    expect(noteCard).toHaveTextContent(/needs a human/i);
    expect(within(noteCard).queryByRole('button', { name: /Chase/ })).not.toBeInTheDocument();
    expect(within(noteCard).getByRole('button', { name: /Dismiss/ })).toBeInTheDocument();
  });

  it('a URL tip still shows a chaseable Chase button', async () => {
    global.fetch = mockFetch();
    render(<LeadsInbox />);

    await screen.findByText('Denmark heat mandate');
    const urlCard = screen.getByText('Denmark heat mandate').closest('li');
    expect(within(urlCard).getByRole('button', { name: /Chase/ })).toBeInTheDocument();
  });
});

describe('LeadsInbox cleans encoded HTML in note/title text', () => {
  const ENCODED_TITLE_TIP = {
    lead_id: 'tip-encoded',
    title: '&lt;a href="https://news.google.com/rss/articles/CBMi123abc"&gt;'
      + 'Netherlands drafts heat reuse rule&lt;/a&gt;',
    source_url: 'https://news.google.com/rss/articles/CBMi123abc',
    snippet: '',
    origin: 'news',
    status: 'new',
  };

  const NO_TITLE_REDIRECT_TIP = {
    lead_id: 'tip-notitle',
    title: '',
    source_url: 'https://news.google.com/rss/articles/CBMi999xyz',
    snippet: '',
    origin: 'news',
    status: 'new',
  };

  const ENCODED_NOTE_ONLY_TIP = {
    lead_id: 'tip-encoded-note',
    title: '&lt;a href="https://news.google.com/rss/articles/CBMi456"&gt;Belgium rumor&lt;/a&gt;',
    source_url: '',
    snippet: '&lt;a href="https://news.google.com/rss/articles/CBMi456"&gt;Belgium rumor&lt;/a&gt;',
    origin: 'community',
    status: 'new',
  };

  it('renders the decoded title text, never the raw angle-bracket markup', async () => {
    global.fetch = mockFetch([ENCODED_TITLE_TIP]);
    render(<LeadsInbox />);

    await screen.findByText('Netherlands drafts heat reuse rule');
    expect(screen.queryByText(/&lt;a href/)).not.toBeInTheDocument();
    expect(screen.queryByText(/<a href/)).not.toBeInTheDocument();
  });

  it('falls back to a friendly label when title and note are empty and the URL is a bare Google News redirect', async () => {
    global.fetch = mockFetch([NO_TITLE_REDIRECT_TIP]);
    render(<LeadsInbox />);

    await screen.findByText('Untitled source');
    expect(screen.queryByText(/news\.google\.com\/rss/)).not.toBeInTheDocument();
  });

  it('cleans encoded markup in a note-only tip title too', async () => {
    global.fetch = mockFetch([ENCODED_NOTE_ONLY_TIP]);
    render(<LeadsInbox />);

    await screen.findByText('Belgium rumor');
    expect(screen.queryByText(/&lt;a href/)).not.toBeInTheDocument();
  });
});

describe('LeadsInbox chase outcomes', () => {
  const POLICY_FOUND_TIP = {
    lead_id: 'tip-found',
    title: 'Sweden Heat Rule',
    source_url: 'https://gov.example/sweden-law',
    snippet: '',
    origin: 'news',
    status: 'chased',
    policy_url: 'https://gov.example/sweden-law-final',
    chase_outcome: 'policy_found',
    chased_at: '2026-07-20T10:00:00Z',
  };

  const NO_POLICY_TIP = {
    lead_id: 'tip-no-policy',
    title: 'Norway Rumor',
    source_url: 'https://gov.example/norway',
    snippet: '',
    origin: 'community',
    status: 'chased',
    policy_url: null,
    chase_outcome: 'no_policy',
    chased_at: '2026-07-21T09:00:00Z',
  };

  const FETCH_FAILED_TIP = {
    lead_id: 'tip-failed',
    title: 'Google News Wrapper',
    source_url: 'https://news.google.com/rss/articles/xyz',
    snippet: '',
    origin: 'news',
    status: 'new',
    policy_url: null,
    chase_outcome: 'fetch_failed',
    chase_error: 'too many redirects',
    chased_at: '2026-07-22T08:00:00Z',
  };

  const DISMISSED_TIP = {
    lead_id: 'tip-dismissed',
    title: 'Should not appear',
    source_url: 'https://gov.example/dismissed',
    snippet: '',
    origin: 'community',
    status: 'dismissed',
  };

  it('shows a link to the found policy with when it was chased', async () => {
    global.fetch = mockFetch([POLICY_FOUND_TIP]);
    render(<LeadsInbox />);

    const card = (await screen.findByText('Sweden Heat Rule')).closest('li');
    expect(within(card).getByText(/found/i)).toBeInTheDocument();
    expect(within(card).getByRole('link', { name: /sweden-law-final/ })).toHaveAttribute(
      'href', 'https://gov.example/sweden-law-final',
    );
  });

  it('shows "checked - nothing found" with when for a no-policy outcome', async () => {
    global.fetch = mockFetch([NO_POLICY_TIP]);
    render(<LeadsInbox />);

    const card = (await screen.findByText('Norway Rumor')).closest('li');
    expect(within(card)).toBeTruthy();
    expect(card).toHaveTextContent(/checked.*nothing found/i);
  });

  it('shows a fetch-failed outcome with the reason and keeps the tip chaseable', async () => {
    global.fetch = mockFetch([FETCH_FAILED_TIP]);
    render(<LeadsInbox />);

    const card = (await screen.findByText('Google News Wrapper')).closest('li');
    expect(card).toHaveTextContent(/failed/i);
    expect(card).toHaveTextContent(/too many redirects/i);
    expect(within(card).getByRole('button', { name: /Chase/ })).toBeInTheDocument();
  });

  it('never shows a dismissed tip', async () => {
    global.fetch = mockFetch([POLICY_FOUND_TIP, DISMISSED_TIP]);
    render(<LeadsInbox />);

    await screen.findByText('Sweden Heat Rule');
    expect(screen.queryByText('Should not appear')).not.toBeInTheDocument();
  });
});

describe('LeadsInbox last-sweep status line (WP-43 UI)', () => {
  const OK_STATUS = {
    ran_at: '2026-08-20T06:00:00Z',
    feeds_tried: 5,
    feeds_ok: 4,
    feeds_failed: 1,
    failures: [{ feed: 'Denmark RSS', reason: 'timed out' }],
    items_found: 12,
    kept_after_triage: 6,
    added_after_dedupe: 3,
  };

  it('renders the last-sweep sentence above the tips list', async () => {
    global.fetch = mockFetch([URL_TIP], { signalsStatus: { body: OK_STATUS } });
    render(<LeadsInbox />);

    const sentence = await screen.findByText(/Last sweep:/);
    expect(sentence).toHaveTextContent('3 new tips');
    expect(sentence).toHaveTextContent('4 feeds ok');
    expect(sentence).toHaveTextContent('1 failed');
  });

  it('shows an InfoHotspot listing each feed failure and how to fix it', async () => {
    global.fetch = mockFetch([URL_TIP], { signalsStatus: { body: OK_STATUS } });
    render(<LeadsInbox />);

    await screen.findByText(/Last sweep:/);
    const hotspotButton = screen.getByRole('button', { name: 'Which feeds failed' });
    fireEvent.click(hotspotButton);
    expect(screen.getByText(/Denmark RSS: timed out/)).toBeInTheDocument();
    expect(screen.getByText(/turn the feed off in configuration/i)).toBeInTheDocument();
  });

  it('does not show a failure hotspot when no feeds failed', async () => {
    global.fetch = mockFetch([URL_TIP], {
      signalsStatus: { body: { ...OK_STATUS, feeds_failed: 0, failures: [] } },
    });
    render(<LeadsInbox />);

    await screen.findByText(/Last sweep:/);
    expect(screen.queryByRole('button', { name: 'Which feeds failed' })).not.toBeInTheDocument();
  });

  it('shows "No sweep recorded yet." when the record is empty', async () => {
    global.fetch = mockFetch([URL_TIP], { signalsStatus: { body: {} } });
    render(<LeadsInbox />);

    await screen.findByText('No sweep recorded yet.');
    expect(screen.queryByText(/Last sweep:/)).not.toBeInTheDocument();
  });

  it('sends the admin header on the signals/status fetch', async () => {
    setAdminToken('secret-token');
    const fetchMock = mockFetch([URL_TIP], { signalsStatus: { body: OK_STATUS } });
    global.fetch = fetchMock;
    render(<LeadsInbox />);

    await screen.findByText(/Last sweep:/);
    const statusCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/api/signals/status'));
    expect(statusCall).toBeDefined();
    expect(statusCall[1]?.headers?.['X-Admin-Token']).toBe('secret-token');
  });

  it('renders nothing (not an error) when the status fetch 403s (not signed in)', async () => {
    global.fetch = mockFetch([URL_TIP], { signalsStatus: { status: 403 } });
    render(<LeadsInbox />);

    await screen.findByText('Denmark heat mandate');
    expect(screen.queryByText(/Last sweep:/)).not.toBeInTheDocument();
    expect(screen.queryByText('No sweep recorded yet.')).not.toBeInTheDocument();
    expect(screen.queryByText(/error/i)).not.toBeInTheDocument();
  });
});
