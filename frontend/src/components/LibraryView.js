import React, { useCallback, useEffect, useRef, useState } from 'react';
import { apiUrl } from '../config/api';
import { adminHeaders } from '../utils/adminAuth';
import SavedPolicy from './SavedPolicy';

const PAGE_SIZE = 25;

const REVIEW_STATUS_OPTIONS = ['new', 'reviewed', 'promoted', 'rejected'];

// Same order as src.core.models.LIFECYCLE_STAGES.
const LIFECYCLE_STAGE_OPTIONS = [
    'proposed', 'consultation', 'in_committee', 'passed',
    'enacted', 'transposition_notified', 'amended', 'unknown',
];

// Sensible default direction per backend sort key (src/storage/store.py
// PolicyStore._SORT_COLUMNS) — a fresh click on a column starts here.
const SORT_DEFAULT_DIR = {
    name: 'asc',
    jurisdiction: 'asc',
    relevance: 'desc',
    discovered_at: 'desc',
};

const COLUMNS = [
    { key: 'name', label: 'Name', sortKey: 'name' },
    { key: 'jurisdiction', label: 'Jurisdiction', sortKey: 'jurisdiction' },
    { key: 'lifecycle_stage', label: 'Stage', sortKey: null },
    { key: 'review_status', label: 'Status', sortKey: null },
    { key: 'relevance_score', label: 'Score', sortKey: 'relevance' },
    { key: 'domain_id', label: 'Source', sortKey: null },
    { key: 'discovered_at', label: 'Discovered', sortKey: 'discovered_at' },
];

const TOTAL_COLUMN_COUNT = COLUMNS.length + 1; // + Actions

function formatDiscovered(value) {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString();
}

function rowStatusClass(status) {
    if (status === 'promoted') return 'library-row-promoted';
    if (status === 'new') return 'library-row-new';
    if (status === 'rejected') return 'library-row-rejected';
    return '';
}

