import React from 'react';

function PolicyScannerHeader({ onToggleAdmin, adminOpen = false }) {
    return (
        <div className="policy-scanner-heading-row">
            <div>
                <h2 className="panel-heading">Policy Scanner</h2>
                <p className="text-block-small">
                    Explore the map, then ask about the policies already found
                </p>
            </div>
            <button
                type="button"
                className="button admin-toggle-button"
                onClick={onToggleAdmin}
                aria-expanded={adminOpen}
            >
                {adminOpen ? 'Exit admin' : 'Admin'}
            </button>
        </div>
    );
}

export default PolicyScannerHeader;
