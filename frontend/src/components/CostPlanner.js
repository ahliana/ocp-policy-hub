import React, { useEffect, useMemo, useState } from 'react';
import { apiUrl } from '../config/api';
import { adminHeaders } from '../utils/adminAuth';
import { formatLabel } from '../utils/scanTargets';

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
    + 'from the pre-scan formula, which tends to run high. After a scope has run '
    + 'twice, real recorded costs replace the formula automatically.';

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

    return (
        <div className="cost-planner" aria-label="Cost planner">
            <h2 className="panel-heading">Cost planner</h2>
            <p className="text-block-small">
                Projects scan cost into a budget figure per scope, blending real scan history
                once a scope has run at least twice.
            </p>

            <div className="cost-planner-controls">
                <label htmlFor="cost-planner-scope-a">Scope groups (Scenario A)</label>
                <select
                    id="cost-planner-scope-a"
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
                                        <span title="No completed scans recorded for this scope yet"> *</span>
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

            {scenariosLackHistory(scenarios) && (
                <p className="cost-planner-note" role="note">{NO_HISTORY_NOTE}</p>
            )}

            <button type="button" className="button" onClick={handleCopy}>
                Copy as text
            </button>
            {copyStatus && <p role="status">{copyStatus}</p>}
        </div>
    );
}

export default CostPlanner;
