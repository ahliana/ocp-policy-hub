import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import CountryView from './CountryView';

const GEOMETRY = {
  country: { iso_numeric: '056', iso3: 'BEL', name: 'Belgium' },
  viewBox: [0, 0, 960, 784.2],
  units: [
    { code: 'BE-BRU', name: 'Brussels', d: 'M0,0Z', cx: 1, cy: 1, area: 100 },
    { code: 'BE-VLG', name: 'Flanders', d: 'M1,1Z', cx: 2, cy: 2, area: 200 },
    { code: 'BE-WAL', name: 'Wallonia', d: 'M2,2Z', cx: 3, cy: 3, area: 300 },
  ],
};

const CHILDREN_RESPONSE = {
  parent: { slug: 'belgium', name: 'Belgium', iso_numeric: '056' },
  national: { sources: 0, policies: 0, top_policy_names: [] },
  children: [
    {
      slug: 'belgium-bru', name: 'Brussels', kind: 'subnational', code: 'BE-BRU',
      sources: 1, policies: 3, top_policy_names: ['Brussels Heat Directive'],
    },
    {
      slug: 'belgium-wal', name: 'Wallonia', kind: 'subnational', code: 'BE-WAL',
      sources: 2, policies: 4, top_policy_names: [],
    },
    // Flanders (BE-VLG) omitted, matching the API's real behavior: a child
    // with zero data does not appear in children[] at all.
  ],
  totals: { sources: 3, policies: 7 },
};

function mockLoad(geometry = GEOMETRY) {
  return jest.fn(() => Promise.resolve({ default: geometry }));
}

function mockFetchChildren(payload = CHILDREN_RESPONSE, ok = true) {
  return jest.fn(async () => (
    ok ? { ok: true, json: async () => payload } : { ok: false, status: 404, text: async () => 'not found' }
  ));
}

afterEach(() => {
  jest.restoreAllMocks();
});

