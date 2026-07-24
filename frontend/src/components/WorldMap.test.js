import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import WorldMap from './WorldMap';

const BASE_COVERAGE = {
  countries: [
    {
      name: 'United States', slug: 'us', iso_numeric: '840', sources: 162, policies: 23,
      top_policy_names: ['Heat Reuse Act', 'Thermal Energy Network Pilot Program'],
    },
    {
      name: 'Sweden', slug: 'sweden', iso_numeric: '752', sources: 8, policies: 0, top_policy_names: [],
    },
    {
      // Absent from the 110m atlas entirely - no <path> can ever exist for
      // it. Must still reach the map (as a micro dot) and the browse list.
      name: 'Singapore', slug: 'singapore', iso_numeric: '702', sources: 2, policies: 0, top_policy_names: [],
    },
  ],
  supranational: [
    {
      name: 'European Union', slug: 'eu', sources: 0, policies: 7,
      top_policy_names: ['EU Energy Efficiency Directive'],
    },
  ],
  totals: { sources: 372, policies: 118 },
};

function mockFetch(coverage = BASE_COVERAGE) {
  return jest.fn(async (url) => {
    if (String(url).includes('/api/coverage')) {
      return { ok: true, json: async () => coverage };
    }
    return { ok: false, text: async () => 'not found' };
  });
}

// /api/coverage/children?parent=<slug> is a distinct endpoint but its path
// still contains "/api/coverage" - checked first here so it never falls
// through to the world coverage payload.
function mockFetchWithChildren(coverage, childrenByParent) {
  return jest.fn(async (url) => {
    const s = String(url);
    if (s.includes('/api/coverage/children')) {
      const parent = new URL(s).searchParams.get('parent');
      const payload = childrenByParent[parent];
      if (!payload) return { ok: false, status: 404, text: async () => 'not found' };
      return { ok: true, json: async () => payload };
    }
    if (s.includes('/api/coverage')) {
      return { ok: true, json: async () => coverage };
    }
    return { ok: false, text: async () => 'not found' };
  });
}

const US_CHILDREN = {
  parent: { slug: 'us', name: 'United States', iso_numeric: '840' },
  national: { sources: 6, policies: 6, top_policy_names: ['Federal Heat Reuse Act'] },
  children: [
    {
      slug: 'us-mn', name: 'Minnesota', kind: 'us_state', code: 'US-MN',
      sources: 3, policies: 5, top_policy_names: ['MN Thermal Pilot'],
    },
  ],
  totals: { sources: 8, policies: 11 },
};

afterEach(() => {
  jest.restoreAllMocks();
});

