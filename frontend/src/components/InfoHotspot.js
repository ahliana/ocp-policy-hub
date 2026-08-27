import React, { useEffect, useRef, useState } from 'react';

// A toggletip, not a hover tooltip: the tip is reachable by keyboard (focus
// opens it) and stays open ("sticky") once opened by a click, so a user
// reading it can move the mouse away without it vanishing under them.
function InfoHotspot({ label, children }) {
    const [open, setOpen] = useState(false);
    const stickyRef = useRef(false);
    const containerRef = useRef(null);

    useEffect(() => {
        if (!open) return undefined;

        const handleKeyDown = (event) => {
            if (event.key === 'Escape') {
                stickyRef.current = false;
                setOpen(false);
            }
        };

        const handleOutsideClick = (event) => {
            if (containerRef.current && !containerRef.current.contains(event.target)) {
                stickyRef.current = false;
                setOpen(false);
            }
        };

        document.addEventListener('keydown', handleKeyDown);
        document.addEventListener('mousedown', handleOutsideClick);
        return () => {
            document.removeEventListener('keydown', handleKeyDown);
            document.removeEventListener('mousedown', handleOutsideClick);
        };
    }, [open]);

    // Click means "pin it or dismiss it", not blind toggle: in a real
    // browser a mouse user's click is always preceded by mouseenter (tip
    // already open, non-sticky), and a keyboard user's Enter by focus - a
    // toggle would close the tip at the exact moment they tried to pin it.
    const handleClick = () => {
        if (stickyRef.current) {
            stickyRef.current = false;
            setOpen(false);
        } else {
            stickyRef.current = true;
            setOpen(true);
        }
    };

    const handleMouseEnter = () => {
        if (!open) setOpen(true);
    };

    const handleMouseLeave = () => {
        if (open && !stickyRef.current) setOpen(false);
    };

    const handleFocus = () => {
        if (!open) setOpen(true);
    };

    const handleBlur = () => {
        if (open && !stickyRef.current) setOpen(false);
    };

    return (
        <span className="info-hotspot" ref={containerRef}>
            <button
                type="button"
                className="info-hotspot-trigger"
                aria-expanded={open}
                aria-label={label}
                onClick={handleClick}
                onMouseEnter={handleMouseEnter}
                onMouseLeave={handleMouseLeave}
                onFocus={handleFocus}
                onBlur={handleBlur}
            >
                ?
            </button>
            {open && (
                <span role="status" className="info-hotspot-tip">
                    {children}
                </span>
            )}
        </span>
    );
}

export default InfoHotspot;
