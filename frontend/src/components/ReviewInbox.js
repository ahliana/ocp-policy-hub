import React, { useCallback, useEffect, useState } from 'react';
import { apiUrl } from '../config/api';
import { adminHeaders } from '../utils/adminAuth';

const EARLY_STAGES = new Set(['proposed', 'consultation', 'in_committee']);
const VISIBLE_LIMIT = 5;

export function sortNewestFirst(policies) {
    return [...policies].sort((a, b) =>
        String(b.discovered_at || '').localeCompare(String(a.discovered_at || '')),
    );
}

function formatFound(discoveredAt) {
    if (!discoveredAt) return '';
    const date = new Date(discoveredAt);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleDateString();
}

function ReviewInbox({ isAdmin }) {
    const [pending, setPending] = useState([]);
    const [promotedCount, setPromotedCount] = useState(0);
    const [sheetUrl, setSheetUrl] = useState(null);
    const [error, setError] = useState('');

    const refresh = useCallback(async () => {
        try {
            // adminHeaders() so this stays unclamped under a reviewed_only
            // public visibility posture — the inbox needs review_status=new
            // items even when non-admin readers are clamped to promoted-only.
            const [newRes, promotedRes] = await Promise.all([
                fetch(apiUrl('/api/policies?review_status=new'), { headers: adminHeaders() }),
                fetch(apiUrl('/api/policies?review_status=promoted'), { headers: adminHeaders() }),
            ]);
            if (!newRes.ok) throw new Error();
            const newData = await newRes.json();
            setPending(sortNewestFirst(newData.policies || []));
            if (promotedRes.ok) {
                setPromotedCount((await promotedRes.json()).count || 0);
            }
            setError('');
        } catch {
            setError('Could not load the review queue.');
        }
    }, []);

    useEffect(() => {
        refresh();
        window.addEventListener('policy-data-changed', refresh);
        return () => window.removeEventListener('policy-data-changed', refresh);
    }, [refresh]);

    useEffect(() => {
        if (!isAdmin) {
            setSheetUrl(null);
            return;
        }
        let cancelled = false;
        fetch(apiUrl('/api/settings/sheet'), { headers: adminHeaders() })
            .then((res) => (res.ok ? res.json() : { configured: false }))
            .then((data) => {
                if (!cancelled && data.configured) setSheetUrl(data.url);
            })
            .catch(() => {});
        return () => { cancelled = true; };
    }, [isAdmin]);

    const markReviewed = async (url) => {
        try {
            const response = await fetch(apiUrl('/api/policies/review'), {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', ...adminHeaders() },
                body: JSON.stringify({ url, review_status: 'reviewed' }),
            });
            if (!response.ok) throw new Error();
            setPending((prev) => prev.filter((p) => p.url !== url));
        } catch {
            setError('Could not update the review status.');
        }
    };

    if (pending.length === 0 && !error) {
        return (
            <section className="review-inbox" aria-label="Review queue">
                <div className="review-inbox-header">
                    <h2 className="ask-box-title">New finds to review</h2>
                    {sheetUrl && (
                        <a
                            className="review-sheet-link"
                            href={sheetUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                        >
                            Open review sheet
                        </a>
                    )}
                </div>
                <p className="ask-box-hint">
                    All caught up - nothing awaiting review.
                    {promotedCount > 0 && ` ${promotedCount} already promoted to the database.`}
                </p>
            </section>
        );
    }

    const visible = pending.slice(0, VISIBLE_LIMIT);
    const hiddenCount = pending.length - visible.length;

    return (
        <section className="review-inbox" aria-label="Review queue">
            <div className="review-inbox-header">
                <h2 className="ask-box-title">New finds to review ({pending.length})</h2>
                {sheetUrl && (
                    <a
                        className="review-sheet-link"
                        href={sheetUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                    >
                        Open review sheet
                    </a>
                )}
            </div>
            <p className="ask-box-hint">
                Each row is already in the Staging sheet - fill in the judgment columns
                there, then mark it reviewed.
            </p>
            {error && <p className="ask-box-error" role="alert">{error}</p>}
            <ul className="review-inbox-list">
                {visible.map((policy) => (
                    <li key={policy.url} className="review-inbox-item">
                        <div className="review-inbox-item-main">
                            <p className="review-inbox-name">
                                <a href={policy.url} target="_blank" rel="noopener noreferrer">
                                    {policy.policy_name}
                                </a>
                                {EARLY_STAGES.has(policy.lifecycle_stage) && (
                                    <span className="review-chip review-chip-early">
                                        Early signal
                                    </span>
                                )}
                            </p>
                            <p className="review-inbox-meta">
                                {policy.jurisdiction}
                                {policy.lifecycle_stage && policy.lifecycle_stage !== 'unknown'
                                    && ` · ${policy.lifecycle_stage.replace(/_/g, ' ')}`}
                                {formatFound(policy.discovered_at)
                                    && ` · found ${formatFound(policy.discovered_at)}`}
                            </p>
                        </div>
                        {isAdmin && (
                            <button
                                type="button"
                                className="leads-dismiss-button review-done-button"
                                onClick={() => markReviewed(policy.url)}
                            >
                                Mark reviewed
                            </button>
                        )}
                    </li>
                ))}
            </ul>
            <p className="review-inbox-footer">
                {hiddenCount > 0 && `${hiddenCount} more awaiting review · `}
                {promotedCount > 0 && `${promotedCount} promoted to the database`}
            </p>
        </section>
    );
}

export default ReviewInbox;