describe('CountryView', () => {
  it('shows a loading state, then draws each admin-1 unit and the federal chip', async () => {
    global.fetch = mockFetchChildren();
    render(
      <CountryView
        slug="belgium"
        countryName="Belgium"
        load={mockLoad()}
        onBack={jest.fn()}
        onSelectPlace={jest.fn()}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent(/Loading Belgium/);

    expect(await screen.findByLabelText('Brussels: 1 sources, 3 policies')).toBeInTheDocument();
    expect(screen.getByLabelText('Wallonia: 2 sources, 4 policies')).toBeInTheDocument();
    // Untracked: present in geometry but absent from children[] - drawn, not dropped.
    expect(screen.getByLabelText('Flanders: not yet tracked')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Federal \/ nationwide/ })).toBeInTheDocument();
  });

  it('clicking a unit opens the region panel with its data and a working search CTA', async () => {
    const onSelectPlace = jest.fn();
    global.fetch = mockFetchChildren();
    render(
      <CountryView
        slug="belgium"
        countryName="Belgium"
        load={mockLoad()}
        onBack={jest.fn()}
        onSelectPlace={onSelectPlace}
      />,
    );

    const brussels = await screen.findByRole('button', { name: /Brussels: 1 sources/ });
    fireEvent.click(brussels);

    expect(await screen.findByRole('heading', { name: 'Brussels' })).toBeInTheDocument();
    expect(screen.getByText(/1 tracked source/)).toBeInTheDocument();
    expect(screen.getByText('Brussels Heat Directive')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Scan Brussels for new policies' }));
    expect(onSelectPlace).toHaveBeenCalledWith('Brussels');
  });

  it('leads with "View found policies" for a unit with policies, wired to onViewPlacePolicies', async () => {
    const onViewPlacePolicies = jest.fn();
    global.fetch = mockFetchChildren();
    render(
      <CountryView
        slug="belgium"
        countryName="Belgium"
        load={mockLoad()}
        onBack={jest.fn()}
        onSelectPlace={jest.fn()}
        onViewPlacePolicies={onViewPlacePolicies}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Brussels: 1 sources/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'View 3 found policies' }));
    expect(onViewPlacePolicies).toHaveBeenCalledWith({ slug: 'belgium-bru', name: 'Brussels' });
  });

  it('the federal chip\'s panel views policies under the country\'s own slug', async () => {
    const onViewPlacePolicies = jest.fn();
    global.fetch = mockFetchChildren({
      ...CHILDREN_RESPONSE,
      national: { sources: 6, policies: 2, top_policy_names: ['Federal Heat Reuse Act'] },
    });
    render(
      <CountryView
        slug="belgium"
        countryName="Belgium"
        load={mockLoad()}
        onBack={jest.fn()}
        onSelectPlace={jest.fn()}
        onViewPlacePolicies={onViewPlacePolicies}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Federal \/ nationwide/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'View 2 found policies' }));
    expect(onViewPlacePolicies).toHaveBeenCalledWith({ slug: 'belgium', name: 'Belgium' });
  });

  it('hides the scan action when showScanAction is false', async () => {
    global.fetch = mockFetchChildren();
    render(
      <CountryView
        slug="belgium"
        countryName="Belgium"
        load={mockLoad()}
        onBack={jest.fn()}
        onSelectPlace={jest.fn()}
        showScanAction={false}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Brussels: 1 sources/ }));
    expect(await screen.findByRole('button', { name: 'View 3 found policies' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Scan Brussels/ })).not.toBeInTheDocument();
  });

  it('the federal chip is not a map unit path, and opens a distinctly federal-badged panel', async () => {
    global.fetch = mockFetchChildren({
      ...CHILDREN_RESPONSE,
      national: { sources: 6, policies: 2, top_policy_names: ['Federal Heat Reuse Act'] },
    });
    render(
      <CountryView
        slug="belgium"
        countryName="Belgium"
        load={mockLoad()}
        onBack={jest.fn()}
        onSelectPlace={jest.fn()}
      />,
    );

    const federalChip = await screen.findByRole('button', { name: /Federal \/ nationwide/ });
    expect(federalChip.tagName.toLowerCase()).toBe('button');
    expect(federalChip).not.toHaveClass('wm-country');
    expect(federalChip.getAttribute('data-bin')).toBeNull();

    fireEvent.click(federalChip);

    expect(await screen.findByRole('heading', { name: 'Belgium' })).toBeInTheDocument();
    const panel = document.querySelector('.wm-panel-federal');
    expect(panel).toBeInTheDocument();
    expect(panel.querySelector('.wm-federal-badge')).toHaveTextContent('Federal / nationwide');
  });

  it('renders city text chips when a bucket optionally carries them, and nothing when it does not', async () => {
    global.fetch = mockFetchChildren({
      ...CHILDREN_RESPONSE,
      national: { sources: 6, policies: 2, top_policy_names: [], cities: ['Brussels City', 'Leuven'] },
    });
    render(
      <CountryView
        slug="belgium"
        countryName="Belgium"
        load={mockLoad()}
        onBack={jest.fn()}
        onSelectPlace={jest.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Federal \/ nationwide/ }));
    const chips = await screen.findByLabelText('Cities with tracked detail');
    expect(chips).toHaveTextContent('Brussels City');
    expect(chips).toHaveTextContent('Leuven');

    // A unit with no `cities` field (the current, real contract) renders no
    // chip row at all - the scaffold stays dormant until the API adds it.
    fireEvent.click(screen.getByRole('button', { name: 'Close panel' }));
    fireEvent.click(screen.getByRole('button', { name: /Wallonia: 2 sources/ }));
    await screen.findByRole('heading', { name: 'Wallonia' });
    expect(screen.queryByLabelText('Cities with tracked detail')).not.toBeInTheDocument();
  });

  it('reconciles the summary against national + children totals', async () => {
    global.fetch = mockFetchChildren();
    render(
      <CountryView
        slug="belgium"
        countryName="Belgium"
        load={mockLoad()}
        onBack={jest.fn()}
        onSelectPlace={jest.fn()}
      />,
    );

    // national.policies=0, totals.policies=7, children.length=2 (Flanders omitted).
    await waitFor(() => {
      expect(screen.getByText(/0 federal policies/)).toBeInTheDocument();
    });
    expect(screen.getByText(/7 across 2 regions/)).toBeInTheDocument();
    expect(screen.getByText(/= 7 total/)).toBeInTheDocument();
  });

  it('breadcrumb "World" click and Escape both return to the world view (Escape closes an open panel first)', async () => {
    const onBack = jest.fn();
    global.fetch = mockFetchChildren();
    render(
      <CountryView
        slug="belgium"
        countryName="Belgium"
        load={mockLoad()}
        onBack={onBack}
        onSelectPlace={jest.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Brussels: 1 sources/ }));
    expect(await screen.findByRole('heading', { name: 'Brussels' })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onBack).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Brussels' })).not.toBeInTheDocument());

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onBack).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: /World/ }));
    expect(onBack).toHaveBeenCalledTimes(2);
  });

  it('the zoom controls always show a "World" button that calls onBack', async () => {
    const onBack = jest.fn();
    global.fetch = mockFetchChildren();
    render(
      <CountryView
        slug="belgium"
        countryName="Belgium"
        load={mockLoad()}
        onBack={onBack}
        onSelectPlace={jest.fn()}
      />,
    );

    const controls = await screen.findByRole('group', { name: 'Map zoom controls' });
    const worldButton = within(controls).getByRole('button', { name: 'Back to world map' });
    expect(worldButton).toHaveTextContent('World');

    fireEvent.click(worldButton);
    expect(onBack).toHaveBeenCalledTimes(1);

    // The breadcrumb "World" link is a distinct control and still works.
    fireEvent.click(screen.getByRole('button', { name: '← World' }));
    expect(onBack).toHaveBeenCalledTimes(2);
  });

  it('surfaces a geometry or coverage load failure without crashing', async () => {
    global.fetch = mockFetchChildren(null, false);
    render(
      <CountryView
        slug="nowhere"
        countryName="Nowhere"
        load={mockLoad()}
        onBack={jest.fn()}
        onSelectPlace={jest.fn()}
      />,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not load Nowhere/);
  });
});
