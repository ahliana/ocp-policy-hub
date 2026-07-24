import React, { useId, useState } from 'react';
import './SavedPolicy.css';

const TAG_GROUPS = {
    regulatory: {
        color: '#7c2d12',
        background: '#ffedd5',
        border: '#fed7aa',
        keywords: ['mandate', 'mandatory', 'reporting', 'deadline', 'registry', 'framework', 'article', 'eed'],
    },
    energy: {
        color: '#166534',
        background: '#dcfce7',
        border: '#bbf7d0',
        keywords: ['efficiency', 'energy', 'pue', 'renewable'],
    },
    heat: {
        color: '#075985',
        background: '#e0f2fe',
        border: '#bae6fd',
        keywords: ['heat', 'district'],
    },
    climate: {
        color: '#365314',
        background: '#ecfccb',
        border: '#d9f99d',
        keywords: ['carbon', 'zero'],
    },
    incentives: {
        color: '#854d0e',
        background: '#fef3c7',
        border: '#fde68a',
        keywords: ['incentive', 'grant', 'subsid', 'tax'],
    },
    research: {
        color: '#6b21a8',
        background: '#f3e8ff',
        border: '#e9d5ff',
        keywords: ['research', 'study', 'studies', 'data'],
    },
    planning: {
        color: '#3730a3',
        background: '#e0e7ff',
        border: '#c7d2fe',
        keywords: ['planning', 'zoning', 'permit', 'infrastructure', 'strategy'],
    },
    default: {
        color: '#374151',
        background: '#e5e7eb',
        border: '#d1d5db',
        keywords: [],
    },
};

function getTagGroup(tag, description = '') {
    const text = `${tag} ${description}`.toLowerCase();
    return Object.entries(TAG_GROUPS).find(([, group]) =>
        group.keywords.some((keyword) => text.includes(keyword))
    )?.[0] || 'default';
}

export function formatTagLabel(tag) {
    return tag.replaceAll('_', ' ');
}

const LIFECYCLE_LABELS = {
    proposed: 'Proposed',
    consultation: 'Consultation',
    in_committee: 'In Committee',
    passed: 'Passed',
    transposition_notified: 'Transposition Notified',
    enacted: 'Enacted',
    amended: 'Amended',
};

const UPCOMING_LIFECYCLE_STAGES = new Set([
    'proposed', 'consultation', 'in_committee', 'passed', 'transposition_notified',
]);

// Friendly reader-facing text for the review workflow's internal statuses —
// "new"/"promoted" read as jargon to a visitor who isn't part of the review
// process (see WP-3 "public review visibility").
const REVIEW_STATUS_LABELS = {
    new: 'awaiting review',
    reviewed: 'reviewed',
    promoted: 'reviewed',
    rejected: 'rejected',
};

function formatReviewStatusLabel(status) {
    return REVIEW_STATUS_LABELS[status] || status;
}

function getLifecycleBadgeStyle(stage) {
    if (UPCOMING_LIFECYCLE_STAGES.has(stage)) {
        return { color: '#92400e', backgroundColor: '#fef3c7', borderColor: '#fde68a' };
    }
    return { color: '#166534', backgroundColor: '#dcfce7', borderColor: '#bbf7d0' };
}

function getTagBadgeStyle(tag, description) {
    const group = TAG_GROUPS[getTagGroup(tag, description)];

    return {
        color: group.color,
        backgroundColor: group.background,
        borderColor: group.border,
    };
}

