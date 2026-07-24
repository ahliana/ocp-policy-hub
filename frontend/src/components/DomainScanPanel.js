import React from 'react';
import Checkbox from '@mui/material/Checkbox';
import FormControlLabel from '@mui/material/FormControlLabel';
import FormGroup from '@mui/material/FormGroup';
import Tooltip from '@mui/material/Tooltip';
import { describeSelectionLabels, splitSelection } from '../utils/scanTargets';
import ModeSelector from './ModeSelector';
import RegionSelector from './RegionSelector';

// "news" is deliberately absent: news signals run on their own weekly
// schedule, not inside a scan — a checkbox here would silently do nothing.
const CHANNEL_OPTIONS = [
    { id: 'crawl', label: 'Government websites' },
    { id: 'law_apis', label: 'Law databases' },
    { id: 'transposition', label: 'EU transposition' },
];

function DomainScanPanel({
    selectedRegions,
    onSelectionChange,
    mode,
    onModeChange,
    channels,
    onChannelsChange,
    costStatus,
    costEstimateText,
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
    const handleChannelToggle = (channelId, checked) => {
        const nextChannels = checked
            ? [...channels, channelId]
            : channels.filter((id) => id !== channelId);
        onChannelsChange(nextChannels);
    };

    // What-you-launch-is-unambiguous summary, kept directly above the
    // Scan/Stop buttons. sourceCount is the cost estimate's domain_count
    // once that request is ready (the backend's actual resolved-domain
    // count - the single source of truth); while it's loading/idle/absent,
    // fall back to the number of scope entries currently selected so the
    // line still shows a number rather than "unknown".
    const selectionLabels = describeSelectionLabels(selectedRegions);
    const scopeText = selectionLabels.length > 0 ? selectionLabels.join(', ') : 'nothing selected';
    const resolvedSourceCount = sourceCount != null
        ? sourceCount
        : splitSelection(selectedRegions || []).targets.length;
    const sourceLabel = `${resolvedSourceCount} source${resolvedSourceCount === 1 ? '' : 's'}`;
    const scanScopeSummary = `Scanning: ${scopeText} - ${sourceLabel} - ${costEstimateText}`;

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
                />
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
            <p className="scan-scope-summary" aria-live="polite">{scanScopeSummary}</p>
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
            {!hasApiKey && (
                <p className="text-block-small">
                    Add an Anthropic API key in Settings to enable scanning.
                </p>
            )}
        </div>
    );
}

export default DomainScanPanel;
