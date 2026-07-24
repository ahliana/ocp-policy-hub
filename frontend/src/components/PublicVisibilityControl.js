import React, { useEffect, useState } from 'react';
import { apiUrl } from '../config/api';
import { adminHeaders } from '../utils/adminAuth';

const OPTIONS = [
    {
        value: 'default_all',
        label: 'Show all finds by default',
        hint: 'Visitors see everything except rejected finds; they can still switch to reviewed-only.',
    },
    {
        value: 'default_reviewed',
        label: 'Show reviewed only by default, visitors can switch',
        hint: 'Visitors default to reviewed-only, with a switch to see all finds.',
    },
    {
        value: 'reviewed_only',
        label: 'Reviewed only - hide the switch from visitors',
        hint: 'Visitors always see reviewed-only finds; no switch is shown.',
    },
];

// Admin control for the WP-3 "public review visibility" posture — mirrors
// the cost-controls settings panel's load/save/confirm shape.
function PublicVisibilityControl() {
    const [mode, setMode] = useState(null);
    const [isSaving, setIsSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        let cancelled = false;
        fetch(apiUrl('/api/settings/public-visibility'), { headers: adminHeaders() })
            .then((res) => (res.ok ? res.json() : Promise.reject()))
            .then((data) => { if (!cancelled) setMode(data.mode); })
            .catch(() => { if (!cancelled) setError('Could not load the visibility setting.'); });
        return () => { cancelled = true; };
    }, []);

    const handleChange = async (value) => {
        setIsSaving(true);
        setSaved(false);
        setError('');
        try {
            const response = await fetch(apiUrl('/api/settings/public-visibility'), {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', ...adminHeaders() },
                body: JSON.stringify({ mode: value }),
            });
            if (!response.ok) throw new Error();
            const data = await response.json();
            setMode(data.mode);
            setSaved(true);
        } catch {
            setError('Could not save the visibility setting.');
        } finally {
            setIsSaving(false);
        }
    };

    if (mode === null) return null;

    return (
        <div className="public-visibility-control" role="radiogroup" aria-label="Public visibility">
            <h3 className="public-visibility-title">Public visibility</h3>
            {OPTIONS.map((option) => (
                <label key={option.value} className="public-visibility-option">
                    <input
                        type="radio"
                        name="public-visibility"
                        value={option.value}
                        checked={mode === option.value}
                        disabled={isSaving}
                        onChange={() => handleChange(option.value)}
                    />
                    <span>
                        <span className="public-visibility-option-label">{option.label}</span>
                        <span className="public-visibility-option-hint">{option.hint}</span>
                    </span>
                </label>
            ))}
            {saved && <p className="public-visibility-saved" role="status">Saved.</p>}
            {error && <p className="ask-box-error" role="alert">{error}</p>}
        </div>
    );
}

export default PublicVisibilityControl;
