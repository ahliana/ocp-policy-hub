import React, { useEffect, useMemo, useState } from 'react';
import { apiUrl } from '../config/api';
import { adminHeaders } from '../utils/adminAuth';

async function fetchKeywords() {
    const response = await fetch(apiUrl('/api/keywords'), { headers: adminHeaders() });
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Keywords request failed (${response.status})`);
    }
    return response.json();
}

async function putOverrides(body) {
    const response = await fetch(apiUrl('/api/keywords/overrides'), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...adminHeaders() },
        body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const detail = Array.isArray(data.detail) ? data.detail.join('; ') : data.detail;
        throw new Error(detail || `Save failed (${response.status})`);
    }
    return data;
}

function withTerm(delta, term) {
    const added = delta.added.includes(term) ? delta.added : [...delta.added, term];
    return { ...delta, added };
}

function withoutAddedTerm(delta, term) {
    return { ...delta, added: delta.added.filter((t) => t !== term) };
}

function withRemovedTerm(delta, term) {
    const removed = delta.removed.includes(term) ? delta.removed : [...delta.removed, term];
    return { ...delta, removed };
}

function withoutRemovedTerm(delta, term) {
    return { ...delta, removed: delta.removed.filter((t) => t !== term) };
}

function emptyDelta() {
    return { added: [], removed: [] };
}

function getDelta(categories, category, language) {
    return categories[category]?.[language] || emptyDelta();
}

function setDelta(categories, category, language, delta) {
    return {
        ...categories,
        [category]: {
            ...categories[category],
            [language]: delta,
        },
    };
}

// Keywords panel (WP-10) - shows the merged (YAML + overlay) keyword config
// per category, lets an admin add/remove/restore terms and tweak
// thresholds, and saves via PUT /api/keywords/overrides. Lives in the admin
// area, below the Sources panel.
function KeywordsPanel() {
    const [categories, setCategories] = useState({});
    const [overrideCategories, setOverrideCategories] = useState({});
    // A pristine snapshot of the added-terms overlay as it was when last
    // fetched from the server. `categories[cat].terms[lang]` (the merged
    // view) already has these baked in, so this is what tells "yaml term"
    // apart from "custom term" - it must NOT change as the user edits
    // overrideCategories, or removing a just-added term would make it
    // reappear as a fake yaml term (the merged terms list is static
    // between fetches).
    const [baselineAdded, setBaselineAdded] = useState({});
    // Two separate threshold states: the merged/effective values (display
    // fallback) and the raw override delta (what Save sends). Sending the
    // merged values back would silently pin YAML's current thresholds as a
    // permanent override on every save - after which editing keywords.yaml
    // in git would have no effect. Only explicit edits enter the override.
    const [effectiveThresholds, setEffectiveThresholds] = useState({});
    const [thresholdOverrides, setThresholdOverrides] = useState({});
    const [loadError, setLoadError] = useState('');
    const [saveError, setSaveError] = useState('');
    const [saveStatus, setSaveStatus] = useState('');
    const [newTermCategory, setNewTermCategory] = useState('');
    const [newTermLanguage, setNewTermLanguage] = useState('en');
    const [newTerm, setNewTerm] = useState('');

    const load = () => fetchKeywords()
        .then((data) => {
            const overrideCats = data.overrides?.categories || {};
            setCategories(data.categories || {});
            setOverrideCategories(overrideCats);
            setBaselineAdded(overrideCats);
            setEffectiveThresholds(data.thresholds || {});
            setThresholdOverrides(data.overrides?.thresholds || {});
            const firstCategory = Object.keys(data.categories || {})[0] || '';
            setNewTermCategory((current) => current || firstCategory);
        })
        .catch((err) => setLoadError(err.message));

    useEffect(() => {
        load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const categoryNames = useMemo(() => Object.keys(categories), [categories]);

    const handleAddTerm = () => {
        const term = newTerm.trim();
        if (!newTermCategory || !newTermLanguage || !term) return;
        const delta = getDelta(overrideCategories, newTermCategory, newTermLanguage);
        setOverrideCategories((current) => setDelta(
            current, newTermCategory, newTermLanguage, withTerm(delta, term),
        ));
        setNewTerm('');
    };

    const handleRemoveYamlTerm = (category, language, term) => {
        const delta = getDelta(overrideCategories, category, language);
        setOverrideCategories((current) => setDelta(
            current, category, language, withRemovedTerm(delta, term),
        ));
    };

    const handleRestoreTerm = (category, language, term) => {
        const delta = getDelta(overrideCategories, category, language);
        setOverrideCategories((current) => setDelta(
            current, category, language, withoutRemovedTerm(delta, term),
        ));
    };

    const handleRemoveAddedTerm = (category, language, term) => {
        const delta = getDelta(overrideCategories, category, language);
        setOverrideCategories((current) => setDelta(
            current, category, language, withoutAddedTerm(delta, term),
        ));
    };

    const handleSave = async () => {
        setSaveError('');
        setSaveStatus('');
        try {
            await putOverrides({ categories: overrideCategories, thresholds: thresholdOverrides });
            setSaveStatus('Saved.');
            await load();
        } catch (err) {
            setSaveError(err.message);
        }
    };

    return (
        <div className="keywords-panel" aria-label="Keywords panel">
            <h2 className="panel-heading">Keywords - what counts as relevant</h2>
            {loadError && <p role="alert">{loadError}</p>}

            {categoryNames.map((category) => {
                const cfg = categories[category];
                const languages = Object.keys(cfg.terms || {});
                return (
                    <details key={category} className="keyword-category">
                        <summary>{`${category} (weight ${cfg.weight})`}</summary>
                        <p className="keyword-category-description">{cfg.description}</p>
                        {languages.map((language) => {
                            const delta = getDelta(overrideCategories, category, language);
                            const baselineAddedSet = new Set(getDelta(baselineAdded, category, language).added);
                            const removedSet = new Set(delta.removed);
                            const yamlTerms = (cfg.terms[language] || []).filter(
                                (t) => !baselineAddedSet.has(t),
                            );
                            return (
                                <div key={language} className="keyword-language-group">
                                    <h4>{language}</h4>
                                    <ul>
                                        {yamlTerms.map((term) => {
                                            const isRemoved = removedSet.has(term);
                                            return (
                                                <li key={term} className={isRemoved ? 'term-removed' : ''}>
                                                    <span className={isRemoved ? 'term-struck' : ''}>{term}</span>
                                                    {isRemoved ? (
                                                        <button
                                                            type="button"
                                                            onClick={() => handleRestoreTerm(category, language, term)}
                                                        >
                                                            Restore
                                                        </button>
                                                    ) : (
                                                        <button
                                                            type="button"
                                                            onClick={() => handleRemoveYamlTerm(category, language, term)}
                                                        >
                                                            Remove
                                                        </button>
                                                    )}
                                                </li>
                                            );
                                        })}
                                        {delta.added.map((term) => (
                                            <li key={`added-${term}`}>
                                                <span className="term-custom">{term}</span>
                                                <button
                                                    type="button"
                                                    onClick={() => handleRemoveAddedTerm(category, language, term)}
                                                >
                                                    Remove
                                                </button>
                                            </li>
                                        ))}
                                    </ul>
                                </div>
                            );
                        })}
                    </details>
                );
            })}

            <div className="keyword-add-term">
                <label htmlFor="keyword-add-category">Category</label>
                <select
                    id="keyword-add-category"
                    value={newTermCategory}
                    onChange={(event) => setNewTermCategory(event.target.value)}
                >
                    {categoryNames.map((category) => (
                        <option key={category} value={category}>{category}</option>
                    ))}
                </select>

                <label htmlFor="keyword-add-language">Language</label>
                <input
                    id="keyword-add-language"
                    type="text"
                    value={newTermLanguage}
                    onChange={(event) => setNewTermLanguage(event.target.value)}
                />

                <label htmlFor="keyword-add-term-input">New term</label>
                <input
                    id="keyword-add-term-input"
                    type="text"
                    value={newTerm}
                    onChange={(event) => setNewTerm(event.target.value)}
                />

                <button type="button" className="button" onClick={handleAddTerm}>
                    Add term
                </button>
            </div>

            <div className="keyword-thresholds">
                <label htmlFor="keyword-threshold-score">Minimum keyword score</label>
                <input
                    id="keyword-threshold-score"
                    type="number"
                    value={thresholdOverrides.minimum_keyword_score
                        ?? effectiveThresholds.minimum_keyword_score ?? ''}
                    onChange={(event) => setThresholdOverrides((current) => ({
                        ...current, minimum_keyword_score: Number(event.target.value),
                    }))}
                />

                <label htmlFor="keyword-threshold-matches">Minimum matches</label>
                <input
                    id="keyword-threshold-matches"
                    type="number"
                    value={thresholdOverrides.minimum_matches
                        ?? effectiveThresholds.minimum_matches ?? ''}
                    onChange={(event) => setThresholdOverrides((current) => ({
                        ...current, minimum_matches: Number(event.target.value),
                    }))}
                />

                {Object.keys(thresholdOverrides).length > 0 && (
                    <p className="keyword-thresholds-override-note" role="note">
                        Thresholds are overridden - YAML defaults no longer apply.
                        <button
                            type="button"
                            className="button-link"
                            onClick={() => setThresholdOverrides({})}
                        >
                            Reset to YAML defaults
                        </button>
                    </p>
                )}
            </div>

            {saveError && <p role="alert">{saveError}</p>}
            {saveStatus && <p role="status">{saveStatus}</p>}
            <button type="button" className="button" onClick={handleSave}>
                Save
            </button>

            <p className="keyword-calibration-note" role="note">
                Use the per-URL tester (Analyze, in the search panel above) to calibrate a term
                before saving it here.
            </p>
            <p className="keyword-scope-note" role="note">
                Changes apply to future scans - a scan already running is not affected.
            </p>
        </div>
    );
}

export default KeywordsPanel;