describe('WorldMap', () => {
  it('shows totals from the live coverage endpoint', async () => {
    global.fetch = mockFetch();
    render(<WorldMap onSelectPlace={jest.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('372')).toBeInTheDocument();
    });
    expect(screen.getByText('118')).toBeInTheDocument();
    // 3 countries (incl. Singapore, off-atlas) + 1 supranational entry
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  it('never drops a tracked country with no atlas polygon: it reaches the map and the list', async () => {
    const onSelectPlace = jest.fn();
    global.fetch = mockFetch();
    render(<WorldMap onSelectPlace={onSelectPlace} />);

    // Renders as a <circle> micro marker, not a <path> - there is no
    // Singapore polygon in the 110m atlas at all.
    const dot = await screen.findByRole('button', { name: /Singapore: tracked/ });
    expect(dot.tagName.toLowerCase()).toBe('circle');

    expect(await screen.findByText('Browse all tracked places as a list (4)')).toBeInTheDocument();

    fireEvent.click(dot);
    expect(await screen.findByRole('heading', { name: 'Singapore' })).toBeInTheDocument();
    // No policies found yet (policies: 0), so the primary "View found
    // policies" CTA does not render - only the demoted scan action does.
    expect(screen.queryByRole('button', { name: /View \d+ found/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Scan Singapore for new policies' }));
    expect(onSelectPlace).toHaveBeenCalledWith('Singapore');
  });

  it('reaches an off-atlas country through the quick-filter box too', async () => {
    global.fetch = mockFetch();
    render(<WorldMap onSelectPlace={jest.fn()} />);

    await screen.findByRole('button', { name: /United States of America/ });
    fireEvent.change(screen.getByLabelText('Find a tracked place on the map'), {
      target: { value: 'Singapor' },
    });
    fireEvent.keyDown(screen.getByLabelText('Find a tracked place on the map'), { key: 'Enter' });

    expect(await screen.findByRole('heading', { name: 'Singapore' })).toBeInTheDocument();
  });

  it('clicking a tracked country opens the panel with its policies and a working search CTA', async () => {
    const onSelectPlace = jest.fn();
    global.fetch = mockFetch();
    render(<WorldMap onSelectPlace={onSelectPlace} />);

    const path = await screen.findByRole('button', { name: /United States of America/ });
    fireEvent.click(path);

    expect(await screen.findByRole('heading', { name: 'United States' })).toBeInTheDocument();
    expect(screen.getByText(/162 tracked sources/)).toBeInTheDocument();
    expect(screen.getByText('Heat Reuse Act')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Scan United States for new policies' }));
    expect(onSelectPlace).toHaveBeenCalledWith('United States');
  });

  it('a tracked country with policies leads with "View found policies", wired to onViewPlacePolicies', async () => {
    const onViewPlacePolicies = jest.fn();
    global.fetch = mockFetch();
    render(<WorldMap onSelectPlace={jest.fn()} onViewPlacePolicies={onViewPlacePolicies} />);

    const path = await screen.findByRole('button', { name: /United States of America/ });
    fireEvent.click(path);

    fireEvent.click(await screen.findByRole('button', { name: 'View 23 found policies' }));
    expect(onViewPlacePolicies).toHaveBeenCalledWith({ slug: 'us', name: 'United States' });
  });

  it('hides the scan action when showScanAction is false, keeping the view-policies CTA', async () => {
    global.fetch = mockFetch();
    render(<WorldMap onSelectPlace={jest.fn()} showScanAction={false} />);

    const path = await screen.findByRole('button', { name: /United States of America/ });
    fireEvent.click(path);

    expect(await screen.findByRole('button', { name: 'View 23 found policies' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Scan United States/ })).not.toBeInTheDocument();
  });

  it('an untracked country still opens the panel, offering to scan anyway', async () => {
    global.fetch = mockFetch();
    render(<WorldMap onSelectPlace={jest.fn()} />);

    const path = await screen.findByLabelText(/Germany: not yet tracked/);
    fireEvent.click(path);

    expect(await screen.findByRole('heading', { name: 'Germany' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Scan Germany for new policies' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /View \d+ found/ })).not.toBeInTheDocument();
  });

  it('never drops an off-map jurisdiction: the EU renders as a clickable chip', async () => {
    const onSelectPlace = jest.fn();
    const onViewPlacePolicies = jest.fn();
    global.fetch = mockFetch();
    render(
      <WorldMap onSelectPlace={onSelectPlace} onViewPlacePolicies={onViewPlacePolicies} />,
    );

    const tray = await screen.findByLabelText('Coverage without a map shape');
    const chip = within(tray).getByRole('button', { name: /European Union/ });
    fireEvent.click(chip);

    expect(await screen.findByRole('heading', { name: 'European Union' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'View 7 found policies' }));
    expect(onViewPlacePolicies).toHaveBeenCalledWith({ slug: 'eu', name: 'European Union' });

    fireEvent.click(screen.getByRole('button', { name: 'Scan European Union for new policies' }));
    expect(onSelectPlace).toHaveBeenCalledWith('European Union');
  });

  it('legend click filters: dims every bin except the one selected', async () => {
    global.fetch = mockFetch();
    render(<WorldMap onSelectPlace={jest.fn()} />);

    await screen.findByRole('button', { name: /United States of America/ });
    const highTierButton = screen.getByRole('button', { name: '16+ policies' });
    fireEvent.click(highTierButton);

    expect(highTierButton).toHaveAttribute('aria-pressed', 'true');
    const sweden = screen.getByLabelText(/Sweden: 8 sources/);
    expect(sweden).toHaveClass('wm-dim');
    const usa = screen.getByRole('button', { name: /United States of America/ });
    expect(usa).not.toHaveClass('wm-dim');
  });

  it('refetches and redraws when the app dispatches policy-data-changed', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(<WorldMap onSelectPlace={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('118')).toBeInTheDocument());

    fetchMock.mockImplementation(async (url) => {
      if (String(url).includes('/api/coverage')) {
        return { ok: true, json: async () => ({ ...BASE_COVERAGE, totals: { sources: 372, policies: 119 } }) };
      }
      return { ok: false, text: async () => 'not found' };
    });

    await act(async () => {
      window.dispatchEvent(new Event('policy-data-changed'));
    });

    await waitFor(() => expect(screen.getByText('119')).toBeInTheDocument());
  });

  it('offers "Explore regions" only for a country with both admin-1 geometry and state data', async () => {
    global.fetch = mockFetch({
      ...BASE_COVERAGE,
      countries: BASE_COVERAGE.countries.map((c) => (
        // United States (iso 840) is in DRILLABLE_COUNTRIES; give it real
        // children_with_data. Sweden (752) is not in the registry at all,
        // even with children_with_data it must never show the affordance.
        c.iso_numeric === '840'
          ? { ...c, children_with_data: 28 }
          : { ...c, children_with_data: 3 }
      )),
    });
    render(<WorldMap onSelectPlace={jest.fn()} />);

    const usPath = await screen.findByRole('button', { name: /United States of America/ });
    fireEvent.click(usPath);
    expect(await screen.findByRole('button', { name: /Explore United States.*regions/ }))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close panel' }));

    const swedenPath = screen.getByLabelText(/Sweden: 8 sources/);
    fireEvent.click(swedenPath);
    expect(await screen.findByRole('heading', { name: 'Sweden' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Explore/ })).not.toBeInTheDocument();
  });

  it('does not offer "Explore regions" for a drillable-registry country with no state data yet', async () => {
    global.fetch = mockFetch(BASE_COVERAGE); // no children_with_data on United States
    render(<WorldMap onSelectPlace={jest.fn()} />);

    const usPath = await screen.findByRole('button', { name: /United States of America/ });
    fireEvent.click(usPath);
    expect(await screen.findByRole('heading', { name: 'United States' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Explore/ })).not.toBeInTheDocument();
  });

  it('enters country view from the drill affordance and returns to the world via the breadcrumb', async () => {
    const onSelectPlace = jest.fn();
    global.fetch = mockFetchWithChildren(
      {
        ...BASE_COVERAGE,
        countries: BASE_COVERAGE.countries.map((c) => (
          c.iso_numeric === '840' ? { ...c, children_with_data: 28 } : c
        )),
      },
      { us: US_CHILDREN },
    );
    render(<WorldMap onSelectPlace={onSelectPlace} />);

    const usPath = await screen.findByRole('button', { name: /United States of America/ });
    fireEvent.click(usPath);
    fireEvent.click(await screen.findByRole('button', { name: /Explore United States.*regions/ }));

    // The world map's own controls are gone; the country view's own unit
    // (Minnesota) and federal chip have replaced them.
    expect(screen.queryByRole('button', { name: /United States of America/ })).not.toBeInTheDocument();
    expect(await screen.findByLabelText(/Minnesota: 3 sources, 5 policies/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Federal \/ nationwide/ })).toBeInTheDocument();
    expect(screen.getByText(/6 federal polic/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /World/ }));
    expect(await screen.findByRole('button', { name: /United States of America/ })).toBeInTheDocument();
  });

  describe('drill discoverability (double-click / keyboard)', () => {
    // Reduced-motion forces usePanZoom's animateTo() to apply the target
    // viewBox synchronously (see hooks/usePanZoom.js) instead of stepping
    // through requestAnimationFrame, so the zoom-toward assertions below
    // don't need to pump animation frames.
    const originalMatchMedia = window.matchMedia;

    beforeEach(() => {
      window.matchMedia = jest.fn((query) => ({
        matches: true,
        media: query,
        addListener: () => {},
        removeListener: () => {},
      }));
    });

    afterEach(() => {
      window.matchMedia = originalMatchMedia;
    });

    const withDrillableUS = {
      ...BASE_COVERAGE,
      countries: BASE_COVERAGE.countries.map((c) => (
        c.iso_numeric === '840' ? { ...c, children_with_data: 28 } : c
      )),
    };

    it('double-clicking a drillable country drills straight into its regions', async () => {
      global.fetch = mockFetchWithChildren(withDrillableUS, { us: US_CHILDREN });
      render(<WorldMap onSelectPlace={jest.fn()} />);

      const usPath = await screen.findByRole('button', { name: /United States of America/ });
      fireEvent.doubleClick(usPath);

      expect(await screen.findByLabelText(/Minnesota: 3 sources, 5 policies/)).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /United States of America/ })).not.toBeInTheDocument();
    });

    it('double-clicking a non-drillable country zooms toward it instead of doing nothing', async () => {
      global.fetch = mockFetch();
      render(<WorldMap onSelectPlace={jest.fn()} />);

      const swedenPath = await screen.findByLabelText(/Sweden: 8 sources/);
      const svg = screen.getByRole('group', { name: 'World map of PolicyPulse coverage' });
      const before = svg.getAttribute('viewBox');

      fireEvent.doubleClick(swedenPath);

      await waitFor(() => expect(svg.getAttribute('viewBox')).not.toBe(before));
      const [, , w] = svg.getAttribute('viewBox').split(' ').map(Number);
      expect(w).toBeLessThan(960);
    });

    it('closes an open panel when a double-click zooms instead of drilling', async () => {
      global.fetch = mockFetch();
      render(<WorldMap onSelectPlace={jest.fn()} />);

      const swedenPath = await screen.findByLabelText(/Sweden: 8 sources/);
      fireEvent.doubleClick(swedenPath);

      await waitFor(() => {
        expect(screen.queryByRole('heading', { name: 'Sweden' })).not.toBeInTheDocument();
      });
    });

    it('does not capture the pointer on a plain press, so click/double-click reach the country', async () => {
      // Regression guard for the drill-dead bug: capturing on pointerdown
      // retargets the follow-up click AND dblclick to the <svg>, so the country
      // <path>'s onClick / onDoubleClick never fire - single-click stops opening
      // the panel and double-click stops drilling. fireEvent.click on the path
      // bypasses this (no real capture), which is why unit tests missed it, so
      // assert the capture is NOT taken on a plain press. (Capture-on-drag is
      // exercised manually - jsdom carries no pointer geometry to drive it.)
      global.fetch = mockFetch();
      const originalCapture = Element.prototype.setPointerCapture;
      const capture = jest.fn();
      Element.prototype.setPointerCapture = capture;
      try {
        render(<WorldMap onSelectPlace={jest.fn()} />);
        await screen.findByRole('button', { name: /United States of America/ });
        const svg = screen.getByRole('group', { name: 'World map of PolicyPulse coverage' });

        fireEvent.pointerDown(svg, {
          pointerId: 1, button: 0, pointerType: 'mouse', clientX: 100, clientY: 100,
        });
        expect(capture).not.toHaveBeenCalled();
      } finally {
        Element.prototype.setPointerCapture = originalCapture;
      }
    });

    it('marks a drillable country with the drill cursor class and a tooltip hint on hover', async () => {
      global.fetch = mockFetch(withDrillableUS);
      render(<WorldMap onSelectPlace={jest.fn()} />);

      const usPath = await screen.findByRole('button', { name: /United States of America/ });
      expect(usPath).toHaveClass('wm-drillable');

      const swedenPath = screen.getByLabelText(/Sweden: 8 sources/);
      expect(swedenPath).not.toHaveClass('wm-drillable');

      // jsdom in this environment has no native PointerEvent constructor, so
      // fireEvent.pointerMove's options (clientX/clientY) never reach the
      // dispatched event - build one by hand so handleHover sees real
      // coordinates instead of NaN.
      const moveEvent = new window.Event('pointermove', { bubbles: true });
      Object.assign(moveEvent, { clientX: 10, clientY: 10 });
      fireEvent(usPath, moveEvent);

      expect(await screen.findByText('Double-click to see state and province detail.'))
        .toBeInTheDocument();
    });

    it('Shift+Enter on a focused drillable country drills it; plain Enter still opens the panel', async () => {
      global.fetch = mockFetchWithChildren(withDrillableUS, { us: US_CHILDREN });
      render(<WorldMap onSelectPlace={jest.fn()} />);

      const usPath = await screen.findByRole('button', { name: /United States of America/ });
      fireEvent.keyDown(usPath, { key: 'Enter' });
      expect(await screen.findByRole('heading', { name: 'United States' })).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Close panel' }));
      fireEvent.keyDown(usPath, { key: 'Enter', shiftKey: true });

      expect(await screen.findByRole('button', { name: /Federal \/ nationwide/ })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /United States of America/ })).not.toBeInTheDocument();
    });
  });
});

