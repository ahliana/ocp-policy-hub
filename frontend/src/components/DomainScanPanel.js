import React, { useState } from 'react';
import Checkbox from '@mui/material/Checkbox';
import FormControlLabel from '@mui/material/FormControlLabel';
import FormGroup from '@mui/material/FormGroup';
import Tooltip from '@mui/material/Tooltip';
import useScopePreview from '../hooks/useScopePreview';
import { describeSelectionLabels, splitSelection } from '../utils/scanTargets';
import CostFunnelDiagram from './CostFunnelDiagram';
import HelpNote from './HelpNote';
import InfoHotspot from './InfoHotspot';
import ModeSelector from './ModeSelector';
import RegionSelector from './RegionSelector';

// "news" is deliberately absent: news signals run on their own weekly
// schedule, not inside a scan - a checkbox here would silently do nothing.
const CHANNEL_OPTIONS = [
    { id: 'crawl', label: 'Government websites' },
    { id: 'law_apis', label: 'Law databases' },
    { id: 'transposition', label: 'EU transposition' },
];

// Plain-language copy for the "Why this price?" breakdown (WP-26) - one
// entry per channel the cost estimate can itemize. Keeps the tone rule
// (no "LLM"/"token"/"API") in one place rather than repeated per channel.
const CHANNEL_BREAKDOWN_COPY = {
    crawl: { noun: 'government websites', itemNoun: 'pages checked' },
    law_apis: { noun: 'law databases', itemNoun: 'entries checked' },
    transposition: { noun: 'EU law trackers', itemNoun: 'entries checked' },
};

// WP-30b - one-sentence hotspot tips for the three channel kinds, shared by
// the "Why this price?" breakdown lines below and the "Where will this
// search?" scope-preview group headings in useScopePreview's render.
const CHANNEL_HOTSPOT_TEXT = {
    crawl: 'Sites we read page by page, the way a visitor would.',
    law_apis: 'Official legal databases we query directly - faster and more precise than reading pages.',
    transposition: "Trackers for how EU directives become each member country's national law.",
};

function formatUsd(value) {
    return `$${Number(value || 0).toFixed(2)}`;
}

// Returns JSX (not a plain string, unlike its Phase-C shape) so the sentence
// can carry a trailing InfoHotspot - the sentence itself stays in its own
// <span> so it is still findable as one exact text node.
function channelBreakdownLine(channelId, channel) {
    const copy = CHANNEL_BREAKDOWN_COPY[channelId];
    if (!copy) return null;
    const sentence = `${channel.domain_count} ${copy.noun} - about ${channel.estimated_items_or_pages} `
        + `${copy.itemNoun}, ~${channel.screening_calls} get a fast AI pass, `
        + `~${channel.analysis_calls} get a full AI read - ${formatUsd(channel.cost_usd)} `
        + `(range ${formatUsd(channel.cost_low_usd)}-${formatUsd(channel.cost_high_usd)})`;
    return (
        <>
            <span>{sentence}</span>
            <InfoHotspot label={`More about ${copy.noun}`}>{CHANNEL_HOTSPOT_TEXT[channelId]}</InfoHotspot>
        </>
    );
}

// A click that lands on a HelpNote's summary while it is still closed is
// about to open it - this is the "open" signal WP-28's scope preview waits
// for before it fetches anything. (The native <details> "toggle" event would
// be the obvious hook, but it fires as an async, unlisenable-in-jsdom task in
// some environments, so this reads the pre-toggle DOM state on the click
// itself instead - reliable in both real browsers and the test suite.)
function isOpeningClick(event) {
    const summary = event.target.closest('.help-note-summary');
    if (!summary) return false;
    const details = summary.closest('details');
    return Boolean(details) && !details.open;
}

