import React from 'react';

// A native <details>/<summary> disclosure, styled to read as an inline help
// note rather than a generic expander. Native semantics mean keyboard
// support (Enter/Space on the summary, focus order) comes for free - no
// custom key handling needed, unlike InfoHotspot's toggletip.
function HelpNote({
    label, children, className = '', defaultOpen = false,
}) {
    return (
        <details
            className={['help-note', className].filter(Boolean).join(' ')}
            open={defaultOpen}
        >
            <summary className="help-note-summary">{label}</summary>
            <div className="help-note-body">{children}</div>
        </details>
    );
}

export default HelpNote;
