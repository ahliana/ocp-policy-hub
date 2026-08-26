import { useEffect, useMemo, useState } from 'react';
import { apiUrl } from '../config/api';
import { adminHeaders } from '../utils/adminAuth';
import { normalizeTarget, splitSelection } from '../utils/scanTargets';

// A real, documented bound (src/agent/orchestrator.py's PolicyAgent.run(),
// max_iterations default): discovery has no dollar estimate, but it is not
// unbounded either, so we say so instead of blacking the line out.
const DISCOVERY_COST_NOTE =
    "Discovery cost is bounded by the agent's 50-iteration search limit per "
    + 'country scan; actual cost within that varies with how much it explores.';

// One aggregated call per selection change - the backend's `domains` query
// param already accepts a comma-separated union of groups/regions/ids, so a
// 165-domain US selection is one request, not one request per domain.
async function getCostEstimate(domains, deep) {
    const params = new URLSearchParams({ domains });
    if (deep) {
        params.set('deep', 'true');
    }
    return fetch(apiUrl(`/api/cost-estimate?${params.toString()}`), {
        method: 'POST',
        headers: adminHeaders(),
    });
}

function errorStatusForResponse(status) {
    if (status === 401 || status === 403) {
        return 'unauthorized';
    }
    if (status === 400) {
        return 'bad_scope';
    }
    return 'error';
}

function formatCostEstimateText(costStatus, costEstimate) {
    if (costStatus === 'loading') {
        return 'Estimating...';
    }
    if (costStatus === 'idle') {
        return 'Pick a place or sources above to see the cost before anything runs.';
    }
    if (costStatus === 'filters_only') {
        return 'Select a scan target';
    }
    if (costStatus === 'discover') {
        return DISCOVERY_COST_NOTE;
    }
    if (costStatus === 'unauthorized') {
        return 'Sign in as admin to see estimates.';
    }
    if (costStatus === 'bad_scope') {
        return 'Unknown scan scope.';
    }
    if (costStatus === 'error') {
        return 'Estimate unavailable';
    }
    if (costStatus === 'ready' && costEstimate) {
        const targetLabel = costEstimate.target_count > 1 ? `${costEstimate.target_count} targets` : '1 target';
        const filterNote = costEstimate.has_filters ? ', filters not included' : '';
        const suffix = `(${targetLabel}${filterNote})`;

        // WP-26: the backend now sends a low/high band alongside the
        // typical (point) figure. Show the band when it's present and
        // actually spans a range; a legacy/degenerate response (band
        // fields absent, or low === high) falls back to the plain
        // point-estimate text so this stays backward compatible.
        const low = costEstimate.estimated_cost_low_usd;
        const high = costEstimate.estimated_cost_high_usd;
        const typical = Number(costEstimate.estimated_cost_usd || 0).toFixed(2);
        if (low != null && high != null && Number(low) !== Number(high)) {
            const lowText = Number(low).toFixed(2);
            const highText = Number(high).toFixed(2);
            return `$${lowText}-$${highText} (typically ~$${typical}) ${suffix}`;
        }
        return `$${typical} ${suffix}`;
    }
    // Truly-unknown fallback - every named costStatus has its own message above.
    return 'No cost estimate';
}

function useCostEstimate({ selectedRegions, mode }) {
    const [costEstimate, setCostEstimate] = useState(null);
    const [costStatus, setCostStatus] = useState('idle');

    // A stable, content-derived key rather than the selectedRegions array
    // reference itself - callers (and this hook's own tests) often pass a
    // freshly-built array each render, and depending on the reference
    // directly would re-run this effect - and reschedule the debounce timer
    // - on every render even when the actual selection hasn't changed.
    const selectionKey = selectedRegions.join(',');

    useEffect(() => {
        let isCurrent = true;
        const { categories, tags, targets } = splitSelection(selectedRegions);

        if (mode === 'discover') {
            setCostEstimate(null);
            setCostStatus('discover');
            return () => {
                isCurrent = false;
            };
        }

        if (targets.length === 0) {
            setCostEstimate(null);
            setCostStatus(selectedRegions.length === 0 ? 'idle' : 'filters_only');
            return () => {
                isCurrent = false;
            };
        }

        setCostStatus('loading');
        const domains = targets.map(normalizeTarget).join(',');

        // Debounced 300ms - selection/mode changes within that window coalesce
        // into one call instead of firing a request per click while a user is
        // still assembling their scope.
        const timerId = setTimeout(() => {
            getCostEstimate(domains, mode === 'deep')
                .then(async (response) => {
                    if (!isCurrent) return;
                    if (!response.ok) {
                        setCostEstimate(null);
                        setCostStatus(errorStatusForResponse(response.status));
                        return;
                    }
                    const data = await response.json();
                    if (!isCurrent) return;
                    setCostEstimate({
                        ...data,
                        target_count: data.domain_count,
                        has_filters: categories.length > 0 || tags.length > 0,
                    });
                    setCostStatus('ready');
                })
                .catch(() => {
                    if (!isCurrent) return;
                    setCostEstimate(null);
                    setCostStatus('error');
                });
        }, 300);

        return () => {
            isCurrent = false;
            clearTimeout(timerId);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectionKey, mode]);

    const costEstimateText = useMemo(
        () => formatCostEstimateText(costStatus, costEstimate),
        [costStatus, costEstimate],
    );

    return {
        costStatus,
        costEstimateText,
        // The estimate's domain_count - the backend's actual resolved-domain
        // count for the current selection, and the single source of truth
        // for the scan-scope summary line (WP-6) once it's ready. null while
        // loading/idle/erroring, so callers know to fall back.
        domainCount: costStatus === 'ready' && costEstimate ? costEstimate.target_count : null,
        // The raw /api/cost-estimate response (WP-26) - carries the
        // per-channel breakdown and assumptions list that costEstimateText
        // doesn't summarize. null outside the ready state.
        costEstimate: costStatus === 'ready' ? costEstimate : null,
    };
}

export default useCostEstimate;
