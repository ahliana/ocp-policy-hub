import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AgentPanel from './AgentPanel';

class FakeWebSocket {
  constructor() {
    FakeWebSocket.instances.push(this);
  }

  close() {}
}
FakeWebSocket.instances = [];

function mockFetch() {
  return jest.fn(async (url) => {
    const path = String(url);
    if (path.includes('/api/coverage')) {
      return { ok: true, json: async () => ({ countries: [], supranational: [], totals: { sources: 0, policies: 0 } }) };
    }
    if (path.includes('/api/settings/api-key')) {
      return { ok: true, json: async () => ({ exists: false }) };
    }
    // Every other admin-subsurface fetch (public-visibility, review inbox,
    // sheet settings, region/group listings, etc.) fails gracefully in
    // those components' own catch blocks - no need to model each shape here.
    return { ok: false, json: async () => ({}), text: async () => '' };
  });
}

beforeEach(() => {
  global.fetch = mockFetch();
  global.WebSocket = FakeWebSocket;
  FakeWebSocket.instances = [];
});

afterEach(() => {
  jest.restoreAllMocks();
});

function renderPanel(props = {}) {
  return render(
    <AgentPanel
      adminRequired={false}
      hasAdminToken={false}
      onAdminTokenChange={jest.fn()}
      onViewPlacePolicies={jest.fn()}
      publicView="all"
      onPublicViewChange={jest.fn()}
      showPublicViewToggle
      {...props}
    />,
  );
}

describe('AgentPanel admin mode banner', () => {
  it('shows no banner before admin is opened', () => {
    renderPanel();
    expect(screen.queryByText(/Administrator mode/i)).not.toBeInTheDocument();
  });

  it('shows the banner once admin is opened and unlocked', async () => {
    renderPanel({ adminRequired: false, hasAdminToken: false });
    fireEvent.click(screen.getByRole('button', { name: 'Admin' }));

    const banner = await screen.findByText(
      'Administrator mode - actions here can spend money and change what the public sees.',
    );
    expect(banner).toHaveAttribute('role', 'status');
  });

  it('does not show the banner when admin is open but locked', async () => {
    renderPanel({ adminRequired: true, hasAdminToken: false });
    fireEvent.click(screen.getByRole('button', { name: 'Admin' }));

    await screen.findByText(/read-only view/i);
    expect(screen.queryByText(/Administrator mode/i)).not.toBeInTheDocument();
  });

  it('button reads "Exit admin" once open, and back to "Admin" once closed', async () => {
    renderPanel();
    const toggle = screen.getByRole('button', { name: 'Admin' });

    fireEvent.click(toggle);
    expect(await screen.findByRole('button', { name: 'Exit admin' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Exit admin' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Admin' })).toBeInTheDocument());
  });

  it('the banner persists while the admin area is open (e.g. with a subsurface like the review inbox mounted)', async () => {
    renderPanel({ adminRequired: false, hasAdminToken: false });
    fireEvent.click(screen.getByRole('button', { name: 'Admin' }));

    await screen.findByText(/Administrator mode/i);
    // ReviewInbox and PublicVisibilityControl render inside the admin area -
    // the banner must still be present alongside them, not just at the
    // instant admin opens.
    expect(screen.getByText(/Administrator mode/i)).toBeInTheDocument();
  });
});
