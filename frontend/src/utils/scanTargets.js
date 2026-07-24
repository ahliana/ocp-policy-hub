import { apiUrl } from '../config/api';

// Human-readable overrides for values whose formatted-from-slug form reads
// oddly (acronyms, multi-word proper nouns). Shared by RegionSelector (tree
// labels) and describeSelectionLabels below (the scan-scope summary line) so
// the same value always reads the same way in both places.
const LABEL_OVERRIDES = {
    all: 'All',
    apac: 'APAC',
    eu: 'EU',
    uk: 'United Kingdom',
    us: 'United States',
    uae: 'United Arab Emirates',
    dach: 'DACH',
    nordic: 'Nordic',
};

export function formatLabel(value) {
    if (!value) return '';
    if (LABEL_OVERRIDES[value]) return LABEL_OVERRIDES[value];

    return value
        .replace(/_/g, ' ')
        .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function splitSelection(items) {
    return {
        categories: items
            .filter((item) => item.startsWith('category:'))
            .map((item) => item.slice('category:'.length)),
        tags: items
            .filter((item) => item.startsWith('tag:'))
            .map((item) => item.slice('tag:'.length)),
        targets: items.filter(
            (item) => !item.startsWith('category:') && !item.startsWith('tag:'),
        ),
    };
}

export function normalizeTarget(item) {
    if (item.startsWith('group:') && item.includes(':region:')) {
        return item.slice(item.lastIndexOf(':region:') + ':region:'.length);
    }
    if (item.startsWith('group:')) {
        return item.slice('group:'.length);
    }
    if (item.startsWith('region:')) {
        return item.slice('region:'.length);
    }
    return item;
}

export function parseDomainTarget(item) {
    if (!item.startsWith('group:') || !item.includes(':region:')) {
        return { group: normalizeTarget(item), region: null };
    }

    const regionMarker = ':region:';
    const markerIndex = item.lastIndexOf(regionMarker);
    return {
        group: item.slice('group:'.length, markerIndex),
        region: item.slice(markerIndex + regionMarker.length),
    };
}

// Human-readable label for one raw selection value ("group:eu",
// "group:quick:region:eu", "category:energy_ministry", "tag:incentive", or a
// bare domain/region id) - feeds the scan-scope summary line so an admin can
// see in plain language what's about to be scanned.
export function describeSelectionLabel(item) {
    if (item.startsWith('category:')) {
        return `Category: ${formatLabel(item.slice('category:'.length))}`;
    }
    if (item.startsWith('tag:')) {
        return `Tag: ${formatLabel(item.slice('tag:'.length))}`;
    }
    const { group, region } = parseDomainTarget(item);
    return formatLabel(region || group);
}

export function describeSelectionLabels(selectedItems) {
    return (selectedItems || []).map(describeSelectionLabel);
}

export async function resolveDomainsForTargets(targets) {
    const domainById = new Map();

    await Promise.all(targets.map(async (target) => {
        const { group, region } = parseDomainTarget(target);
        const response = await fetch(
            apiUrl(`/api/domains?group=${encodeURIComponent(group)}`),
        );

        if (!response.ok) {
            throw new Error(`Could not resolve domains for ${group} (${response.status})`);
        }

        const data = await response.json();
        (data.domains || [])
            .filter((domain) => !region || (domain.region || []).includes(region))
            .forEach((domain) => {
                if (domain.id) {
                    domainById.set(domain.id, domain);
                }
            });
    }));

    return [...domainById.values()];
}

export const DEFAULT_CHANNELS = ['crawl', 'law_apis', 'transposition'];

export function buildChannels(selectedChannels) {
    return Array.isArray(selectedChannels) && selectedChannels.length > 0
        ? selectedChannels
        : ['crawl'];
}

export async function buildScanRequests(selectedItems, scanOptions) {
    const { categories, tags, targets } = splitSelection(selectedItems);
    const domainMatchesFilters = (domain) => (
        (!categories[0] || domain.category === categories[0])
        && (tags.length === 0 || tags.some((tag) => (domain.tags || []).includes(tag)))
    );
    const baseRequest = {
        max_concurrent: scanOptions.deep ? 10 : 5,
        skip_llm: false,
        dry_run: false,
        deep: scanOptions.deep,
        discover: scanOptions.discover,
        category: categories[0] || null,
        tags: tags.length > 0 ? tags : null,
        channels: buildChannels(scanOptions.channels),
    };

    // Discovery runs an agent workflow per country, so it stays per-target.
    if (scanOptions.discover) {
        const discoverTargets = targets.map(normalizeTarget);
        return (discoverTargets.length > 0 ? discoverTargets : ['all']).map((target) => ({
            ...baseRequest,
            domains: target,
        }));
    }

    // Everything else is ONE consolidated scan: the backend accepts a
    // comma-separated union, so one intent never fans out into a scan
    // per domain (which looked broken and buried the real results).
    const domainIds = (await resolveDomainsForTargets(targets))
        .filter(domainMatchesFilters)
        .map((domain) => domain.id);

    return [{
        ...baseRequest,
        domains: domainIds.length > 0 ? domainIds.join(',') : 'all',
    }];
}
