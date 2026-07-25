import React, { useEffect, useMemo, useState } from 'react';
import { apiUrl } from '../config/api';
import { adminHeaders } from '../utils/adminAuth';

const PAGE_SIZE = 50;

function keyStatusLabel(keyStatus) {
    if (!keyStatus) return '-';
    if (!keyStatus.required_env) return 'No key needed';
    return keyStatus.configured ? 'Key configured' : 'Key missing';
}

async function fetchSources() {
    const response = await fetch(apiUrl('/api/sources/status'), { headers: adminHeaders() });
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Sources request failed (${response.status})`);
    }
    return response.json();
}

async function putEnabled(id, enabled) {
    const response = await fetch(apiUrl(`/api/sources/${id}/enabled`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...adminHeaders() },
        body: JSON.stringify({ enabled }),
    });
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Update failed (${response.status})`);
    }
    return response.json();
}

// Sources panel (WP-9) - every configured domain/source, its YAML-enabled
// state, the admin enabled-override, and (for structured connectors)
// whether the required API key is set. Lives in the admin area, below the
// Cost planner.
function SourcesPanel() {
    const [sources, setSources] = useState([]);
    const [loadError, setLoadError] = useState('');
    const [toggleError, setToggleError] = useState('');
    const [search, setSearch] = useState('');
    const [typeFilter, setTypeFilter] = useState('');
    const [regionFilter, setRegionFilter] = useState('');
    const [missingKeysOnly, setMissingKeysOnly] = useState(false);
    const [page, setPage] = useState(1);

    useEffect(() => {
        let isCurrent = true;
        fetchSources()
            .then((data) => {
                if (isCurrent) setSources(data.sources || []);
            })
            .catch((err) => {
                if (isCurrent) setLoadError(err.message);
            });
        return () => {
            isCurrent = false;
        };
    }, []);

    const types = useMemo(
        () => Array.from(new Set(sources.map((s) => s.type))).sort(),
        [sources],
    );

    const summary = useMemo(() => {
        const total = sources.length;
        const enabled = sources.filter((s) => s.effective_enabled).length;
        const connectors = sources.filter((s) => s.key_status !== null);
        const missingKeys = connectors.filter((s) => !s.key_status.configured).length;
        return { total, enabled, connectorCount: connectors.length, missingKeys };
    }, [sources]);

    const filtered = useMemo(() => {
        const needle = search.trim().toLowerCase();
        const region = regionFilter.trim().toLowerCase();
        return sources.filter((s) => {
            if (needle && !s.name.toLowerCase().includes(needle) && !s.id.toLowerCase().includes(needle)) {
                return false;
            }
            if (typeFilter && s.type !== typeFilter) return false;
            if (region && !(s.region || []).some((r) => r.toLowerCase().includes(region))) return false;
            if (missingKeysOnly && !(s.key_status && !s.key_status.configured)) return false;
            return true;
        });
    }, [sources, search, typeFilter, regionFilter, missingKeysOnly]);

    useEffect(() => {
        setPage(1);
    }, [search, typeFilter, regionFilter, missingKeysOnly]);

    const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    const currentPage = Math.min(page, pageCount);
    const pageRows = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

    const handleToggle = async (row) => {
        setToggleError('');
        const nextEnabled = !row.effective_enabled;
        setSources((current) => current.map((s) => (
            s.id === row.id
                ? { ...s, effective_enabled: nextEnabled, enabled_override: nextEnabled }
                : s
        )));
        try {
            await putEnabled(row.id, nextEnabled);
        } catch (err) {
            // Revert ONLY this row - a whole-array snapshot restore would
            // stomp another row's concurrent optimistic update if two
            // toggles are in flight and this one fails.
            setSources((current) => current.map((s) => (
                s.id === row.id
                    ? {
                        ...s,
                        effective_enabled: row.effective_enabled,
                        enabled_override: row.enabled_override,
                    }
                    : s
            )));
            setToggleError(err.message);
        }
    };

    return (
        <div className="sources-panel" aria-label="Sources panel">
            <h2 className="panel-heading">Sources - what PolicyPulse watches</h2>
            {loadError && <p role="alert">{loadError}</p>}
            <p className="sources-summary">
                {`${summary.total} sources - ${summary.enabled} enabled - `}
                {`${summary.connectorCount} API connectors, ${summary.missingKeys} missing keys`}
            </p>

            <div className="sources-filters">
                <label htmlFor="sources-search">Search sources</label>
                <input
                    id="sources-search"
                    type="text"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="Name or id"
                />

                <label htmlFor="sources-type-filter">Type</label>
                <select
                    id="sources-type-filter"
                    value={typeFilter}
                    onChange={(event) => setTypeFilter(event.target.value)}
                >
                    <option value="">All types</option>
                    {types.map((type) => (
                        <option key={type} value={type}>{type}</option>
                    ))}
                </select>

                <label htmlFor="sources-region-filter">Region</label>
                <input
                    id="sources-region-filter"
                    type="text"
                    value={regionFilter}
                    onChange={(event) => setRegionFilter(event.target.value)}
                    placeholder="e.g. sweden"
                />

                <label htmlFor="sources-missing-keys-only">
                    <input
                        id="sources-missing-keys-only"
                        type="checkbox"
                        checked={missingKeysOnly}
                        onChange={(event) => setMissingKeysOnly(event.target.checked)}
                    />
                    Missing keys only
                </label>
            </div>

            {toggleError && <p role="alert">{toggleError}</p>}

            <table className="sources-table">
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Type</th>
                        <th>Regions</th>
                        <th>Key status</th>
                        <th>Enabled</th>
                    </tr>
                </thead>
                <tbody>
                    {pageRows.map((row) => (
                        <tr key={row.id}>
                            <td>{row.name}</td>
                            <td><span className="type-badge">{row.type}</span></td>
                            <td>{(row.region || []).join(', ')}</td>
                            <td>
                                {row.key_status && (
                                    <span
                                        className={
                                            row.key_status.configured
                                                ? 'key-status-badge key-status-ok'
                                                : 'key-status-badge key-status-missing'
                                        }
                                    >
                                        {keyStatusLabel(row.key_status)}
                                    </span>
                                )}
                                {!row.key_status && '-'}
                            </td>
                            <td>
                                <button
                                    type="button"
                                    className="button"
                                    aria-label={`Toggle ${row.name}`}
                                    onClick={() => handleToggle(row)}
                                >
                                    {row.effective_enabled ? 'Enabled' : 'Disabled'}
                                </button>
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>

            <div className="sources-pagination">
                <button
                    type="button"
                    className="button"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={currentPage <= 1}
                >
                    Previous
                </button>
                <span>{`Page ${currentPage} of ${pageCount}`}</span>
                <button
                    type="button"
                    className="button"
                    onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                    disabled={currentPage >= pageCount}
                >
                    Next
                </button>
            </div>

            <p className="sources-note" role="note">
                Changes apply to future scans - a scan already running is not affected.
            </p>
        </div>
    );
}

export default SourcesPanel;
