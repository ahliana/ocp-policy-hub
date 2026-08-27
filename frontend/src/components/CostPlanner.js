import React, { useEffect, useMemo, useState } from 'react';
import { apiUrl } from '../config/api';
import { adminHeaders } from '../utils/adminAuth';
import { formatLabel } from '../utils/scanTargets';
import HelpNote from './HelpNote';
import InfoHotspot from './InfoHotspot';

const CADENCES = [
    { id: 'monthly', label: 'Monthly' },
    { id: 'weekly', label: 'Weekly' },
    { id: 'quarterly', label: 'Quarterly' },
];

function groupOptionLabel(id, description) {
    return description && description !== 'No description'
        ? `${formatLabel(id)} - ${description}`
        : formatLabel(id);
}

function formatUsd(value) {
    return value == null ? '-' : `$${Number(value).toFixed(2)}`;
}

function selectedValues(event) {
    return Array.from(event.target.selectedOptions).map((option) => option.value);
}

function formatScanDate(value) {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString();
}

function formatScanStatus(status) {
    return status === 'completed_budget_reached' ? 'completed (budget cap reached)' : status;
}

// Percent (actual - estimate)/estimate, rounded to a whole number. null
// (rendered as "-") whenever either figure is missing - legacy rows before
// WP-24 have no estimate on file - or the estimate is zero/negative, which
// would make the percentage meaningless.
function estimateDifferencePercent(estimatedUsd, actualUsd) {
    if (estimatedUsd == null || actualUsd == null || estimatedUsd <= 0) return null;
    return Math.round(((actualUsd - estimatedUsd) / estimatedUsd) * 100);
}

function formatDifferencePercent(percent) {
    if (percent == null) return '-';
    return percent > 0 ? `+${percent}%` : `${percent}%`;
}

