import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import SchedulesPanel, { formatCadence } from './SchedulesPanel';

const SCHEDULE_ACTIVE = {
  id: 'sched-1', name: 'Monthly full scan', domains: 'all',
  channels: ['crawl', 'law_apis', 'transposition'], deep: false, topic: null,
  cadence: 'monthly:1:06:00', enabled: true, monthly_ceiling_usd: null,
  paused_reason: null, last_run_at: '2026-06-01T06:00:00', last_scan_id: 'scan-abc12345',
  next_run_at: '2026-07-01T06:00:00', created_at: '2026-01-01T00:00:00',
  estimate_usd: 12.5, history: null, per_month_usd: 12.5,
};

const SCHEDULE_PAUSED = {
  id: 'sched-2', name: 'Weekly quick check', domains: 'quick',
  channels: ['crawl'], deep: false, topic: null,
  cadence: 'weekly:0:06:30', enabled: true, monthly_ceiling_usd: 10.0,
  paused_reason: 'monthly ceiling reached ($15.00 of $10.00)',
  last_run_at: '2026-07-20T06:30:00', last_scan_id: 'scan-def67890',
  next_run_at: '2026-07-27T06:30:00', created_at: '2026-01-01T00:00:00',
  estimate_usd: 2.0, history: { runs: 3, mean_cost_usd: 2.5, last_cost_usd: 2.1 },
  per_month_usd: 10.83,
};

function jsonResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function mockFetch({ schedules = [SCHEDULE_ACTIVE, SCHEDULE_PAUSED], onPost, onPut, onDelete, onRunNow } = {}) {
  return jest.fn(async (url, options) => {
    const parsed = new URL(String(url));

    if (parsed.pathname === '/api/schedules' && (!options || !options.method || options.method === 'GET')) {
      return jsonResponse(200, { schedules });
    }
    if (parsed.pathname === '/api/schedules' && options?.method === 'POST') {
      if (onPost) return onPost(JSON.parse(options.body));
      return jsonResponse(200, { ...SCHEDULE_ACTIVE, ...JSON.parse(options.body) });
    }
    const putMatch = parsed.pathname.match(/^\/api\/schedules\/([^/]+)$/);
    if (putMatch && options?.method === 'PUT') {
      if (onPut) return onPut(putMatch[1], JSON.parse(options.body));
      return jsonResponse(200, { ...SCHEDULE_ACTIVE, id: putMatch[1], ...JSON.parse(options.body) });
    }
    if (putMatch && options?.method === 'DELETE') {
      if (onDelete) return onDelete(putMatch[1]);
      return jsonResponse(200, { status: 'deleted', id: putMatch[1] });
    }
    const runNowMatch = parsed.pathname.match(/^\/api\/schedules\/([^/]+)\/run-now$/);
    if (runNowMatch && options?.method === 'POST') {
      if (onRunNow) return onRunNow(runNowMatch[1]);
      return jsonResponse(200, { ...SCHEDULE_ACTIVE, id: runNowMatch[1], last_scan_id: 'scan-new111' });
    }
    return jsonResponse(404, {});
  });
}

afterEach(() => {
  jest.restoreAllMocks();
});

describe('formatCadence', () => {
  it('renders a weekly cadence in words', () => {
    expect(formatCadence('weekly:0:06:30')).toBe('Mondays 06:30 UTC');
  });

  it('renders a monthly cadence in words', () => {
    expect(formatCadence('monthly:1:06:00')).toBe('1st of the month 06:00 UTC');
  });

  it('handles ordinal suffixes', () => {
    expect(formatCadence('monthly:2:06:00')).toBe('2nd of the month 06:00 UTC');
    expect(formatCadence('monthly:3:06:00')).toBe('3rd of the month 06:00 UTC');
    expect(formatCadence('monthly:11:06:00')).toBe('11th of the month 06:00 UTC');
    expect(formatCadence('monthly:22:06:00')).toBe('22nd of the month 06:00 UTC');
  });
});

