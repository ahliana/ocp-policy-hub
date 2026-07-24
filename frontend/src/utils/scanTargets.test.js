import { DEFAULT_CHANNELS, buildChannels, describeSelectionLabels, formatLabel } from './scanTargets';

describe('formatLabel', () => {
  it('applies known overrides', () => {
    expect(formatLabel('eu')).toBe('EU');
    expect(formatLabel('us')).toBe('United States');
  });

  it('title-cases and de-underscores unknown values', () => {
    expect(formatLabel('nordic_baltics')).toBe('Nordic Baltics');
  });

  it('returns empty string for falsy input', () => {
    expect(formatLabel('')).toBe('');
    expect(formatLabel(undefined)).toBe('');
  });
});

describe('describeSelectionLabels', () => {
  it('formats a bare group id', () => {
    expect(describeSelectionLabels(['group:eu'])).toEqual(['EU']);
  });

  it('formats a group+region selection using the region, not the group', () => {
    expect(describeSelectionLabels(['group:quick:region:eu'])).toEqual(['EU']);
  });

  it('formats a plain region id', () => {
    expect(describeSelectionLabels(['region:us'])).toEqual(['United States']);
  });

  it('formats a category selection', () => {
    expect(describeSelectionLabels(['category:energy_ministry']))
      .toEqual(['Category: Energy Ministry']);
  });

  it('formats a tag selection', () => {
    expect(describeSelectionLabels(['tag:incentive'])).toEqual(['Tag: Incentive']);
  });

  it('formats a bare domain id', () => {
    expect(describeSelectionLabels(['legiscan_api'])).toEqual(['Legiscan Api']);
  });

  it('preserves order across a mixed selection', () => {
    expect(describeSelectionLabels(['group:eu', 'tag:incentive', 'category:energy_ministry']))
      .toEqual(['EU', 'Tag: Incentive', 'Category: Energy Ministry']);
  });

  it('returns an empty array for no selection', () => {
    expect(describeSelectionLabels([])).toEqual([]);
    expect(describeSelectionLabels(undefined)).toEqual([]);
  });
});

describe('DEFAULT_CHANNELS', () => {
  it('defaults to crawl, law_apis, and transposition', () => {
    expect(DEFAULT_CHANNELS).toEqual(['crawl', 'law_apis', 'transposition']);
  });
});

describe('buildChannels', () => {
  it('returns the selected channels when provided', () => {
    expect(buildChannels(['crawl', 'news'])).toEqual(['crawl', 'news']);
  });

  it('falls back to crawl-only when no channels are selected', () => {
    expect(buildChannels([])).toEqual(['crawl']);
  });

  it('falls back to crawl-only when channels is undefined', () => {
    expect(buildChannels(undefined)).toEqual(['crawl']);
  });
});

describe('buildScanRequests', () => {
  const domainsByGroup = {
    us_states: [
      { id: 'ca_leg', region: ['california', 'us_states'] },
      { id: 'ct_deep', region: ['connecticut', 'us_states'] },
      { id: 'legiscan_api', region: ['us', 'us_states'] },
    ],
  };

  beforeEach(() => {
    global.fetch = jest.fn(async (url) => {
      const group = decodeURIComponent(url.split('group=')[1]);
      return {
        ok: true,
        json: async () => ({ domains: domainsByGroup[group] || [] }),
      };
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('consolidates all resolved domains into ONE scan request', async () => {
    const { buildScanRequests } = require('./scanTargets');
    const requests = await buildScanRequests(['group:us_states'], {
      channels: ['crawl', 'law_apis'],
    });

    expect(requests).toHaveLength(1);
    expect(requests[0].domains).toBe('ca_leg,ct_deep,legiscan_api');
    expect(requests[0].channels).toEqual(['crawl', 'law_apis']);
  });

  it('discover mode still issues one request per target', async () => {
    const { buildScanRequests } = require('./scanTargets');
    const requests = await buildScanRequests(
      ['group:sweden', 'group:denmark'],
      { discover: true, channels: ['crawl'] },
    );

    expect(requests).toHaveLength(2);
    expect(requests.map((r) => r.domains)).toEqual(['sweden', 'denmark']);
  });
});