// Admin review surface over the full persisted database (WP-4 "the
// Library") — GET /api/policies/library, paginated/sorted/filtered in SQL
// (see src/storage/store.py). Distinct from PolicyList: no in-memory scan
// merge, no public-visibility clamp, rejected rows included.
function LibraryView() {
    const [policies, setPolicies] = useState([]);
    const [total, setTotal] = useState(0);
    const [offset, setOffset] = useState(0);
    const [reviewStatus, setReviewStatus] = useState('');
    const [lifecycleStage, setLifecycleStage] = useState('');
    const [jurisdictionInput, setJurisdictionInput] = useState('');
    const [jurisdictionQuery, setJurisdictionQuery] = useState('');
    const [sort, setSort] = useState(null);
    const [sortDir, setSortDir] = useState(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState('');
    const [expandedUrl, setExpandedUrl] = useState(null);
    const [rejectPromptUrl, setRejectPromptUrl] = useState(null);
    const [rejectReason, setRejectReason] = useState('');
    const debounceRef = useRef(null);

    // Debounce the jurisdiction text box by 300ms before it becomes a query
    // param, matching PolicyList's free-text search debounce.
    useEffect(() => {
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(() => {
            setJurisdictionQuery(jurisdictionInput.trim());
            setOffset(0);
        }, 300);
        return () => {
            if (debounceRef.current) clearTimeout(debounceRef.current);
        };
    }, [jurisdictionInput]);

    const fetchLibrary = useCallback(async () => {
        setIsLoading(true);
        setError('');
        try {
            const params = new URLSearchParams({
                limit: String(PAGE_SIZE),
                offset: String(offset),
            });
            if (reviewStatus) params.set('review_status', reviewStatus);
            if (lifecycleStage) params.set('lifecycle_stage', lifecycleStage);
            if (jurisdictionQuery) params.set('jurisdiction', jurisdictionQuery);
            if (sort) {
                params.set('sort', sort);
                params.set('sort_dir', sortDir || SORT_DEFAULT_DIR[sort]);
            }
            const response = await fetch(
                apiUrl(`/api/policies/library?${params.toString()}`),
                { headers: adminHeaders() },
            );
            if (!response.ok) throw new Error();
            const data = await response.json();
            setPolicies(Array.isArray(data.policies) ? data.policies : []);
            setTotal(data.total || 0);
        } catch {
            setError('Could not load the library. Check that the backend is running, then refresh.');
        } finally {
            setIsLoading(false);
        }
    }, [reviewStatus, lifecycleStage, jurisdictionQuery, sort, sortDir, offset]);

    useEffect(() => {
        fetchLibrary();
    }, [fetchLibrary]);

    const handleSortClick = (sortKey) => {
        setOffset(0);
        if (sort === sortKey) {
            setSortDir((prev) => (prev === 'asc' ? 'desc' : 'asc'));
        } else {
            setSort(sortKey);
            setSortDir(SORT_DEFAULT_DIR[sortKey]);
        }
    };

    const ariaSortFor = (sortKey) => {
        if (sort !== sortKey) return 'none';
        return sortDir === 'asc' ? 'ascending' : 'descending';
    };

    const patchReview = async (url, nextStatus, reason) => {
        try {
            const body = { url, review_status: nextStatus };
            if (reason) body.reason = reason;
            const response = await fetch(apiUrl('/api/policies/review'), {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', ...adminHeaders() },
                body: JSON.stringify(body),
            });
            if (!response.ok) throw new Error();
            setRejectPromptUrl(null);
            setRejectReason('');
            await fetchLibrary();
        } catch {
            setError('Could not update that policy. Please try again.');
        }
    };

    const handleClear = () => {
        setReviewStatus('');
        setLifecycleStage('');
        setJurisdictionInput('');
        setJurisdictionQuery('');
        setOffset(0);
    };

    const start = total === 0 ? 0 : offset + 1;
    const end = Math.min(offset + PAGE_SIZE, total);

    return (
        <section className="library-view" aria-label="Library">
            <h2 className="ask-box-title">Library - everything in the database</h2>

            <div className="library-filters">
                <label className="library-filter-label">
                    Review status
                    <select
                        value={reviewStatus}
                        onChange={(event) => {
                            setReviewStatus(event.target.value);
                            setOffset(0);
                        }}
                    >
                        <option value="">All</option>
                        {REVIEW_STATUS_OPTIONS.map((status) => (
                            <option key={status} value={status}>{status}</option>
                        ))}
                    </select>
                </label>
                <label className="library-filter-label">
                    Lifecycle stage
                    <select
                        value={lifecycleStage}
                        onChange={(event) => {
                            setLifecycleStage(event.target.value);
                            setOffset(0);
                        }}
                    >
                        <option value="">All</option>
                        {LIFECYCLE_STAGE_OPTIONS.map((stage) => (
                            <option key={stage} value={stage}>{stage.replaceAll('_', ' ')}</option>
                        ))}
                    </select>
                </label>
                <label className="library-filter-label">
                    Jurisdiction
                    <input
                        type="text"
                        value={jurisdictionInput}
                        onChange={(event) => setJurisdictionInput(event.target.value)}
                        placeholder="Filter by jurisdiction"
                    />
                </label>
                <button type="button" className="library-clear-button" onClick={handleClear}>
                    Clear
                </button>
            </div>

            {error && <p className="ask-box-error" role="alert">{error}</p>}

            {!error && !isLoading && policies.length === 0 && (
                <p className="library-empty">No policies match these filters.</p>
            )}

            {policies.length > 0 && (
                <div className="library-table-wrap">
                    <table className="library-table">
                        <thead>
                            <tr>
                                {COLUMNS.map((column) => (
                                    <th
                                        key={column.key}
                                        aria-sort={column.sortKey ? ariaSortFor(column.sortKey) : undefined}
                                    >
                                        {column.sortKey ? (
                                            <button
                                                type="button"
                                                className="library-sort-button"
                                                onClick={() => handleSortClick(column.sortKey)}
                                            >
                                                {column.label}
                                            </button>
                                        ) : column.label}
                                    </th>
                                ))}
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {policies.map((policy) => (
                                <React.Fragment key={policy.url}>
                                    <tr className={rowStatusClass(policy.review_status)}>
                                        <td>
                                            <button
                                                type="button"
                                                className="library-name-button"
                                                onClick={() => setExpandedUrl(
                                                    (prev) => (prev === policy.url ? null : policy.url),
                                                )}
                                            >
                                                {policy.policy_name}
                                            </button>
                                        </td>
                                        <td>{policy.jurisdiction}</td>
                                        <td>{policy.lifecycle_stage}</td>
                                        <td>{policy.review_status}</td>
                                        <td>{policy.relevance_score}</td>
                                        <td>{policy.domain_id}</td>
                                        <td>{formatDiscovered(policy.discovered_at)}</td>
                                        <td className="library-actions">
                                            {policy.review_status !== 'promoted' && (
                                                <button
                                                    type="button"
                                                    onClick={() => patchReview(policy.url, 'promoted')}
                                                >
                                                    Promote
                                                </button>
                                            )}
                                            {policy.review_status === 'rejected' ? (
                                                <button
                                                    type="button"
                                                    onClick={() => patchReview(policy.url, 'new')}
                                                >
                                                    Restore
                                                </button>
                                            ) : (
                                                <button
                                                    type="button"
                                                    onClick={() => setRejectPromptUrl(
                                                        (prev) => (prev === policy.url ? null : policy.url),
                                                    )}
                                                >
                                                    Reject
                                                </button>
                                            )}
                                        </td>
                                    </tr>
                                    {rejectPromptUrl === policy.url && (
                                        <tr className="library-reject-prompt-row">
                                            <td colSpan={TOTAL_COLUMN_COUNT}>
                                                <label>
                                                    Reason (optional)
                                                    <input
                                                        type="text"
                                                        value={rejectReason}
                                                        maxLength={500}
                                                        onChange={(event) => setRejectReason(event.target.value)}
                                                    />
                                                </label>
                                                <button
                                                    type="button"
                                                    onClick={() => patchReview(
                                                        policy.url, 'rejected', rejectReason || undefined,
                                                    )}
                                                >
                                                    Confirm reject
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => {
                                                        setRejectPromptUrl(null);
                                                        setRejectReason('');
                                                    }}
                                                >
                                                    Cancel
                                                </button>
                                            </td>
                                        </tr>
                                    )}
                                    {expandedUrl === policy.url && (
                                        <tr className="library-detail-row">
                                            <td colSpan={TOTAL_COLUMN_COUNT}>
                                                <SavedPolicy policy={policy} />
                                            </td>
                                        </tr>
                                    )}
                                </React.Fragment>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            <div className="library-pagination">
                <button
                    type="button"
                    disabled={offset === 0}
                    onClick={() => setOffset((prev) => Math.max(0, prev - PAGE_SIZE))}
                >
                    Previous
                </button>
                <span>Showing {start}-{end} of {total}</span>
                <button
                    type="button"
                    disabled={end >= total}
                    onClick={() => setOffset((prev) => prev + PAGE_SIZE)}
                >
                    Next
                </button>
            </div>
        </section>
    );
}

export default LibraryView;