export function getPolicyTags(policy, tags) {
    const explicitTags = Array.isArray(policy.tags) ? policy.tags : [];
    const searchableText = [
        policy.policy_name,
        policy.policy_type,
        policy.summary,
        policy.key_requirements,
        ...(policy.referenced_policies || []),
    ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();

    const inferredTags = Object.entries(tags)
        .filter(([tag, description]) => {
            const tagText = tag.replaceAll('_', ' ').toLowerCase();
            const descriptionWords = String(description)
                .toLowerCase()
                .split(/[^a-z0-9]+/)
                .filter((word) => word.length >= 5);

            return (
                searchableText.includes(tagText) ||
                descriptionWords.some((word) => searchableText.includes(word))
            );
        })
        .map(([tag]) => tag);

    return [...new Set([...explicitTags, ...inferredTags])].slice(0, 8);
}

function SavedPolicy({ policy, tags = {} }) {
    const [isExpanded, setIsExpanded] = useState(false);
    const detailsId = useId();

    if (!policy) {
        return <div className="saved-policy-empty">No policy data available</div>;
    }

    const policyTags = getPolicyTags(policy, tags);

    const getReviewStatusBadge = (status) => {
        const statusMap = {
            new: 'badge-new',
            needs_review: 'badge-review',
            verified: 'badge-verified',
            archived: 'badge-archived',
        };
        return statusMap[status] || 'badge-default';
    };

    const getPolicyTypeBadge = (type) => {
        const typeMap = {
            law: 'type-law',
            directive: 'type-directive',
            regulation: 'type-regulation',
            incentive: 'type-incentive',
        };
        return typeMap[type] || 'type-default';
    };

    const formatDate = (dateString) => {
        if (!dateString) return 'Not specified';
        const date = new Date(dateString);
        if (Number.isNaN(date.getTime())) return 'Not specified';
        return new Intl.DateTimeFormat(navigator.language, { dateStyle: 'medium' }).format(date);
    };

    return (
        <div className="saved-policy-card">
            <button
                type="button"
                className="saved-policy-header"
                aria-expanded={isExpanded}
                aria-controls={detailsId}
                onClick={() => setIsExpanded((current) => !current)}
            >
                <span className="saved-policy-title-section">
                    <span className="saved-policy-name">{policy.policy_name}</span>
                    <span className="saved-policy-meta">
                        <span className="saved-policy-jurisdiction">{policy.jurisdiction}</span>
                        <span className={`saved-policy-badge ${getPolicyTypeBadge(policy.policy_type)}`}>
                            {policy.policy_type}
                        </span>
                        <span className={`saved-policy-badge ${getReviewStatusBadge(policy.review_status)}`}>
                            {formatReviewStatusLabel(policy.review_status)}
                        </span>
                        {policy.lifecycle_stage && policy.lifecycle_stage !== 'unknown' && (
                            <span
                                className="policy-tag-badge lifecycle-badge"
                                style={getLifecycleBadgeStyle(policy.lifecycle_stage)}
                            >
                                {LIFECYCLE_LABELS[policy.lifecycle_stage] || formatTagLabel(policy.lifecycle_stage)}
                            </span>
                        )}
                    </span>
                    {policyTags.length > 0 && (
                        <span className="saved-policy-tags" aria-label="Policy tags">
                            {policyTags.map((tag) => (
                                <span
                                    key={tag}
                                    className="policy-tag-badge"
                                    style={getTagBadgeStyle(tag, tags[tag])}
                                    title={tags[tag] || tag}
                                >
                                    {formatTagLabel(tag)}
                                </span>
                            ))}
                        </span>
                    )}
                </span>
                <span className="saved-policy-score">
                    <span className="relevance-score">{policy.relevance_score}</span>
                    <span className="score-label">relevance</span>
                </span>
                <span className="expand-button" aria-hidden="true">
                    {isExpanded ? '-' : '+'}
                </span>
            </button>

            <div className="saved-policy-summary">
                <p>{policy.summary}</p>
            </div>

            {isExpanded && (
                <div className="saved-policy-details" id={detailsId}>
                    <div className="detail-section" id="key-info">
                        <h4>Key Information</h4>
                        <dl className="detail-list">
                            {policy.bill_number && (
                                <>
                                    <dt>Bill Number:</dt>
                                    <dd>{policy.bill_number}</dd>
                                </>
                            )}
                            {policy.effective_date && (
                                <>
                                    <dt>Effective Date:</dt>
                                    <dd>{formatDate(policy.effective_date)}</dd>
                                </>
                            )}
                            {policy.source_language && (
                                <>
                                    <dt>Source Language:</dt>
                                    <dd>{policy.source_language}</dd>
                                </>
                            )}
                            {policy.discovered_at && (
                                <>
                                    <dt>Discovered:</dt>
                                    <dd>{formatDate(policy.discovered_at)}</dd>
                                </>
                            )}
                        </dl>
                    </div>

                    {policy.key_requirements && (
                        <div className="detail-section" id="key-requirements">
                            <h4>Key Requirements</h4>
                            <p className="requirements-text">{policy.key_requirements}</p>
                        </div>
                    )}

                    {policy.referenced_policies && policy.referenced_policies.length > 0 && (
                        <div className="detail-section" id="referenced-policies">
                            <h4>Referenced Policies</h4>
                            <ul className="referenced-list">
                                {policy.referenced_policies.map((ref, index) => (
                                    <li key={index}>{ref}</li>
                                ))}
                            </ul>
                        </div>
                    )}

                    {policy.referenced_urls && policy.referenced_urls.length > 0 && (
                        <div className="detail-section" id="referenced-urls">
                            <h4>Referenced URLs</h4>
                            <ul className="referenced-urls-list">
                                {policy.referenced_urls.map((url, index) => (
                                    <li key={index}>
                                        <a href={url} target="_blank" rel="noopener noreferrer">
                                            {url}
                                        </a>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}

                    {policy.verification_flags && policy.verification_flags.length > 0 && (
                        <div className="detail-section verification-flags">
                            <h4>Verification Flags</h4>
                            <div className="flags-container">
                                {policy.verification_flags.map((flag, index) => (
                                    <span key={index} className="flag-badge">
                                        {flag}
                                    </span>
                                ))}
                            </div>
                        </div>
                    )}

                    <div className="detail-section meta-info">
                        <dl className="detail-list">
                            <dt>Scan ID:</dt>
                            <dd className="monospace">{policy.scan_id}</dd>
                            <dt>Domain ID:</dt>
                            <dd className="monospace">{policy.domain_id}</dd>
                            <dt>Crawl Status:</dt>
                            <dd>{policy.crawl_status}</dd>
                            {policy.error_details && (
                                <>
                                    <dt>Error Details:</dt>
                                    <dd>{policy.error_details}</dd>
                                </>
                            )}
                        </dl>
                    </div>

                    <div className="detail-section actions">
                        <a
                            href={policy.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="action-link primary"
                        >
                            View Full Policy
                        </a>
                    </div>
                </div>
            )}
        </div>
    );
}

export default SavedPolicy;
