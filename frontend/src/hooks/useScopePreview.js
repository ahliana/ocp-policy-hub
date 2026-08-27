import { useEffect, useState } from 'react';
import {
    domainChannel, formatLabel, resolveDomainsForTargets, splitSelection,
} from '../utils/scanTargets';

// Plain-language channel headings (WP-28) - same three channels the cost
// breakdown (WP-26) itemizes, ordered the same way.
const CHANNEL_LABELS = {
    crawl: 'Government websites',
    law_apis: 'Law databases',
    transposition: 'EU law trackers',
};

const CHANNEL_ORDER = ['crawl', 'law_apis', 'transposition'];

// The most specific region a domain is tagged with - config lists broad-to-
// narrow (e.g. ["eu", "eu_central", "germany", "hessen"]), so the last entry
// is the one worth showing next to the source name.
function mostSpecificRegion(domain) {
    const regions = domain.region || [];
    return regions.length > 0 ? regions[regions.length - 1] : '';
}

function groupDomains(domains) {
    return CHANNEL_ORDER
        .map((channelId) => ({
            id: channelId,
            label: CHANNEL_LABELS[channelId],
            entries: domains
                .filter((domain) => domainChannel(domain) === channelId)
                .map((domain) => ({
                    id: domain.id,
                    name: domain.name,
                    region: formatLabel(mostSpecificRegion(domain)),
                })),
        }))
        .filter((group) => group.entries.length > 0);
}

// Lazy: resolving the full domain list is one fetch per selected
// group/region (see resolveDomainsForTargets), so this only runs once the
// caller says the preview is actually open (`active`) - not on every
// selection tweak while it stays closed. Re-opening after the selection
// changed resolves fresh, since the effect keys off `active`, not the
// selection - each open reads whatever is currently selected at that moment.
function useScopePreview({ selectedRegions, active }) {
    const [status, setStatus] = useState('idle');
    const [groups, setGroups] = useState([]);
    const [totalCount, setTotalCount] = useState(0);

    useEffect(() => {
        if (!active) return undefined;

        const { targets } = splitSelection(selectedRegions || []);
        if (targets.length === 0) {
            setStatus('empty');
            setGroups([]);
            setTotalCount(0);
            return undefined;
        }

        let isCurrent = true;
        setStatus('loading');

        resolveDomainsForTargets(targets)
            .then((domains) => {
                if (!isCurrent) return;
                setGroups(groupDomains(domains));
                setTotalCount(domains.length);
                setStatus('ready');
            })
            .catch(() => {
                if (!isCurrent) return;
                setGroups([]);
                setTotalCount(0);
                setStatus('error');
            });

        return () => {
            isCurrent = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [active]);

    return { status, groups, totalCount };
}

export default useScopePreview;
