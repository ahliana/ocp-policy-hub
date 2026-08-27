import React from 'react';
import HelpNote from './HelpNote';

// WP-31 - the whole gather-to-public-map journey, in one place, for anyone
// who wants the full picture rather than one panel's slice of it. Lives last
// in the admin area, after Schedules. Nothing here fetches - the stage copy
// is fixed text, and per the content spec no source count is hardcoded
// (omitted rather than guessed) since a live count isn't cheaply available
// to this component.
const STAGES = [
    {
        name: 'Gather.',
        sentence: 'PolicyPulse checks the watched sources: government websites it reads page '
            + 'by page, official law databases it queries directly, a weekly news sweep, and '
            + 'tips that visitors submit.',
        whereYouControlIt: 'the Sources list, and the Tips inbox.',
    },
    {
        name: 'Keyword screen.',
        sentence: 'Every gathered page is scored against weighted terms in each language; pages '
            + 'that score too low are set aside before any paid reading happens. Law database '
            + 'results skip this screen - they are already on topic.',
        whereYouControlIt: 'the Keywords panel.',
    },
    {
        name: 'Fast AI pass.',
        sentence: 'A quick reader confirms whether each remaining page is really about policy. '
            + 'Borderline cases are kept for a closer look rather than dropped.',
        whereYouControlIt: 'the cost level in the settings window.',
    },
    {
        name: 'Full AI read.',
        sentence: "A stronger reader extracts the policy's name, stage, requirements, and "
            + 'jurisdiction from every page that passes.',
        whereYouControlIt: null,
    },
    {
        name: 'Automatic checks.',
        sentence: 'Consistency checks flag anything odd about a find. Flags inform reviewers; '
            + 'they never delete anything on their own.',
        whereYouControlIt: null,
    },
    {
        name: 'Save and record.',
        sentence: 'Every find lands in the database and is copied to the staging sheet for '
            + 'curators. Costs and page counts are recorded so future estimates get more '
            + 'accurate.',
        whereYouControlIt: null,
    },
    {
        name: 'Human review.',
        sentence: 'A person promotes or rejects each find in the review queue or the Library. '
            + 'Rejecting hides a find from visitors without deleting it.',
        whereYouControlIt: 'the review queue, the Library, and the public visibility setting.',
    },
    {
        name: 'The public map.',
        sentence: 'Visitors browse what the visibility setting allows. Rejected finds never '
            + 'appear, in any setting.',
        whereYouControlIt: null,
    },
];

const DIAGRAM_ARIA_LABEL = 'A policy flows from gathering through the keyword screen, a fast '
    + 'AI pass, a full AI read, and human review before reaching the public map.';

// Six boxes, left to right, in the same visual language as CostFunnelDiagram
// (WP-38) - fills #eef2f7, strokes #d7dee8, text #0f172a - but its own fixed
// layout, since this diagram always shows the same six stages rather than
// numbers computed from a live estimate.
const DIAGRAM_BOXES = ['Gather', 'Keyword screen', 'Fast AI pass', 'Full AI read', 'Review', 'Public map'];
const BOX_WIDTH = 92;
const BOX_HEIGHT = 70;
const BOX_Y = 35;
const BOX_GAP = 10;
const BOX_X_POSITIONS = DIAGRAM_BOXES.map((_, index) => 19 + index * (BOX_WIDTH + BOX_GAP));

function HowItWorksDiagram() {
    return (
        <svg
            viewBox="0 0 640 140"
            role="img"
            aria-label={DIAGRAM_ARIA_LABEL}
            className="how-it-works-diagram"
        >
            <defs>
                <marker
                    id="how-it-works-arrowhead"
                    markerWidth="8"
                    markerHeight="8"
                    refX="6"
                    refY="4"
                    orient="auto"
                >
                    <path d="M0,0 L8,4 L0,8 Z" fill="#64748b" />
                </marker>
            </defs>
            {DIAGRAM_BOXES.map((label, index) => {
                const x = BOX_X_POSITIONS[index];
                const centerX = x + BOX_WIDTH / 2;
                return (
                    <g key={label}>
                        <rect
                            x={x}
                            y={BOX_Y}
                            width={BOX_WIDTH}
                            height={BOX_HEIGHT}
                            rx="10"
                            fill="#eef2f7"
                            stroke="#d7dee8"
                        />
                        <text
                            x={centerX}
                            y={BOX_Y + BOX_HEIGHT / 2 + 4}
                            textAnchor="middle"
                            fill="#0f172a"
                            fontSize="11"
                        >
                            {label}
                        </text>
                    </g>
                );
            })}
            {DIAGRAM_BOXES.slice(0, -1).map((label, index) => {
                const startX = BOX_X_POSITIONS[index] + BOX_WIDTH;
                const endX = BOX_X_POSITIONS[index + 1];
                return (
                    <line
                        key={`arrow-${label}`}
                        x1={startX + 1}
                        y1={BOX_Y + BOX_HEIGHT / 2}
                        x2={endX - 1}
                        y2={BOX_Y + BOX_HEIGHT / 2}
                        stroke="#64748b"
                        strokeWidth="2"
                        markerEnd="url(#how-it-works-arrowhead)"
                    />
                );
            })}
        </svg>
    );
}

function HowItWorksPanel() {
    return (
        <div className="how-it-works-panel" aria-label="How PolicyPulse works">
            <HelpNote
                label="How PolicyPulse works - from government website to the public map"
                className="how-it-works-note"
            >
                <p>
                    Here is the whole journey a policy takes through PolicyPulse, and where you
                    steer each step.
                </p>
                <ol className="how-it-works-stages">
                    {STAGES.map((stage) => (
                        <li key={stage.name}>
                            <strong>{stage.name}</strong> {stage.sentence}
                            {stage.whereYouControlIt && (
                                <p className="how-it-works-control">
                                    Where you control it: {stage.whereYouControlIt}
                                </p>
                            )}
                        </li>
                    ))}
                </ol>
                <p className="how-it-works-cost">
                    Only three steps spend money: the fast AI pass, the full AI read, and the
                    short report written at the end of a scan. Everything else is free. The
                    estimate on the scan panel prices exactly those steps before you run
                    anything.
                </p>
                <HelpNote label="See it as a picture" className="how-it-works-diagram-note">
                    <HowItWorksDiagram />
                </HelpNote>
            </HelpNote>
        </div>
    );
}

export default HowItWorksPanel;
