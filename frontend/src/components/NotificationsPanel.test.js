import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import NotificationsPanel from './NotificationsPanel';
import { setAdminToken } from '../utils/adminAuth';

const SUBSCRIPTION_BOTH = {
  id: 'sub-1', email: 'ops@example.com', topics: ['early_signals', 'ops_alerts'], frequency: 'daily',
};

const SUBSCRIPTION_ONE = {
  id: 'sub-2', email: 'signals@example.com', topics: ['early_signals'], frequency: 'immediate',
};

const DEFAULT_STATUS = { smtp_configured: true, last_digest: null, last_send_error: null };

function jsonResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function mockFetch({
  subscriptions = [SUBSCRIPTION_BOTH, SUBSCRIPTION_ONE],
  status = DEFAULT_STATUS,
  onPost,
  onDelete,
  capturedHeaders,
} = {}) {
  return jest.fn(async (url, options) => {
    const parsed = new URL(String(url));
    if (capturedHeaders) capturedHeaders.push(options?.headers || {});

    if (parsed.pathname === '/api/notifications/subscriptions' && (!options || !options.method || options.method === 'GET')) {
      return jsonResponse(200, { subscriptions });
    }
    if (parsed.pathname === '/api/notifications/subscriptions' && options?.method === 'POST') {
      if (onPost) return onPost(JSON.parse(options.body));
      return jsonResponse(200, { id: 'sub-new', ...JSON.parse(options.body) });
    }
    const deleteMatch = parsed.pathname.match(/^\/api\/notifications\/subscriptions\/([^/]+)$/);
    if (deleteMatch && options?.method === 'DELETE') {
      if (onDelete) return onDelete(deleteMatch[1]);
      return jsonResponse(200, { status: 'deleted' });
    }
    if (parsed.pathname === '/api/notifications/status') {
      return jsonResponse(200, status);
    }
    return jsonResponse(404, {});
  });
}

afterEach(() => {
  jest.restoreAllMocks();
  setAdminToken('');
});

describe('NotificationsPanel help note', () => {
  it('shows "How notifications work", closed by default', async () => {
    global.fetch = mockFetch();
    render(<NotificationsPanel />);

    await waitFor(() => expect(screen.getByText('ops@example.com')).toBeInTheDocument());
    const summary = screen.getByText('How notifications work');
    const details = summary.closest('details');
    expect(details).toHaveClass('help-note');
    expect(details).not.toHaveAttribute('open');
  });
});

describe('NotificationsPanel list rendering', () => {
  it('fetches and renders subscription rows with mapped topic and frequency labels', async () => {
    global.fetch = mockFetch();
    render(<NotificationsPanel />);

    await waitFor(() => expect(screen.getByText('ops@example.com')).toBeInTheDocument());

    const rowBoth = screen.getByText('ops@example.com').closest('tr');
    expect(within(rowBoth).getByText('Early signals + Operational alerts')).toBeInTheDocument();
    expect(within(rowBoth).getByText('Daily digest')).toBeInTheDocument();

    const rowOne = screen.getByText('signals@example.com').closest('tr');
    expect(within(rowOne).getByText('Early signals')).toBeInTheDocument();
    expect(within(rowOne).getByText('Immediately')).toBeInTheDocument();
  });

  it('renders the table inside an admin-table-wrap container', async () => {
    global.fetch = mockFetch();
    render(<NotificationsPanel />);

    await waitFor(() => expect(screen.getByText('ops@example.com')).toBeInTheDocument());
    const table = screen.getByRole('table');
    expect(table.closest('.admin-table-wrap')).not.toBeNull();
  });
});