describe('SchedulesPanel list rendering', () => {
  it('fetches and renders schedule rows', async () => {
    global.fetch = mockFetch();
    render(<SchedulesPanel />);

    await waitFor(() => expect(screen.getByText('Monthly full scan')).toBeInTheDocument());
    expect(screen.getByText('Weekly quick check')).toBeInTheDocument();

    const row = screen.getByText('Monthly full scan').closest('tr');
    expect(within(row).getByText('all')).toBeInTheDocument();
    expect(within(row).getByText(/1st of the month 06:00 UTC/)).toBeInTheDocument();
    expect(within(row).getByText(/scan-abc12345/)).toBeInTheDocument();
    expect(within(row).getAllByText('$12.50').length).toBeGreaterThanOrEqual(1);
  });

  it('shows a paused badge with reason for a ceiling-paused schedule', async () => {
    global.fetch = mockFetch();
    render(<SchedulesPanel />);

    await waitFor(() => expect(screen.getByText('Weekly quick check')).toBeInTheDocument());
    const row = screen.getByText('Weekly quick check').closest('tr');
    expect(within(row).getByText(/paused/i)).toBeInTheDocument();
    expect(within(row).getByText(/monthly ceiling reached/i)).toBeInTheDocument();
  });

  it('does not show a paused badge for a schedule with no paused_reason', async () => {
    global.fetch = mockFetch();
    render(<SchedulesPanel />);

    await waitFor(() => expect(screen.getByText('Monthly full scan')).toBeInTheDocument());
    const row = screen.getByText('Monthly full scan').closest('tr');
    expect(within(row).queryByText(/paused/i)).not.toBeInTheDocument();
  });
});

describe('SchedulesPanel create form', () => {
  it('submits a new schedule with the expected POST body', async () => {
    let capturedBody = null;
    global.fetch = mockFetch({
      schedules: [],
      onPost: (body) => {
        capturedBody = body;
        return jsonResponse(200, { ...SCHEDULE_ACTIVE, ...body, id: 'sched-new' });
      },
    });
    render(<SchedulesPanel />);
    await waitFor(() => expect(screen.getByLabelText(/schedule name/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/schedule name/i), { target: { value: 'New schedule' } });
    fireEvent.change(screen.getByLabelText(/scope/i), { target: { value: 'eu' } });
    fireEvent.click(screen.getByLabelText(/^crawl$/i));
    fireEvent.click(screen.getByLabelText(/deep scan/i));
    fireEvent.change(screen.getByLabelText(/cadence type/i), { target: { value: 'weekly' } });
    fireEvent.change(screen.getByLabelText(/day/i), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText(/hour/i), { target: { value: '06' } });
    fireEvent.change(screen.getByLabelText(/minute/i), { target: { value: '30' } });
    fireEvent.change(screen.getByLabelText(/monthly ceiling/i), { target: { value: '25' } });

    fireEvent.click(screen.getByRole('button', { name: /create schedule/i }));

    await waitFor(() => expect(capturedBody).not.toBeNull());
    expect(capturedBody.name).toBe('New schedule');
    expect(capturedBody.domains).toBe('eu');
    expect(capturedBody.channels).toEqual(['crawl']);
    expect(capturedBody.deep).toBe(true);
    expect(capturedBody.cadence).toBe('weekly:2:06:30');
    expect(capturedBody.monthly_ceiling_usd).toBe(25);
  });

  it('blocks submit with no channel selected and never calls the API', async () => {
    let posted = false;
    global.fetch = mockFetch({
      schedules: [],
      onPost: () => { posted = true; return jsonResponse(200, SCHEDULE_ACTIVE); },
    });
    render(<SchedulesPanel />);
    await waitFor(() => expect(screen.getByLabelText(/schedule name/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/schedule name/i), { target: { value: 'No channels' } });
    fireEvent.change(screen.getByLabelText(/scope/i), { target: { value: 'eu' } });
    // Leave every channel unchecked (emptyForm defaults them all false).
    fireEvent.click(screen.getByRole('button', { name: /create schedule/i }));

    expect(await screen.findByText(/at least one source channel/i)).toBeInTheDocument();
    expect(posted).toBe(false);
  });

  it('shows the server validation error on a failed create', async () => {
    global.fetch = mockFetch({
      schedules: [],
      onPost: () => jsonResponse(400, { detail: "Unknown group/region/domain: 'bogus'." }),
    });
    render(<SchedulesPanel />);
    await waitFor(() => expect(screen.getByLabelText(/schedule name/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/schedule name/i), { target: { value: 'X' } });
    fireEvent.change(screen.getByLabelText(/scope/i), { target: { value: 'bogus' } });
    fireEvent.click(screen.getByLabelText(/^crawl$/i)); // pass the client-side channel guard
    fireEvent.click(screen.getByRole('button', { name: /create schedule/i }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/bogus/));
  });
});

describe('SchedulesPanel enabled toggle', () => {
  it('sends a PUT with just the enabled flag', async () => {
    let capturedBody = null;
    global.fetch = mockFetch({
      onPut: (id, body) => {
        capturedBody = body;
        return jsonResponse(200, { ...SCHEDULE_ACTIVE, enabled: false });
      },
    });
    render(<SchedulesPanel />);
    await waitFor(() => expect(screen.getByText('Monthly full scan')).toBeInTheDocument());

    const row = screen.getByText('Monthly full scan').closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: /toggle/i }));

    await waitFor(() => expect(capturedBody).toEqual({ enabled: false }));
  });
});

