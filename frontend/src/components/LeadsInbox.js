import React, { useCallback, useEffect, useState } from 'react';
import Tooltip from '@mui/material/Tooltip';
import { apiUrl } from '../config/api';
import { adminHeaders } from '../utils/adminAuth';
import { cleanTipText } from '../utils/plainText';

function formatWhen(isoString) {
    if (!isoString) return '';
    const parsed = new Date(isoString);
    return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleString();
}

const UNTITLED_FALLBACK = 'Untitled source';

// Cleans a tip's title for display, falling back to a cleaned second field
// (a note or the source URL) when the title itself has nothing readable in
// it - e.g. a Google News RSS item with no title but a note, or a bare
// redirect URL with neither.
function cleanCardTitle(title, fallbackRaw) {
    const cleanedTitle = cleanTipText(title, '');
    if (cleanedTitle) return cleanedTitle;
    return cleanTipText(fallbackRaw, UNTITLED_FALLBACK);
}

// User-facing vocabulary is "Tips" (this file keeps its LeadsInbox name and
// talks to /api/tips - see src/api/routes/leads.py for the API-side mapping
// between the public "tip" vocabulary and the internal Lead/LeadStore
// storage layer).
function LeadsInbox({ adminRequired = false, hasAdminToken = false }) {
    const [leads, setLeads] = useState([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState(null);
    const [isExpanded, setIsExpanded] = useState(true);
    const [suggestUrl, setSuggestUrl] = useState('');
    const [suggestNote, setSuggestNote] = useState('');
    const [isSuggesting, setIsSuggesting] = useState(false);
    const [suggestMessage, setSuggestMessage] = useState('');
    const [busyLeadId, setBusyLeadId] = useState(null);

    const isAdminLocked = adminRequired && !hasAdminToken;

    const loadLeads = useCallback(async () => {
        setError(null);
        try {
            const response = await fetch(apiUrl('/api/tips'));
            if (!response.ok) {
                throw new Error(`Failed to load tips (${response.status})`);
            }
            const data = await response.json();
            const all = Array.isArray(data.leads) ? data.leads : [];
            // Dismissed tips are gone for good; everything else (new,
            // including a fetch-failed retry candidate, and chased, so its
            // outcome stays visible) stays in view.
            setLeads(all.filter((lead) => lead.status !== 'dismissed'));
        } catch (loadError) {
            console.error(loadError);
            setError('Could not load tips. Check that the backend is running, then refresh.');
        } finally {
            setIsLoading(false);
        }
    }, []);

    useEffect(() => {
        loadLeads();
        window.addEventListener('policy-data-changed', loadLeads);

        return () => {
            window.removeEventListener('policy-data-changed', loadLeads);
        };
    }, [loadLeads]);

    const submitSuggestion = async (event) => {
        event.preventDefault();
        const url = suggestUrl.trim();
        const note = suggestNote.trim();
        if ((!url && !note) || isSuggesting) return;

        setIsSuggesting(true);
        setSuggestMessage('');

        try {
            const response = await fetch(apiUrl('/api/tips'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, note }),
            });

            if (response.status === 409) {
                setSuggestMessage('That tip has already been submitted.');
                return;
            }

            if (response.status === 429) {
                const errorBody = await response.json().catch(() => ({}));
                setSuggestMessage(errorBody.detail || 'Too many tips submitted - please try again shortly.');
                return;
            }

            if (!response.ok) {
                const errorBody = await response.json().catch(() => ({}));
                throw new Error(errorBody.detail || `Could not submit tip (${response.status})`);
            }

            setSuggestUrl('');
            setSuggestNote('');
            setSuggestMessage('Thanks! Your tip has been added to the queue.');
            loadLeads();
        } catch (submitError) {
            setSuggestMessage(submitError.message);
        } finally {
            setIsSuggesting(false);
        }
    };

    const chaseLead = async (leadId) => {
        setBusyLeadId(leadId);
        try {
            const response = await fetch(apiUrl(`/api/tips/${leadId}/chase`), {
                method: 'POST',
                headers: adminHeaders(),
            });
            if (!response.ok) {
                throw new Error(`Chase failed (${response.status})`);
            }
            // Reload rather than removing the card locally: a chase can
            // succeed (found/not-found) or fail-to-fetch, and either way the
            // outcome stays visible on the card instead of disappearing.
            await loadLeads();
            window.dispatchEvent(new Event('policy-data-changed'));
        } catch (chaseError) {
            console.error(chaseError);
            setError('Could not chase tip.');
        } finally {
            setBusyLeadId(null);
        }
    };

    const dismissLead = async (leadId) => {
        setBusyLeadId(leadId);
        try {
            const response = await fetch(apiUrl(`/api/tips/${leadId}/dismiss`), {
                method: 'POST',
                headers: adminHeaders(),
            });
            if (!response.ok) {
                throw new Error(`Dismiss failed (${response.status})`);
            }
            setLeads((current) => current.filter((lead) => lead.lead_id !== leadId));
        } catch (dismissError) {
            console.error(dismissError);
            setError('Could not dismiss tip.');
        } finally {
            setBusyLeadId(null);
        }
    };

    return (
        <section className="leads-inbox" aria-label="Tips inbox">
            <button
                type="button"
                className="leads-inbox-header"
                aria-expanded={isExpanded}
                onClick={() => setIsExpanded((current) => !current)}
            >
                <span>Tips (early signals)</span>
                <span className="leads-count-badge">{leads.length}</span>
            </button>

            {isExpanded && (
                <div className="leads-inbox-body">
                    <form className="leads-suggest-form" onSubmit={submitSuggestion}>
                        <input
                            type="url"
                            placeholder="Policy URL"
                            value={suggestUrl}
                            onChange={(event) => setSuggestUrl(event.target.value)}
                            className="leads-suggest-input"
                            aria-label="Policy URL"
                        />
                        <input
                            type="text"
                            placeholder="Note (optional if URL given)"
                            value={suggestNote}
                            onChange={(event) => setSuggestNote(event.target.value)}
                            className="leads-suggest-input"
                            aria-label="Note"
                        />
                        <button
                            type="submit"
                            className="button"
                            disabled={isSuggesting || (!suggestUrl.trim() && !suggestNote.trim())}
                        >
                            {isSuggesting ? 'Submitting...' : 'Suggest a tip'}
                        </button>
                    </form>
                    {suggestMessage && <p className="text-block-small">{suggestMessage}</p>}

                    {isLoading ? (
                        <p className="text-block-small">Loading tips...</p>
                    ) : error ? (
                        <p className="text-block-small">{error}</p>
                    ) : leads.length === 0 ? (
                        <p className="text-block-small">No new tips right now.</p>
                    ) : (
                        <ul className="leads-list">
                            {leads.map((lead) => {
                                const isNoteOnly = !lead.source_url;
                                return (
                                    <li key={lead.lead_id} className="leads-card">
                                        <div className="leads-card-header">
                                            {isNoteOnly ? (
                                                <span className="leads-card-title">
                                                    {cleanCardTitle(lead.title, lead.snippet)}
                                                </span>
                                            ) : (
                                                <a
                                                    href={lead.source_url}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    className="leads-card-title"
                                                >
                                                    {cleanCardTitle(lead.title, lead.source_url)}
                                                </a>
                                            )}
                                            <div className="leads-chips">
                                                {isNoteOnly && (
                                                    <span className="leads-chip leads-chip-hearsay">
                                                        Hearsay - needs a human
                                                    </span>
                                                )}
                                                {lead.jurisdiction_guess && (
                                                    <span className="leads-chip">{lead.jurisdiction_guess}</span>
                                                )}
                                                {lead.origin && (
                                                    <span className="leads-chip leads-chip-origin">{lead.origin}</span>
                                                )}
                                            </div>
                                        </div>
                                        {!isNoteOnly && lead.snippet && (
                                            <p className="leads-snippet">{cleanTipText(lead.snippet, '')}</p>
                                        )}
                                        {lead.status === 'chased' && (
                                            lead.policy_url ? (
                                                <p className="leads-outcome leads-outcome-found">
                                                    Found a policy:{' '}
                                                    <a href={lead.policy_url} target="_blank" rel="noopener noreferrer">
                                                        {lead.policy_url}
                                                    </a>
                                                    {lead.chased_at && ` (chased ${formatWhen(lead.chased_at)})`}
                                                </p>
                                            ) : (
                                                <p className="leads-outcome leads-outcome-none">
                                                    Checked - nothing found
                                                    {lead.chased_at && ` (on ${formatWhen(lead.chased_at)})`}
                                                </p>
                                            )
                                        )}
                                        {lead.chase_outcome === 'fetch_failed' && (
                                            <p className="leads-outcome leads-outcome-failed">
                                                Chase attempt failed
                                                {lead.chased_at && ` (on ${formatWhen(lead.chased_at)})`}:{' '}
                                                {lead.chase_error || 'unknown error'} - still chaseable.
                                            </p>
                                        )}
                                        <div className="leads-actions">
                                            {!isNoteOnly && (
                                                <Tooltip
                                                    title={isAdminLocked ? 'Administrator sign-in required' : ''}
                                                    disableHoverListener={!isAdminLocked}
                                                >
                                                    <span>
                                                        <button
                                                            type="button"
                                                            className="button"
                                                            disabled={isAdminLocked || busyLeadId === lead.lead_id}
                                                            onClick={() => chaseLead(lead.lead_id)}
                                                        >
                                                            {busyLeadId === lead.lead_id ? 'Chasing...' : 'Chase'}
                                                        </button>
                                                    </span>
                                                </Tooltip>
                                            )}
                                            <Tooltip
                                                title={isAdminLocked ? 'Administrator sign-in required' : ''}
                                                disableHoverListener={!isAdminLocked}
                                            >
                                                <span>
                                                    <button
                                                        type="button"
                                                        className="button leads-dismiss-button"
                                                        disabled={isAdminLocked || busyLeadId === lead.lead_id}
                                                        onClick={() => dismissLead(lead.lead_id)}
                                                    >
                                                        Dismiss
                                                    </button>
                                                </span>
                                            </Tooltip>
                                        </div>
                                    </li>
                                );
                            })}
                        </ul>
                    )}
                </div>
            )}
        </section>
    );
}

export default LeadsInbox;