async function fetchProjection(groups, cadence) {
    const params = new URLSearchParams({ groups: groups.join(','), cadence });
    const response = await fetch(apiUrl(`/api/cost-projection?${params.toString()}`), {
        headers: adminHeaders(),
    });
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Cost projection request failed (${response.status})`);
    }
    return response.json();
}

// Scopes with no completed scan history are priced by the pre-scan formula
// alone, which tends to run high - say so wherever the numbers appear, so a
// large figure is never mistaken for a grounded one.
export const NO_HISTORY_NOTE =
    'Scopes marked * have no completed scans recorded yet - their figures come '
    + 'from the pre-scan formula, which tends to run high. After a scope has '
    + 'completed two real scans, recorded costs replace the formula automatically.';

export function scenariosLackHistory(scenarios) {
    return scenarios.some(({ projection }) => (
        projection && projection.items.some((item) => !item.history)
    ));
}

// Plain-text, fixed-width columns - this feeds a funding email, so it has to
// render cleanly without markdown table pipes.
export function buildPlainTextTable(scenarios, cadence) {
    const headers = ['Scenario', 'Group', 'Est/run', 'Actual mean', `Projected/${cadence}`];
    const rows = [];

    scenarios.forEach(({ name, projection }) => {
        if (!projection) return;
        projection.items.forEach((item) => {
            rows.push([
                name,
                item.history ? item.group : `${item.group} *`,
                formatUsd(item.estimate_usd),
                item.history ? formatUsd(item.history.mean_cost_usd) : '-',
                formatUsd(item.per_month_usd),
            ]);
        });
        rows.push([name, 'TOTAL', '-', '-', formatUsd(projection.total_per_month_usd)]);
    });

    const widths = headers.map((header, col) => Math.max(
        header.length,
        ...rows.map((row) => String(row[col]).length),
    ));
    const formatRow = (cells) => (
        cells.map((cell, col) => String(cell).padEnd(widths[col])).join('  ').trimEnd()
    );

    const lines = [formatRow(headers), ...rows.map(formatRow)];
    if (scenariosLackHistory(scenarios)) {
        lines.push('', NO_HISTORY_NOTE);
    }
    return lines.join('\n');
}

// Cost planner (WP-7) - projects estimate_cost()/scan-history actuals into a
// monthly/weekly/quarterly budget figure per scope group, with an optional
// second scenario for side-by-side comparison. Lives in the admin area,
// below the Library.
function CostPlanner() {
    const [groupOptions, setGroupOptions] = useState({});
    const [scopeA, setScopeA] = useState([]);
    const [scopeB, setScopeB] = useState([]);
    const [compareEnabled, setCompareEnabled] = useState(false);
    const [cadence, setCadence] = useState('monthly');
    const [projectionA, setProjectionA] = useState(null);
    const [projectionB, setProjectionB] = useState(null);
    const [error, setError] = useState('');
    const [copyStatus, setCopyStatus] = useState('');
    // null = not fetched yet (renders nothing); [] = fetched, no scans yet
    // (renders the empty-state message).
    const [recentScans, setRecentScans] = useState(null);

    useEffect(() => {
        let isCurrent = true;
        fetch(apiUrl('/api/groups'), { headers: adminHeaders() })
            .then((response) => (response.ok ? response.json() : {}))
            .then((data) => {
                if (isCurrent) setGroupOptions(data || {});
            })
            .catch(() => {
                if (isCurrent) setGroupOptions({});
            });
        return () => {
            isCurrent = false;
        };
    }, []);

    // Recent scans (WP-24) - estimate vs. actual, most recent 10 runs.
    useEffect(() => {
        let isCurrent = true;
        fetch(apiUrl('/api/scans/history?limit=10'), { headers: adminHeaders() })
            .then((response) => (response.ok ? response.json() : { scans: [] }))
            .then((data) => {
                if (isCurrent) setRecentScans((data && data.scans) || []);
            })
            .catch(() => {
                if (isCurrent) setRecentScans([]);
            });
        return () => {
            isCurrent = false;
        };
    }, []);

    useEffect(() => {
        let isCurrent = true;
        if (scopeA.length === 0) {
            setProjectionA(null);
            return () => {
                isCurrent = false;
            };
        }
        setError('');
        fetchProjection(scopeA, cadence)
            .then((data) => {
                if (isCurrent) setProjectionA(data);
            })
            .catch((err) => {
                if (isCurrent) {
                    setProjectionA(null);
                    setError(err.message);
                }
            });
        return () => {
            isCurrent = false;
        };
    }, [scopeA, cadence]);

    useEffect(() => {
        let isCurrent = true;
        if (!compareEnabled || scopeB.length === 0) {
            setProjectionB(null);
            return () => {
                isCurrent = false;
            };
        }
        setError('');
        fetchProjection(scopeB, cadence)
            .then((data) => {
                if (isCurrent) setProjectionB(data);
            })
            .catch((err) => {
                if (isCurrent) {
                    setProjectionB(null);
                    setError(err.message);
                }
            });
        return () => {
            isCurrent = false;
        };
    }, [scopeB, cadence, compareEnabled]);

    const scenarios = useMemo(() => {
        const list = [{ name: 'Scenario A', projection: projectionA }];
        if (compareEnabled) list.push({ name: 'Scenario B', projection: projectionB });
        return list;
    }, [projectionA, projectionB, compareEnabled]);

    // Rolling accuracy sentence (WP-24) - only counts rows where both an
    // estimate and an actual cost are on file; needs at least two to say
    // anything meaningful.
    const qualifyingDiffPercents = useMemo(() => (
        (recentScans || [])
            .map((scan) => estimateDifferencePercent(scan.estimated_cost_usd, scan.cost_usd))
            .filter((percent) => percent != null)
    ), [recentScans]);
    const accuracySentence = qualifyingDiffPercents.length >= 2
        ? `Estimates were within ${Math.max(...qualifyingDiffPercents.map(Math.abs))}% of actual `
            + `cost on the last ${qualifyingDiffPercents.length} scans.`
        : null;

    const handleCopy = async () => {
        const text = buildPlainTextTable(scenarios, cadence);
        try {
            await navigator.clipboard.writeText(text);
            setCopyStatus('Copied to clipboard.');
        } catch {
            setCopyStatus('Could not copy automatically - select the table and copy manually.');
        }
    };

    const groupEntries = Object.entries(groupOptions);
    const hasAnyProjection = scenarios.some(({ projection }) => projection);

    return (
        <div className="cost-planner" aria-label="Cost planner">
            <h2 className="panel-heading">Cost planner</h2>
            <p className="text-block-small">
                Projects scan cost into a budget figure per scope, blending real scan history
                once a scope has run at least twice.
            </p>
            <HelpNote label="How these projections work" className="cost-planner-help-note">
                <p>
                    Each figure starts from the pre-scan formula, which tends to run high.
                    Once a scope has completed two real scans, its recorded costs replace the
                    formula automatically - and the Recent scans table below shows how close
                    past estimates came to real costs. Pick scope groups and a cadence to see
                    a monthly figure you can copy as plain text into an email.
                </p>
            </HelpNote>

            <div className="cost-planner-controls">
                <label htmlFor="cost-planner-scope-a">Scope groups (Scenario A)</label>
                <select
                    id="cost-planner-scope-a"
                    className="cost-planner-scope-select"
                    multiple
                    value={scopeA}
                    onChange={(event) => setScopeA(selectedValues(event))}
                >
                    {groupEntries.map(([id, description]) => (
                        <option key={id} value={id}>{groupOptionLabel(id, description)}</option>
                    ))}
                </select>

                <label htmlFor="cost-planner-cadence">Cadence</label>
                <select
                    id="cost-planner-cadence"
                    value={cadence}
                    onChange={(event) => setCadence(event.target.value)}
                >
                    {CADENCES.map((option) => (
                        <option key={option.id} value={option.id}>{option.label}</option>
                    ))}
                </select>

                <label htmlFor="cost-planner-compare-toggle">
                    <input
                        id="cost-planner-compare-toggle"
                        type="checkbox"
                        checked={compareEnabled}
                        onChange={(event) => setCompareEnabled(event.target.checked)}
                    />
                    Compare with a second scope
                </label>

                {compareEnabled && (
                    <>
                        <label htmlFor="cost-planner-scope-b">Scope groups (Scenario B)</label>
                        <select
                            id="cost-planner-scope-b"
                            className="cost-planner-scope-select"
                            multiple
                            value={scopeB}
                            onChange={(event) => setScopeB(selectedValues(event))}
                        >
                            {groupEntries.map(([id, description]) => (
                                <option key={id} value={id}>{groupOptionLabel(id, description)}</option>
                            ))}
                        </select>
                    </>
                )}
            </div>

            {error && <p className="cost-planner-error" role="alert">{error}</p>}

            {hasAnyProjection ? (
                <div className="admin-table-wrap">
                    <table className="cost-planner-table">
                        <thead>
                            <tr>
                                {compareEnabled && <th>Scenario</th>}
                                <th>Group</th>
                                <th>Estimate/run</th>
                                <th>Actual mean</th>
                                <th>{`Projected/${cadence}`}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {scenarios.flatMap(({ name, projection }) => {
                                if (!projection) return [];
                                const itemRows = projection.items.map((item) => (
                                    <tr key={`${name}-${item.group}`}>
                                        {compareEnabled && <td>{name}</td>}
                                        <td>
                                            {item.group}
                                            {!item.history && (
                                                <span className="no-history-marker">
                                                    {' *'}
                                                    <InfoHotspot label="What the * means">
                                                        No completed scans recorded for this scope
                                                        yet - its figure comes from the formula,
                                                        not from real costs.
                                                    </InfoHotspot>
                                                </span>
                                            )}
                                        </td>
                                        <td>{formatUsd(item.estimate_usd)}</td>
                                        <td>{item.history ? formatUsd(item.history.mean_cost_usd) : '-'}</td>
                                        <td>{formatUsd(item.per_month_usd)}</td>
                                    </tr>
                                ));
                                const totalRow = (
                                    <tr key={`${name}-total`} className="cost-planner-total-row">
                                        {compareEnabled && <td>{name}</td>}
                                        <td>Total</td>
                                        <td>-</td>
                                        <td>-</td>
                                        <td>{formatUsd(projection.total_per_month_usd)}</td>
                                    </tr>
                                );
                                return [...itemRows, totalRow];
                            })}
                        </tbody>
                    </table>
                </div>
            ) : (
                <p className="cost-planner-empty">Pick one or more scope groups to see projected costs.</p>
            )}

            {scenariosLackHistory(scenarios) && (
                <p className="cost-planner-note" role="note">{NO_HISTORY_NOTE}</p>
            )}

            <button type="button" className="button" onClick={handleCopy}>
                Copy as text
            </button>
            {copyStatus && <p role="status">{copyStatus}</p>}

            <div className="recent-scans">
                <h3 className="recent-scans-heading">Recent scans</h3>
                {recentScans && recentScans.length === 0 && (
                    <p className="recent-scans-empty">
                        No scans recorded yet - the first scheduled scan will appear here.
                    </p>
                )}
                {recentScans && recentScans.length > 0 && (
                    <>
                        <div className="admin-table-wrap">
                            <table className="cost-planner-table recent-scans-table">
                                <thead>
                                    <tr>
                                        <th>Date</th>
                                        <th>Scope</th>
                                        <th>Status</th>
                                        <th>Estimated</th>
                                        <th>Actual</th>
                                        <th>
                                            <span>Difference</span>
                                            <InfoHotspot label="What Difference means">
                                                How far the estimate was from the real cost - plus
                                                means it cost more than estimated.
                                            </InfoHotspot>
                                        </th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {recentScans.map((scan, index) => (
                                        <tr key={scan.scan_id || index}>
                                            <td>{formatScanDate(scan.started_at)}</td>
                                            <td>{scan.domain_group}</td>
                                            <td>
                                                <span>{formatScanStatus(scan.status)}</span>
                                                {scan.status === 'completed_budget_reached' && (
                                                    <InfoHotspot label="What this status means">
                                                        This scan stopped starting new sites when
                                                        it reached its spending cap; everything
                                                        already running was finished and kept.
                                                    </InfoHotspot>
                                                )}
                                            </td>
                                            <td>{formatUsd(scan.estimated_cost_usd)}</td>
                                            <td>{formatUsd(scan.cost_usd)}</td>
                                            <td>
                                                {formatDifferencePercent(
                                                    estimateDifferencePercent(scan.estimated_cost_usd, scan.cost_usd),
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        {accuracySentence && (
                            <p className="recent-scans-accuracy">{accuracySentence}</p>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}

export default CostPlanner;
