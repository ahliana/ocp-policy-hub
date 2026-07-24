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
        const cost = Number(costEstimate.estimated_cost_usd || 0).toFixed(2);
        const targetLabel = costEstimate.target_count > 1 ? `${costEstimate.target_count} targets` : '1 target';
        const filterNote = costEstimate.has_filters ? ', filters not included' : '';
        return `$${cost} (${targetLabel}${filterNote})`;
    }
    return 'No cost estimate';
}

function useCostEstimate({ selectedRegions, mode }) {
    const [costEstimate, setCostEstimate] = useState(null);
    const [costStatus, setCostStatus] = useState('idle');

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

        return () => {
            isCurrent = false;
        };
    }, [selectedRegions, mode]);

    const costEstimateText = useMemo(
        () => formatCostEstimateText(costStatus, costEstimate),
        [costStatus, costEstimate],
    );

    return {
        costStatus,
        costEstimateText,
    };
}

export default useCostEstimate;
