import React, { useCallback, useEffect, useState } from 'react';
import { apiUrl } from '../config/api';
import useAgentSocket from '../hooks/useAgentSocket';
import useCostEstimate from '../hooks/useCostEstimate';
import useScanQueue from '../hooks/useScanQueue';
import { DEFAULT_CHANNELS, buildScanRequests } from '../utils/scanTargets';
import AdminSignInDialog from './AdminSignInDialog';
import AgentChatPanel from './AgentChatPanel';
import ApiKeySettingsModal from './ApiKeySettingsModal';
import CostPlanner from './CostPlanner';
import DomainScanPanel from './DomainScanPanel';
import LibraryView from './LibraryView';
import PolicyScannerHeader from './PolicyScannerHeader';
import PublicVisibilityControl from './PublicVisibilityControl';
import ReviewInbox from './ReviewInbox';
import SearchPanel from './SearchPanel';
import WorldMap from './WorldMap';

function AgentPanel({
    adminRequired = false, hasAdminToken = false, onAdminTokenChange,
    onViewPlacePolicies, publicView = 'all', onPublicViewChange,
    showPublicViewToggle = false,
}) {
    const [selectedRegions, setSelectedRegions] = useState([]);
    const [mode, setMode] = useState('standard');
    const [channels, setChannels] = useState(DEFAULT_CHANNELS);
    const [chatNotice, setChatNotice] = useState(null);
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const [isAdminSignInOpen, setIsAdminSignInOpen] = useState(false);
    const [hasApiKey, setHasApiKey] = useState(false);
    const [isSearchBusy, setIsSearchBusy] = useState(false);
    const [placeRequest, setPlaceRequest] = useState(null);
    const [adminOpen, setAdminOpen] = useState(false);
    // Scanning and other admin tools are gated on a token only when the
    // server has ADMIN_TOKEN set; a local single-user deployment unlocks
    // immediately.
    const adminUnlocked = !adminRequired || hasAdminToken;
    const scanOptions = {
        discover: mode === 'discover',
        deep: mode === 'deep',
        channels,
    };

    const pushNotice = useCallback((type, text) => {
        setChatNotice({
            id: Date.now(),
            type,
            text,
        });
    }, []);

    const handleSelectPlace = useCallback((place) => {
        setPlaceRequest({ value: place, nonce: Date.now() });
    }, []);

    const { wsRef, isChatRunning, setIsChatRunning } = useAgentSocket({
        onNotice: pushNotice,
    });
    const { costStatus, costEstimateText, domainCount } = useCostEstimate({
        selectedRegions,
        mode,
    });
    const {
        isScanRequestRunning,
        isScanRunning,
        isQueueRunning,
        queuedScanCount,
        runScanQueue,
        stopActiveScan,
    } = useScanQueue({
        onNotice: pushNotice,
    });

    const isBusy = isChatRunning || isScanRunning || isScanRequestRunning
        || isQueueRunning || isSearchBusy;

    const fetchApiKeyStatus = useCallback(async () => {
        try {
            const res = await fetch(apiUrl('/api/settings/api-key'));
            if (!res.ok) throw new Error();
            const data = await res.json();
            setHasApiKey(data.exists);
        } catch {
            setHasApiKey(false);
        }
    }, []);

    const scanSelectedRegion = async () => {
        if (isBusy || selectedRegions.length === 0 || !hasApiKey) return;

        let requests;
        try {
            requests = await buildScanRequests(selectedRegions, scanOptions);
        } catch (error) {
            pushNotice('error', `Could not resolve selected domains: ${error.message}`);
            return;
        }

        if (requests.length > 1) {
            pushNotice('system', `Starting sequential scan queue for ${requests.length} targets.`);
        }

        await runScanQueue(requests);
    };

    useEffect(() => {
        fetchApiKeyStatus();
    }, [fetchApiKeyStatus]);

    const handleToggleAdmin = () => {
        setAdminOpen((prev) => {
            const next = !prev;
            // Locked and just opened: prompt for the admin passphrase via
            // its own dialog, kept separate from the Anthropic API key modal.
            if (next && !adminUnlocked) {
                setIsAdminSignInOpen(true);
            }
            return next;
        });
    };

    return (
        <section className="Policy-scanner" aria-label="Policy Scanner">
            <PolicyScannerHeader onToggleAdmin={handleToggleAdmin} adminOpen={adminOpen} />
            <WorldMap
                onSelectPlace={handleSelectPlace}
                onViewPlacePolicies={onViewPlacePolicies}
                showScanAction={adminOpen && adminUnlocked}
                publicView={publicView}
                onPublicViewChange={onPublicViewChange}
                showPublicViewToggle={showPublicViewToggle}
            />
            <ApiKeySettingsModal
                open={isSettingsOpen}
                onClose={() => {
                    setIsSettingsOpen(false);
                    fetchApiKeyStatus();
                }}
            />
            <AdminSignInDialog
                open={isAdminSignInOpen}
                onClose={() => setIsAdminSignInOpen(false)}
                onAdminTokenChange={onAdminTokenChange}
            />
            {adminOpen && (adminUnlocked ? (
                <div className="admin-area">
                    <p className="admin-mode-banner" role="status">
                        Administrator mode - actions here can spend money and change what the
                        public sees.
                    </p>
                    {!adminRequired && (
                        <p className="admin-open-mode-note" role="note">
                            Local open mode - set ADMIN_TOKEN for public deployments.
                        </p>
                    )}
                    <div className="admin-area-actions">
                        <button
                            type="button"
                            className="button"
                            onClick={() => setIsSettingsOpen(true)}
                        >
                            API key settings
                        </button>
                    </div>
                    <PublicVisibilityControl />
                    <SearchPanel
                        hasApiKey={hasApiKey}
                        isBusy={isBusy}
                        onBusyChange={setIsSearchBusy}
                        adminRequired={adminRequired}
                        externalPlace={placeRequest}
                    />
                    <details className="advanced-scan" open>
                        <summary className="advanced-scan-summary">
                            Advanced: pick individual regions and sources
                        </summary>
                        <DomainScanPanel
                            selectedRegions={selectedRegions}
                            onSelectionChange={(event, itemIds) => setSelectedRegions(itemIds)}
                            mode={mode}
                            onModeChange={setMode}
                            channels={channels}
                            onChannelsChange={setChannels}
                            costStatus={costStatus}
                            costEstimateText={costEstimateText}
                            sourceCount={domainCount}
                            isBusy={isBusy}
                            hasApiKey={hasApiKey}
                            isQueueRunning={isQueueRunning}
                            queuedScanCount={queuedScanCount}
                            isScanRequestRunning={isScanRequestRunning}
                            isScanRunning={isScanRunning}
                            onScan={scanSelectedRegion}
                            onStop={stopActiveScan}
                        />
                    </details>
                    <AgentChatPanel
                        wsRef={wsRef}
                        notice={chatNotice}
                        onRunningChange={setIsChatRunning}
                        isRunning={isChatRunning}
                    />
                    <ReviewInbox isAdmin={adminUnlocked} />
                    <LibraryView />
                    <CostPlanner />
                </div>
            ) : (
                <p className="admin-readonly-note" role="status">
                    This is a read-only view of the policy library. Click Admin again to sign in.
                </p>
            ))}
        </section>
    );
}

export default AgentPanel;