describe('SchedulesPanel run now', () => {
  it('sends a POST to run-now for the row', async () => {
    let runNowId = null;
    global.fetch = mockFetch({
      onRunNow: (id) => {
        runNowId = id;
        return jsonResponse(200, { ...SCHEDULE_ACTIVE, last_scan_id: 'scan-new111' });
      },
    });
    render(<SchedulesPanel />);
    await waitFor(() => expect(screen.getByText('Monthly full scan')).toBeInTheDocument());

    const row = screen.getByText('Monthly full scan').closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: /run now/i }));

    await waitFor(() => expect(runNowId).toBe('sched-1'));
  });
});

describe('SchedulesPanel edit form', () => {
  it('edit populates the form and PUTs the full body', async () => {
    let putBody = null;
    let putId = null;
    global.fetch = mockFetch({
      onPut: (id, body) => {
        putId = id;
        putBody = body;
        return jsonResponse(200, { ...SCHEDULE_ACTIVE, ...body });
      },
    });
    render(<SchedulesPanel />);
    await waitFor(() => expect(screen.getByText('Monthly full scan')).toBeInTheDocument());

    const row = screen.getByText('Monthly full scan').closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: /^edit$/i }));

    expect(screen.getByLabelText(/schedule name/i)).toHaveValue('Monthly full scan');
    expect(screen.getByLabelText(/scope/i)).toHaveValue('all');
    expect(screen.getByLabelText(/cadence type/i)).toHaveValue('monthly');

    fireEvent.change(screen.getByLabelText(/schedule name/i), { target: { value: 'Renamed scan' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(putId).toBe('sched-1'));
    expect(putBody.name).toBe('Renamed scan');
    expect(putBody.domains).toBe('all');
    expect(putBody.cadence).toBe('monthly:1:06:00');
  });

  it('cancel edit reverts to the create form', async () => {
    global.fetch = mockFetch();
    render(<SchedulesPanel />);
    await waitFor(() => expect(screen.getByText('Monthly full scan')).toBeInTheDocument());

    const row = screen.getByText('Monthly full scan').closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: /^edit$/i }));
    fireEvent.click(screen.getByRole('button', { name: /cancel edit/i }));

    expect(screen.getByLabelText(/schedule name/i)).toHaveValue('');
    expect(screen.getByRole('button', { name: /create schedule/i })).toBeInTheDocument();
  });
});

describe('SchedulesPanel delete', () => {
  it('requires confirmation before deleting', async () => {
    let deletedId = null;
    global.fetch = mockFetch({
      onDelete: (id) => {
        deletedId = id;
        return jsonResponse(200, { status: 'deleted', id });
      },
    });
    render(<SchedulesPanel />);
    await waitFor(() => expect(screen.getByText('Monthly full scan')).toBeInTheDocument());

    const row = screen.getByText('Monthly full scan').closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: /^delete$/i }));

    // Not deleted yet - awaiting confirmation.
    expect(deletedId).toBeNull();

    fireEvent.click(within(row).getByRole('button', { name: /confirm delete/i }));

    await waitFor(() => expect(deletedId).toBe('sched-1'));
  });

  it('cancel leaves the schedule intact', async () => {
    global.fetch = mockFetch();
    render(<SchedulesPanel />);
    await waitFor(() => expect(screen.getByText('Monthly full scan')).toBeInTheDocument());

    const row = screen.getByText('Monthly full scan').closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: /^delete$/i }));
    fireEvent.click(within(row).getByRole('button', { name: /cancel/i }));

    expect(within(row).queryByRole('button', { name: /confirm delete/i })).not.toBeInTheDocument();
    expect(screen.getByText('Monthly full scan')).toBeInTheDocument();
  });
});