function DomainScanPanel({
    selectedRegions,
    onSelectionChange,
    mode,
    onModeChange,
    channels,
    onChannelsChange,
    costStatus,
    costEstimateText,
    costEstimate,
    standardEstimate,
    deepEstimate,
    sourceCount,
    isBusy,
    hasApiKey,
    isQueueRunning,
    queuedScanCount,
    isScanRequestRunning,
    isScanRunning,
    onScan,
    onStop,
}) {
    const [isScopePreviewActive, setIsScopePreviewActive] = useState(false);

    const handleChannelToggle = (channelId, checked) => {
        const nextChannels = checked
            ? [...channels, channelId]
            : channels.filter((id) => id !== channelId);
        onChannelsChange(nextChannels);
    };

    const handleScopePreviewAreaClick = (event) => {
        if (isOpeningClick(event)) setIsScopePreviewActive(true);
    };

    // What-you-launch-is-unambiguous summary, kept directly above the
    // Scan/Stop buttons. sourceCount is the cost estimate's domain_count
    // once that request is ready (the backend's actual resolved-domain
    // count - the single source of truth); while it's loading/idle/absent,
    // fall back to the number of scope entries currently selected so the
    // line still shows a number rather than "unknown".
    const scopeTargets = splitSelection(selectedRegions || []).targets;
    const hasScope = scopeTargets.length > 0;
    const selectionLabels = describeSelectionLabels(selectedRegions);
    const scopeText = selectionLabels.length > 0 ? selectionLabels.join(', ') : 'nothing selected';
    const resolvedSourceCount = sourceCount != null ? sourceCount : scopeTargets.length;
    const sourceLabel = `${resolvedSourceCount} source${resolvedSourceCount === 1 ? '' : 's'}`;
    const scanScopeSummary = `Scanning: ${scopeText} - ${sourceLabel} - ${costEstimateText}`;

    // "Why this price?" breakdown (WP-26) - only channels the backend
    // actually itemized are shown, in the same order as the checkboxes
    // above. Requires a ready estimate carrying a channels breakdown.
    const channelEntries = costStatus === 'ready' && costEstimate && costEstimate.channels
        ? CHANNEL_OPTIONS
            .map((option) => [option.id, costEstimate.channels[option.id]])
            .filter(([, channel]) => Boolean(channel))
        : [];
    const hasCostBreakdown = channelEntries.length > 0;

    // WP-28: "Where will this search?" - the resolved source list for the
    // current selection, fetched lazily (only once the note is opened).
    const scopePreview = useScopePreview({ selectedRegions, active: isScopePreviewActive });

    return (
        <div className="domain-scan" aria-label="Domain scan">
            <div>
                <div className="settings-heading-panel">
                    <div className="settings-heading-row">
                        <h2 className="panel-heading">Search Government Sources</h2>
                    </div>
                    <p className="text-block-small">Choose countries or regions to search for policies.</p>
                </div>

                <div className="region-selector-scroll">
                    <RegionSelector
                        selectedItems={selectedRegions}
                        onSelectionChange={onSelectionChange}
                    />
                </div>
                <ModeSelector
                    value={mode}
                    onChange={onModeChange}
                    hasScope={hasScope}
                    standardEstimate={standardEstimate}
                    deepEstimate={deepEstimate}
                />
                <HelpNote label="Which depth should I pick?" className="mode-help-note">
                    <p>
                        Standard is right for routine checking - it visits the government sites
                        already on the watch list and spots new or changed policies. Discover casts
                        a wider net: it searches the web for government sites we don&apos;t watch yet
                        and adds what it finds, so use it when coverage of a place feels thin. Deep
                        rereads every page of the watched sites more thoroughly and costs several
                        times more - save it for when you suspect something was missed. The price on
                        each card updates as you change your selection.
                    </p>
                </HelpNote>
                <div className="channels-group" role="group" aria-label="Sources to check">
                    <p className="text-block-small channels-heading">Sources to check</p>
                    <FormGroup row>
                        {CHANNEL_OPTIONS.map((option) => (
                            <FormControlLabel
                                key={option.id}
                                control={
                                    <Checkbox
                                        size="small"
                                        checked={channels.includes(option.id)}
                                        onChange={(event) => handleChannelToggle(option.id, event.target.checked)}
                                    />
                                }
                                label={option.label}
                            />
                        ))}
                    </FormGroup>
                    <p className="text-block-small">
                        Law databases and transposition checks are free data sources; website crawling is
                        the main driver of scan cost.
                    </p>
                </div>
                <Tooltip title="Please note that this is only an estimate and may not reflect the actual cost" placement="top" arrow>
                    <output className={`cost-estimate ${costStatus}`} aria-live="polite">
                        {costEstimateText}
                    </output>
                </Tooltip>
            </div>
            <div className="scan-decision">
                <p className="scan-scope-summary" aria-live="polite">{scanScopeSummary}</p>
                {hasCostBreakdown && (
                    <HelpNote label="Why this price?" className="cost-breakdown">
                        <ul className="cost-breakdown-channels">
                            {channelEntries.map(([channelId, channel]) => (
                                <li key={channelId}>{channelBreakdownLine(channelId, channel)}</li>
                            ))}
                        </ul>
                        <p className="cost-breakdown-auditor">
                            Report generation: {formatUsd(costEstimate.auditor_cost_usd)}
                        </p>
                        {Array.isArray(costEstimate.assumptions) && costEstimate.assumptions.length > 0 && (
                            <div className="cost-breakdown-assumptions">
                                <p className="cost-breakdown-assumptions-heading">What we assumed</p>
                                <ul>
                                    {costEstimate.assumptions.map((assumption) => (
                                        <li key={assumption}>{assumption}</li>
                                    ))}
                                </ul>
                            </div>
                        )}
                        <HelpNote label="See it as a picture" className="cost-funnel-note">
                            <CostFunnelDiagram estimate={costEstimate} />
                        </HelpNote>
                    </HelpNote>
                )}
                {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions, jsx-a11y/click-events-have-key-events */}
                <div className="scope-preview-area" onClick={handleScopePreviewAreaClick}>
                    <HelpNote label="Where will this search?" className="scope-preview">
                        {scopePreview.status === 'empty' && (
                            <p>Pick a place or sources first.</p>
                        )}
                        {scopePreview.status === 'loading' && (
                            <p>Loading sources...</p>
                        )}
                        {scopePreview.status === 'error' && (
                            <p>Could not load the source list.</p>
                        )}
                        {scopePreview.status === 'ready' && (
                            <>
                                {scopePreview.groups.map((group) => (
                                    <div key={group.id} className="scope-preview-group">
                                        <p className="scope-preview-group-heading">
                                            <span>{group.label} ({group.entries.length})</span>
                                            <InfoHotspot label={`More about ${group.label}`}>
                                                {CHANNEL_HOTSPOT_TEXT[group.id]}
                                            </InfoHotspot>
                                        </p>
                                        <ul>
                                            {group.entries.map((entry) => (
                                                <li key={entry.id}>{entry.name} - {entry.region}</li>
                                            ))}
                                        </ul>
                                    </div>
                                ))}
                                <p className="scope-preview-total">
                                    {scopePreview.totalCount} source{scopePreview.totalCount === 1 ? '' : 's'} total
                                </p>
                            </>
                        )}
                    </HelpNote>
                </div>
                <div className="agent-action-row">
                    <button
                        type="button"
                        className="scan-button button"
                        onClick={onScan}
                        disabled={isBusy || selectedRegions.length === 0 || !hasApiKey}
                    >
                        {isQueueRunning
                            ? `Queued (${queuedScanCount})`
                            : isScanRequestRunning || isScanRunning ? 'Scan running' : 'Scan'}
                    </button>
                    <button
                        type="button"
                        className="stop-scan-button button"
                        onClick={onStop}
                        disabled={!isScanRunning && !isQueueRunning && !isScanRequestRunning}
                    >
                        Stop scan
                    </button>
                </div>
            </div>
            {!hasApiKey && (
                <p className="text-block-small">
                    Add an Anthropic API key in Settings to enable scanning.
                </p>
            )}
        </div>
    );
}

export default DomainScanPanel;