// --- Public review visibility toggle (WP-3) ---
//
// The toggle itself is dumb (props-driven: publicView/onPublicViewChange/
// showPublicViewToggle) — App.js owns the actual posture-derived state so
// the same value can also drive PolicyList's fetches. These tests cover the
// toggle's own rendering/wiring and the ?review= param it puts on the
// coverage fetch; the initial-position-from-posture logic lives in App.js.

function fetchCoverageUrls(fetchMock) {
  return fetchMock.mock.calls
    .map(([url]) => String(url))
    .filter((url) => url.includes('/api/coverage') && !url.includes('/children'));
}

describe('WorldMap public view toggle', () => {
  it('renders both options and marks the current one active when shown', async () => {
    global.fetch = mockFetch();
    render(
      <WorldMap
        onSelectPlace={jest.fn()}
        publicView="all"
        onPublicViewChange={jest.fn()}
        showPublicViewToggle
      />,
    );

    await screen.findByText('372');
    const allButton = screen.getByRole('button', { name: 'All finds' });
    const reviewedButton = screen.getByRole('button', { name: 'Reviewed only' });
    expect(allButton).toHaveAttribute('aria-pressed', 'true');
    expect(reviewedButton).toHaveAttribute('aria-pressed', 'false');
  });

  it('is hidden entirely when showPublicViewToggle is false (reviewed_only posture)', async () => {
    global.fetch = mockFetch();
    render(
      <WorldMap
        onSelectPlace={jest.fn()}
        publicView="reviewed"
        onPublicViewChange={jest.fn()}
        showPublicViewToggle={false}
      />,
    );

    await screen.findByText('372');
    expect(screen.queryByRole('button', { name: 'All finds' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reviewed only' })).not.toBeInTheDocument();
  });

  it('clicking an option calls onPublicViewChange with that option', async () => {
    global.fetch = mockFetch();
    const onPublicViewChange = jest.fn();
    render(
      <WorldMap
        onSelectPlace={jest.fn()}
        publicView="all"
        onPublicViewChange={onPublicViewChange}
        showPublicViewToggle
      />,
    );

    await screen.findByText('372');
    fireEvent.click(screen.getByRole('button', { name: 'Reviewed only' }));
    expect(onPublicViewChange).toHaveBeenCalledWith('reviewed');
  });

  it('sends the current publicView as ?review= on the coverage fetch', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    render(
      <WorldMap
        onSelectPlace={jest.fn()}
        publicView="reviewed"
        onPublicViewChange={jest.fn()}
        showPublicViewToggle
      />,
    );

    await screen.findByText('372');
    const urls = fetchCoverageUrls(fetchMock);
    expect(urls.length).toBeGreaterThan(0);
    expect(urls.every((url) => new URL(url).searchParams.get('review') === 'reviewed')).toBe(true);
  });

  it('refetches coverage with the new review param when publicView changes', async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock;
    const { rerender } = render(
      <WorldMap
        onSelectPlace={jest.fn()}
        publicView="all"
        onPublicViewChange={jest.fn()}
        showPublicViewToggle
      />,
    );
    await screen.findByText('372');

    rerender(
      <WorldMap
        onSelectPlace={jest.fn()}
        publicView="reviewed"
        onPublicViewChange={jest.fn()}
        showPublicViewToggle
      />,
    );

    await waitFor(() => {
      const urls = fetchCoverageUrls(fetchMock);
      expect(urls.some((url) => new URL(url).searchParams.get('review') === 'reviewed')).toBe(true);
    });
  });
});