describe('NotificationsPanel empty state', () => {
  it('shows "Nobody is subscribed yet." when there are no subscriptions', async () => {
    global.fetch = mockFetch({ subscriptions: [] });
    render(<NotificationsPanel />);

    expect(await screen.findByText('Nobody is subscribed yet.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});

describe('NotificationsPanel add flow', () => {
  it('submits a new subscription with the expected POST body (immediate)', async () => {
    let capturedBody = null;
    global.fetch = mockFetch({
      subscriptions: [],
      onPost: (body) => {
        capturedBody = body;
        return jsonResponse(200, { id: 'sub-new', ...body });
      },
    });
    render(<NotificationsPanel />);
    await waitFor(() => expect(screen.getByLabelText(/email address/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/email address/i), { target: { value: 'new@example.com' } });
    fireEvent.click(screen.getByLabelText(/early signals/i));
    // frequency defaults to "immediate" (Immediately) - leave as is.
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(capturedBody).not.toBeNull());
    expect(capturedBody.email).toBe('new@example.com');
    expect(capturedBody.topics).toEqual(['early_signals']);
    expect(capturedBody.frequency).toBe('immediate');
  });

  it('maps the frequency select to "daily" and "weekly" machine values', async () => {
    let capturedBody = null;
    global.fetch = mockFetch({
      subscriptions: [],
      onPost: (body) => {
        capturedBody = body;
        return jsonResponse(200, { id: 'sub-new', ...body });
      },
    });
    render(<NotificationsPanel />);
    await waitFor(() => expect(screen.getByLabelText(/email address/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/email address/i), { target: { value: 'weekly@example.com' } });
    fireEvent.click(screen.getByLabelText(/operational alerts/i));
    fireEvent.change(screen.getByLabelText(/how often/i), { target: { value: 'weekly' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(capturedBody).not.toBeNull());
    expect(capturedBody.frequency).toBe('weekly');
    expect(capturedBody.topics).toEqual(['ops_alerts']);
  });

  it('blocks submit with an invalid email and never calls the API', async () => {
    let posted = false;
    global.fetch = mockFetch({
      subscriptions: [],
      onPost: () => { posted = true; return jsonResponse(200, {}); },
    });
    render(<NotificationsPanel />);
    await waitFor(() => expect(screen.getByLabelText(/email address/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/email address/i), { target: { value: 'not-an-email' } });
    fireEvent.click(screen.getByLabelText(/early signals/i));
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    expect(await screen.findByText(/valid email/i)).toBeInTheDocument();
    expect(posted).toBe(false);
  });

  it('blocks submit with no topic selected and never calls the API', async () => {
    let posted = false;
    global.fetch = mockFetch({
      subscriptions: [],
      onPost: () => { posted = true; return jsonResponse(200, {}); },
    });
    render(<NotificationsPanel />);
    await waitFor(() => expect(screen.getByLabelText(/email address/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/email address/i), { target: { value: 'new@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    expect(await screen.findByText(/at least one topic/i)).toBeInTheDocument();
    expect(posted).toBe(false);
  });

  it('shows the server 400 detail on a duplicate subscription', async () => {
    global.fetch = mockFetch({
      subscriptions: [],
      onPost: () => jsonResponse(400, { detail: 'That email is already subscribed.' }),
    });
    render(<NotificationsPanel />);
    await waitFor(() => expect(screen.getByLabelText(/email address/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/email address/i), { target: { value: 'dupe@example.com' } });
    fireEvent.click(screen.getByLabelText(/early signals/i));
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/already subscribed/i));
  });

  it('clears the form and refreshes the list on success', async () => {
    global.fetch = mockFetch({
      subscriptions: [],
      onPost: (body) => jsonResponse(200, { id: 'sub-new', ...body }),
    });
    render(<NotificationsPanel />);
    await waitFor(() => expect(screen.getByLabelText(/email address/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/email address/i), { target: { value: 'fresh@example.com' } });
    fireEvent.click(screen.getByLabelText(/early signals/i));
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(screen.getByLabelText(/email address/i)).toHaveValue(''));
    expect(screen.getByLabelText(/early signals/i)).not.toBeChecked();
  });
});

describe('NotificationsPanel remove flow', () => {
  it('sends a DELETE for the row and refreshes the list', async () => {
    let deletedId = null;
    global.fetch = mockFetch({
      onDelete: (id) => {
        deletedId = id;
        return jsonResponse(200, { status: 'deleted' });
      },
    });
    render(<NotificationsPanel />);
    await waitFor(() => expect(screen.getByText('ops@example.com')).toBeInTheDocument());

    const row = screen.getByText('ops@example.com').closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: /remove/i }));

    await waitFor(() => expect(deletedId).toBe('sub-1'));
  });

  it('shows an error and leaves the row in place if the delete fails', async () => {
    global.fetch = mockFetch({
      onDelete: () => jsonResponse(500, { detail: 'Could not remove.' }),
    });
    render(<NotificationsPanel />);
    await waitFor(() => expect(screen.getByText('ops@example.com')).toBeInTheDocument());

    const row = screen.getByText('ops@example.com').closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: /remove/i }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/could not remove/i));
    expect(screen.getByText('ops@example.com')).toBeInTheDocument();
  });
});

describe('NotificationsPanel status line', () => {
  it('shows a muted note when email sending is not set up', async () => {
    global.fetch = mockFetch({ status: { smtp_configured: false, last_digest: null, last_send_error: null } });
    render(<NotificationsPanel />);

    expect(await screen.findByText(
      'Email sending is not set up yet - subscriptions are saved and digests will start once it is.',
    )).toBeInTheDocument();
  });

  it('does not show the muted note when email sending is configured', async () => {
    global.fetch = mockFetch({ status: DEFAULT_STATUS });
    render(<NotificationsPanel />);

    await waitFor(() => expect(screen.getByText('ops@example.com')).toBeInTheDocument());
    expect(screen.queryByText(/not set up yet/i)).not.toBeInTheDocument();
  });

  it('shows the last send error as a plain warning line', async () => {
    global.fetch = mockFetch({
      status: { smtp_configured: true, last_digest: null, last_send_error: 'connection refused' },
    });
    render(<NotificationsPanel />);

    expect(await screen.findByText('The last email could not be sent: connection refused')).toBeInTheDocument();
  });

  it('the panel stays usable when the status fetch fails entirely', async () => {
    global.fetch = jest.fn(async (url, options) => {
      const parsed = new URL(String(url));
      if (parsed.pathname === '/api/notifications/subscriptions' && (!options || !options.method)) {
        return jsonResponse(200, { subscriptions: [SUBSCRIPTION_BOTH] });
      }
      return jsonResponse(500, {});
    });
    render(<NotificationsPanel />);

    await waitFor(() => expect(screen.getByText('ops@example.com')).toBeInTheDocument());
    expect(screen.queryByText(/not set up yet/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/could not be sent/i)).not.toBeInTheDocument();
  });
});

describe('NotificationsPanel admin headers', () => {
  it('sends the admin token header on every call: list, status, add, and remove', async () => {
    setAdminToken('secret-token');
    const capturedHeaders = [];
    let deleteCalled = false;
    global.fetch = mockFetch({
      capturedHeaders,
      onPost: (body) => jsonResponse(200, { id: 'sub-new', ...body }),
      onDelete: () => { deleteCalled = true; return jsonResponse(200, { status: 'deleted' }); },
    });
    render(<NotificationsPanel />);
    await waitFor(() => expect(screen.getByText('ops@example.com')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/email address/i), { target: { value: 'headers@example.com' } });
    fireEvent.click(screen.getByLabelText(/early signals/i));
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));
    await waitFor(() => expect(screen.getByLabelText(/email address/i)).toHaveValue(''));

    const row = screen.getByText('ops@example.com').closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: /remove/i }));
    await waitFor(() => expect(deleteCalled).toBe(true));

    expect(capturedHeaders.length).toBeGreaterThan(0);
    capturedHeaders.forEach((headers) => {
      expect(headers['X-Admin-Token']).toBe('secret-token');
    });
  });
});
