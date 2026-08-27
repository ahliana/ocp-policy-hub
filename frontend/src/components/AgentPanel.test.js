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

describe('AgentPanel Library gating', () => {
  it('does not render the Library before admin is opened', () => {
    renderPanel();
    expect(screen.queryByText(/Library - everything in the database/i)).not.toBeInTheDocument();
  });

  it('does not render the Library when admin is open but locked', async () => {
    renderPanel({ adminRequired: true, hasAdminToken: false });
    fireEvent.click(screen.getByRole('button', { name: 'Admin' }));

    await screen.findByText(/read-only view/i);
    expect(screen.queryByText(/Library - everything in the database/i)).not.toBeInTheDocument();
  });

  it('renders the Library once admin is opened and unlocked', async () => {
    renderPanel({ adminRequired: false, hasAdminToken: false });
    fireEvent.click(screen.getByRole('button', { name: 'Admin' }));

    expect(await screen.findByText(/Library - everything in the database/i)).toBeInTheDocument();
  });
});

describe('AgentPanel Advanced scan-scope disclosure (WP-6)', () => {
  it('the "Advanced: pick individual regions and sources" details is open by default once the admin area is open', async () => {
    renderPanel({ adminRequired: false, hasAdminToken: false });
    fireEvent.click(screen.getByRole('button', { name: 'Admin' }));

    const summary = await screen.findByText('Advanced: pick individual regions and sources');
    const details = summary.closest('details');
    expect(details).toHaveAttribute('open');
  });
});

describe('AgentPanel "How PolicyPulse works" placement (WP-31)', () => {
  it('renders as the last panel inside the admin area', async () => {
    renderPanel({ adminRequired: false, hasAdminToken: false });
    fireEvent.click(screen.getByRole('button', { name: 'Admin' }));

    await screen.findByText(/Administrator mode/i);
    const howItWorksSummary = await screen.findByText(
      'How PolicyPulse works - from government website to the public map',
    );
    const panel = howItWorksSummary.closest('.how-it-works-panel');
    const adminArea = document.querySelector('.admin-area');
    expect(adminArea.lastElementChild).toBe(panel);
  });
});

describe('AgentPanel "Email notifications" placement (WP-44)', () => {
  it('renders between the Schedules panel and the How it works panel', async () => {
    renderPanel({ adminRequired: false, hasAdminToken: false });
    fireEvent.click(screen.getByRole('button', { name: 'Admin' }));

    await screen.findByText(/Administrator mode/i);
    const notificationsHeading = await screen.findByText('Email notifications');
    const panel = notificationsHeading.closest('.notifications-panel');
    const adminArea = document.querySelector('.admin-area');
    const children = Array.from(adminArea.children);
    const schedulesIndex = children.findIndex((el) => el.classList.contains('schedules-panel'));
    const notificationsIndex = children.indexOf(panel);
    const howItWorksIndex = children.findIndex((el) => el.classList.contains('how-it-works-panel'));

    expect(schedulesIndex).toBeGreaterThanOrEqual(0);
    expect(notificationsIndex).toBe(schedulesIndex + 1);
    expect(howItWorksIndex).toBe(notificationsIndex + 1);
  });
});
