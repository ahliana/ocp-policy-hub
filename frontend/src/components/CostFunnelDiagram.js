import React from 'react';

// WP-38: the same funnel the "Why this price?" text already describes in
// words, drawn as a picture. Every number here comes straight from the one
// cost-estimate object the text lines above already use - this never fetches
// or computes anything of its own, so the two can never disagree.
const STAGE_BOX_WIDTH = 130;
const STAGE_BOX_HEIGHT = 70;
const STAGE_BOX_Y = 35;
const STAGE_X_POSITIONS = [15, 175, 335, 495];

function buildStages(estimate) {
    return [
        { label: 'Sources', value: `${estimate.domain_count ?? 0}` },
        { label: 'Pages checked', value: `~${estimate.estimated_pages ?? 0}` },
        { label: 'Fast AI pass', value: `~${estimate.estimated_screening_calls ?? 0}` },
        { label: 'Full AI read', value: `~${estimate.estimated_analysis_calls ?? 0}` },
    ];
}

function buildAriaLabel(estimate) {
    return `A flow from ${estimate.domain_count ?? 0} sources, `
        + `to ~${estimate.estimated_pages ?? 0} pages checked, `
        + `to ~${estimate.estimated_screening_calls ?? 0} fast AI passes, `
        + `to ~${estimate.estimated_analysis_calls ?? 0} full AI reads.`;
}

function CostFunnelDiagram({ estimate }) {
    if (!estimate) return null;

    const stages = buildStages(estimate);

    return (
        <svg
            viewBox="0 0 640 140"
            role="img"
            aria-label={buildAriaLabel(estimate)}
            className="cost-funnel-diagram"
        >
            <defs>
                <marker
                    id="cost-funnel-arrowhead"
                    markerWidth="8"
                    markerHeight="8"
                    refX="6"
                    refY="4"
                    orient="auto"
                >
                    <path d="M0,0 L8,4 L0,8 Z" fill="#64748b" />
                </marker>
            </defs>
            {stages.map((stage, index) => {
                const x = STAGE_X_POSITIONS[index];
                const centerX = x + STAGE_BOX_WIDTH / 2;
                return (
                    <g key={stage.label}>
                        <rect
                            x={x}
                            y={STAGE_BOX_Y}
                            width={STAGE_BOX_WIDTH}
                            height={STAGE_BOX_HEIGHT}
                            rx="10"
                            fill="#eef2f7"
                            stroke="#d7dee8"
                        />
                        <text
                            x={centerX}
                            y={STAGE_BOX_Y + 26}
                            textAnchor="middle"
                            fill="#0f172a"
                            fontSize="12"
                        >
                            {stage.label}
                        </text>
                        <text
                            x={centerX}
                            y={STAGE_BOX_Y + 50}
                            textAnchor="middle"
                            fill="#0f172a"
                            fontSize="18"
                            fontWeight="bold"
                        >
                            {stage.value}
                        </text>
                    </g>
                );
            })}
            {[0, 1, 2].map((index) => {
                const startX = STAGE_X_POSITIONS[index] + STAGE_BOX_WIDTH;
                const endX = STAGE_X_POSITIONS[index + 1];
                return (
                    <line
                        // eslint-disable-next-line react/no-array-index-key
                        key={`arrow-${index}`}
                        x1={startX + 2}
                        y1={STAGE_BOX_Y + STAGE_BOX_HEIGHT / 2}
                        x2={endX - 10}
                        y2={STAGE_BOX_Y + STAGE_BOX_HEIGHT / 2}
                        stroke="#64748b"
                        strokeWidth="2"
                        markerEnd="url(#cost-funnel-arrowhead)"
                    />
                );
            })}
        </svg>
    );
}

export default CostFunnelDiagram;
