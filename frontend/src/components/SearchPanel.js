import React, { useCallback, useEffect, useRef, useState } from 'react';
import Autocomplete from '@mui/material/Autocomplete';
import LinearProgress from '@mui/material/LinearProgress';
import TextField from '@mui/material/TextField';
import { apiUrl, WS_BASE_URL } from '../config/api';
import { adminHeaders } from '../utils/adminAuth';

// If no scan event arrives for this long, say so instead of going silent -
// a stall the UI explains is patience; a stall it hides is a bug report.
const STALL_AFTER_MS = 45000;

function formatElapsed(totalSeconds) {
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

const PLAN_DEBOUNCE_MS = 450;

const KIND_LABELS = {
    law_api: 'Law database',
    transposition: 'EU transposition',
    website: 'Government website',
};

function notifyPolicyDataChanged() {
    window.dispatchEvent(new Event('policy-data-changed'));
}

export function summarizePlanSources(sources) {
    const websites = sources.filter((s) => s.kind === 'website');
    const rest = sources.filter((s) => s.kind !== 'website');
    const parts = rest.map((s) => s.name);
    if (websites.length === 1) {
        parts.push(websites[0].name);
    } else if (websites.length > 1) {
        parts.push(`${websites.length} government websites`);
    }
    return parts.join(' · ');
}

function PlanPreview({ plan }) {
    if (!plan) return null;
    const { sources, estimate, warnings } = plan;
    const legiscan = estimate?.legiscan;

    return (
        <div className="search-plan" aria-label="Search plan">
            {sources.length > 0 && (
                <>
                    <p className="search-plan-line">
                        <strong>Will search:</strong> {summarizePlanSources(sources)}
                    </p>
                    <ul className="search-plan-sources">
                        {sources.filter((s) => s.kind !== 'website').map((s) => (
                            <li key={s.id}>
                                <span className="search-plan-kind">{KIND_LABELS[s.kind]}</span>
                                {s.description}
                            </li>
                        ))}
                    </ul>
                    <p className="search-plan-line">
                        <strong>Estimated cost:</strong>{' '}
                        up to ~${(estimate?.llm_ceiling_usd ?? 0).toFixed(2)} in AI analysis
                        {' '}(model cost level: {estimate?.cost_level}, set in API key settings)
                        {legiscan && (
                            <>
                                {' '}· uses at most {legiscan.max_queries} LegiScan queries
                                {' '}({legiscan.remaining.toLocaleString()} of{' '}
                                {legiscan.limit.toLocaleString()} left this month)
                            </>
                        )}
                    </p>
                    <p className="search-plan-line search-plan-advanced-hint">
                        Want a different scan mode (discover, deep) or hand-picked regions
                        and sources? Open &ldquo;Advanced&rdquo; below.
                    </p>
                </>
            )}
            {warnings.map((warning) => (
                <p key={warning} className="search-plan-warning" role="note">
                    {warning}
                </p>
            ))}
        </div>
    );
}

function SearchPanel({
    hasApiKey, isBusy, onBusyChange, adminRequired = false, externalPlace = null,
}) {
    const [place, setPlace] = useState('');
    const [topic, setTopic] = useState('');
    const [plan, setPlan] = useState(null);
    const [isPlanning, setIsPlanning] = useState(false);
    const [places, setPlaces] = useState([]);
    const [scan, setScan] = useState(null);
    const [elapsedSeconds, setElapsedSeconds] = useState(0);
    const [isStalled, setIsStalled] = useState(false);
    const wsRef = useRef(null);
    const planRequestRef = useRef(0);
    const lastEventAtRef = useRef(0);

    useEffect(() => {
        let cancelled = false;
        // Suggestions come from /api/search/places, whose every entry is
        // resolver-verified - never suggest an input the search will reject.
        fetch(apiUrl('/api/search/places'))
            .then((res) => (res.ok ? res.json() : { places: [] }))
            .then((data) => {
                if (!cancelled) setPlaces(data.places || []);
            })
            .catch(() => {});
        return () => { cancelled = true; };
    }, []);

    useEffect(() => () => wsRef.current?.close(), []);

    // The world map is a third way to say a place name - same resolve_place
    // flow as typing it here. A fresh nonce (even for the same place name)
    // is what makes a repeat click re-trigger this.
    useEffect(() => {
        if (externalPlace) setPlace(externalPlace.value);
    }, [externalPlace]);

    useEffect(() => {
        const trimmed = place.trim();
        if (trimmed.length < 2) {
            setPlan(null);
            return undefined;
        }

        const requestId = planRequestRef.current + 1;
        planRequestRef.current = requestId;
        setIsPlanning(true);

        const timer = setTimeout(async () => {
            try {
                const params = new URLSearchParams({ place: trimmed });
                if (topic.trim()) params.set('terms', topic.trim());
                const res = await fetch(apiUrl(`/api/search/plan?${params.toString()}`));
                if (!res.ok) throw new Error();
                const data = await res.json();
                if (planRequestRef.current === requestId) setPlan(data);
            } catch {
                if (planRequestRef.current === requestId) setPlan(null);
            } finally {
                if (planRequestRef.current === requestId) setIsPlanning(false);
            }
        }, PLAN_DEBOUNCE_MS);

        return () => clearTimeout(timer);
    }, [place, topic]);

    const runSearch = useCallback(async (event) => {
        event.preventDefault();
        if (!plan || !plan.targets || isBusy) return;

        onBusyChange?.(true);
        setScan({
            status: 'starting', found: 0, sourcesDone: 0, sourceErrors: 0,
            pagesChecked: 0, currentSource: null, names: [],
        });
        setElapsedSeconds(0);
        setIsStalled(false);
        lastEventAtRef.current = Date.now();

        try {
            const response = await fetch(apiUrl('/api/scans'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', ...adminHeaders() },
                body: JSON.stringify({
                    domains: plan.targets,
                    channels: plan.channels,
                    source_params: Object.keys(plan.source_params).length > 0
                        ? plan.source_params
                        : null,
                    max_concurrent: 5,
                }),
            });
            if (response.status === 401) {
                throw new Error(
                    'Searching needs an administrator sign-in. Open Settings and '
                    + 'enter the administrator token, then try again.',
                );
            }
            if (!response.ok) {
                const text = await response.text();
                throw new Error(text || `Search failed with ${response.status}`);
            }
            const job = await response.json();
            setScan((prev) => ({
                ...prev,
                status: 'running',
                scanId: job.scan_id,
                sourceCount: job.domain_count,
            }));

            const ws = new WebSocket(`${WS_BASE_URL}/api/scans/${job.scan_id}/ws`);
            wsRef.current = ws;
            ws.onmessage = (msg) => {
                let payload;
                try {
                    payload = JSON.parse(msg.data);
                } catch {
                    return;
                }
                lastEventAtRef.current = Date.now();
                setIsStalled(false);
                if (payload.type === 'domain_started') {
                    setScan((prev) => prev && ({
                        ...prev,
                        currentSource: payload.data?.domain_name || null,
                    }));
                } else if (payload.type === 'page_fetched') {
                    setScan((prev) => prev && ({
                        ...prev,
                        pagesChecked: (prev.pagesChecked || 0) + 1,
                    }));
                } else if (payload.type === 'policy_found') {
                    setScan((prev) => prev && ({
                        ...prev,
                        found: prev.found + 1,
                        names: [...prev.names, payload.data?.policy_name].slice(-5),
                    }));
                } else if (payload.type === 'domain_complete') {
                    setScan((prev) => prev && ({
                        ...prev,
                        sourcesDone: prev.sourcesDone + 1,
                    }));
                } else if (payload.type === 'scan_complete') {
                    setScan((prev) => prev && ({
                        ...prev,
                        status: 'complete',
                        found: payload.data?.total_policies ?? prev.found,
                    }));
                    notifyPolicyDataChanged();
                    onBusyChange?.(false);
                    ws.close();
                } else if (payload.type === 'error') {
                    // Per-source errors are routine (one website timing out);
                    // the scan continues, so the search card must too.
                    setScan((prev) => prev && ({
                        ...prev,
                        sourceErrors: (prev.sourceErrors || 0) + 1,
                        errorDetails: [
                            ...(prev.errorDetails || []),
                            {
                                source: payload.domain_id || 'scan',
                                message: String(payload.data?.error || 'unknown error').slice(0, 160),
                            },
                        ].slice(-8),
                    }));
                }
            };
            ws.onerror = () => {
                setScan((prev) => prev && ({ ...prev, status: 'error' }));
                onBusyChange?.(false);
            };
        } catch (error) {
            setScan({ status: 'error', message: error.message, found: 0 });
            onBusyChange?.(false);
        }
    }, [plan, isBusy, onBusyChange]);

    const stopSearch = useCallback(async () => {
        if (!scan?.scanId) return;
        try {
            await fetch(apiUrl(`/api/scans/${scan.scanId}`), {
                method: 'DELETE',
                headers: adminHeaders(),
            });
        } catch {
            // Best effort; the WS close event settles state either way.
        }
        wsRef.current?.close();
        setScan((prev) => prev && ({ ...prev, status: 'stopped' }));
        onBusyChange?.(false);
        notifyPolicyDataChanged();
    }, [scan, onBusyChange]);

    const canSearch = Boolean(
        plan && plan.targets && hasApiKey && !isBusy && scan?.status !== 'running',
    );
    const isRunning = scan?.status === 'starting' || scan?.status === 'running';

    useEffect(() => {
        if (!isRunning) return undefined;
        const timer = setInterval(() => {
            // The clock ticks every second regardless of scan events, so the
            // user always sees motion; the stall note kicks in when the scan
            // itself has been quiet too long.
            setElapsedSeconds((prev) => prev + 1);
            if (Date.now() - lastEventAtRef.current > STALL_AFTER_MS) {
                setIsStalled(true);
            }
        }, 1000);
        return () => clearInterval(timer);
    }, [isRunning]);

    // Don't scold mid-keystroke: while the text is still a prefix of a real
    // suggestion ("cal" on its way to "California"), hold the unknown-place
    // warning back instead of flashing "Could not recognize 'cal'".
    const typedLower = place.trim().toLowerCase();
    const typingTowardSuggestion = Boolean(
        plan
        && plan.place?.kind === 'unknown'
        && typedLower
        && places.some((name) => {
            const lower = name.toLowerCase();
            return lower.startsWith(typedLower) && lower !== typedLower;
        }),
    );
    const displayPlan = typingTowardSuggestion ? null : plan;

    return (
        <section className="search-panel" aria-label="Find new policies">
            <div className="settings-heading-panel">
                <h2 className="panel-heading">Find new policies</h2>
                <p className="text-block-small">
                    Pick a place; the right sources are chosen for you and you see the
                    cost before anything runs. This uses paid AI analysis.
                </p>
            </div>
            <form className="search-panel-form" onSubmit={runSearch}>
                <Autocomplete
                    freeSolo
                    options={places}
                    inputValue={place}
                    onInputChange={(event, value) => setPlace(value || '')}
                    disabled={isRunning}
                    autoHighlight
                    className="search-place-autocomplete"
                    renderInput={(params) => (
                        <TextField
                            {...params}
                            size="small"
                            label="Place to search"
                            placeholder='e.g. "California", "Sweden", "EU"'
                        />
                    )}
                />
                <label className="visually-hidden" htmlFor="search-topic-input">
                    Topic (optional)
                </label>
                <input
                    id="search-topic-input"
                    className="ask-box-input"
                    type="text"
                    placeholder="Topic (optional), e.g. thermal energy network"
                    value={topic}
                    onChange={(event) => setTopic(event.target.value)}
                    disabled={isRunning}
                />
                <button type="submit" className="ask-box-button" disabled={!canSearch}>
                    {isRunning ? 'Searching...' : 'Search'}
                </button>
            </form>
            {adminRequired && (
                <p className="search-admin-note" role="note">
                    Signed in as administrator - searches run on the project API key.
                </p>
            )}
            {isPlanning && (
                <p className="search-plan-status" role="status">Planning search...</p>
            )}
            {!isPlanning && <PlanPreview plan={displayPlan} />}
            {!hasApiKey && (
                <p className="text-block-small">
                    Add an Anthropic API key in Settings to enable searching.
                </p>
            )}
            {scan && (
                <div className="search-progress">
                    {/* Deliberately NOT a live region: the clock ticks every
                        second and would spam screen readers. Terminal states
                        below carry role="status" instead. */}
                    {isRunning && (
                        <>
                            <LinearProgress
                                variant={scan.sourceCount ? 'determinate' : 'indeterminate'}
                                value={scan.sourceCount
                                    ? Math.round((scan.sourcesDone / scan.sourceCount) * 100)
                                    : 0}
                                className="search-progress-bar"
                            />
                            <p className="search-progress-line">
                                {scan.sourcesDone} of {scan.sourceCount ?? '...'} sources done
                                {' '}· {scan.pagesChecked || 0} pages checked
                                {' '}· {scan.found} found
                                {scan.sourceErrors > 0
                                    && ` · ${scan.sourceErrors} ${scan.sourceErrors === 1 ? 'error' : 'errors'}`}
                                {' '}· {formatElapsed(elapsedSeconds)}
                                <button
                                    type="button"
                                    className="leads-dismiss-button search-stop-button"
                                    onClick={stopSearch}
                                >
                                    Stop
                                </button>
                            </p>
                            {scan.currentSource && (
                                <p className="search-progress-names">
                                    <span className="search-pulse-dot" aria-hidden="true" />
                                    Now checking: {scan.currentSource}
                                </p>
                            )}
                            {scan.names?.length > 0 && (
                                <p className="search-progress-names">
                                    Latest: {scan.names[scan.names.length - 1]}
                                </p>
                            )}
                            {isStalled && (
                                <p className="search-progress-names" role="status">
                                    Still working - some websites respond slowly.
                                </p>
                            )}
                        </>
                    )}
                    {scan.status === 'complete' && scan.found > 0 && (
                        <p className="search-progress-line search-progress-done" role="status">
                            Done: {scan.found} {scan.found === 1 ? 'policy' : 'policies'} found
                            and added to review. New rows are in the Staging sheet.
                            {scan.sourceErrors > 0
                                && ` (${scan.sourceErrors} of the sources had errors and were skipped.)`}
                        </p>
                    )}
                    {scan.status === 'complete' && scan.found === 0 && (
                        <div className="search-plan-warning" role="status">
                            <p>
                                Nothing new found. {plan ? `Searched ${plan.sources.length} sources
                                for ${plan.place.display}.` : ''} That usually means no new or
                                changed policies since the last search - already-known items are
                                skipped, not re-reported.
                            </p>
                            <p>
                                Try a broader place (a whole country or &quot;EU&quot;), a different
                                topic wording, or ask the admin assistant to investigate.
                            </p>
                        </div>
                    )}
                    {scan.status === 'stopped' && (
                        <p className="search-progress-line">Search stopped.</p>
                    )}
                    {scan.errorDetails?.length > 0 && (
                        <details className="search-error-details">
                            <summary>
                                {scan.sourceErrors} source
                                {scan.sourceErrors === 1 ? ' error' : ' errors'} - show details
                            </summary>
                            <ul>
                                {scan.errorDetails.map((detail, index) => (
                                    // eslint-disable-next-line react/no-array-index-key
                                    <li key={index}>
                                        <strong>{detail.source}</strong>: {detail.message}
                                    </li>
                                ))}
                            </ul>
                        </details>
                    )}
                    {scan.status === 'error' && (
                        <p className="ask-box-error" role="alert">
                            The search could not be completed.
                            {scan.message ? ` ${scan.message}` : ''}
                        </p>
                    )}
                </div>
            )}
        </section>
    );
}

export default SearchPanel;
