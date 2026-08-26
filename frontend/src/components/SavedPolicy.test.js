import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import SavedPolicy from './SavedPolicy';

function policy(overrides = {}) {
    return {
        policy_name: 'Heat Reuse Act',
        jurisdiction: 'Sweden',
        policy_type: 'law',
        relevance_score: 7,
        summary: 'A summary',
        review_status: 'new',
        scan_id: 'scan-12345',
        domain_id: 'de-federal-heat',
        crawl_status: 'success',
        ...overrides,
    };
}

function expandCard() {
    fireEvent.click(screen.getByRole('button', { name: /Heat Reuse Act/ }));
}

describe('SavedPolicy review status badge', () => {
    it('shows "new" as a friendly awaiting-review label', () => {
        render(<SavedPolicy policy={policy({ review_status: 'new' })} tags={{}} />);
        expect(screen.getByText(/awaiting review/i)).toBeInTheDocument();
        expect(screen.queryByText('new')).not.toBeInTheDocument();
    });

    it('shows "promoted" as a friendly reviewed label', () => {
        render(<SavedPolicy policy={policy({ review_status: 'promoted' })} tags={{}} />);
        expect(screen.getByText(/reviewed/i)).toBeInTheDocument();
        expect(screen.queryByText('promoted')).not.toBeInTheDocument();
    });

    it('shows "reviewed" as-is', () => {
        render(<SavedPolicy policy={policy({ review_status: 'reviewed' })} tags={{}} />);
        expect(screen.getByText(/reviewed/i)).toBeInTheDocument();
    });

    it('falls back to the raw status for an unrecognized value', () => {
        render(<SavedPolicy policy={policy({ review_status: 'archived' })} tags={{}} />);
        expect(screen.getByText('archived')).toBeInTheDocument();
    });
});

describe('SavedPolicy pipeline meta (Scan ID / Domain ID / Crawl Status)', () => {
    it('hides pipeline internals from a public (non-admin) viewer', () => {
        render(<SavedPolicy policy={policy()} tags={{}} />);
        expandCard();

        expect(screen.queryByText('Scan ID:')).not.toBeInTheDocument();
        expect(screen.queryByText('Domain ID:')).not.toBeInTheDocument();
        expect(screen.queryByText('Crawl Status:')).not.toBeInTheDocument();
        expect(screen.queryByText('scan-12345')).not.toBeInTheDocument();
    });

    it('shows pipeline internals to an admin viewer', () => {
        render(<SavedPolicy policy={policy()} tags={{}} isAdmin />);
        expandCard();

        expect(screen.getByText('Scan ID:')).toBeInTheDocument();
        expect(screen.getByText('scan-12345')).toBeInTheDocument();
        expect(screen.getByText('Domain ID:')).toBeInTheDocument();
        expect(screen.getByText('de-federal-heat')).toBeInTheDocument();
        expect(screen.getByText('Crawl Status:')).toBeInTheDocument();
        expect(screen.getByText('success')).toBeInTheDocument();
    });

    it('defaults to hidden when isAdmin is not passed at all', () => {
        render(<SavedPolicy policy={policy()} tags={{}} />);
        expandCard();
        expect(screen.queryByText('Scan ID:')).not.toBeInTheDocument();
    });
});

describe('SavedPolicy relevance chip on curated records', () => {
    it('shows the numeric score and "relevance" label for a normal scored record', () => {
        render(<SavedPolicy policy={policy({ relevance_score: 7 })} tags={{}} />);
        expect(screen.getByText('7')).toBeInTheDocument();
        expect(screen.getByText('relevance')).toBeInTheDocument();
        expect(screen.queryByText('Curated')).not.toBeInTheDocument();
    });

    it('shows "Curated" instead of "0 relevance" for a curated master record', () => {
        render(<SavedPolicy policy={policy({ domain_id: 'curated_master_tab', relevance_score: 0 })} tags={{}} />);
        expect(screen.getByText('Curated')).toBeInTheDocument();
        expect(screen.queryByText('0')).not.toBeInTheDocument();
        expect(screen.queryByText('relevance')).not.toBeInTheDocument();
    });

    it('shows "Curated" for a curated master record with no relevance_score at all', () => {
        const curatedPolicy = policy({ domain_id: 'curated_master_tab' });
        delete curatedPolicy.relevance_score;
        render(<SavedPolicy policy={curatedPolicy} tags={{}} />);
        expect(screen.getByText('Curated')).toBeInTheDocument();
    });

    it('shows "Curated" for any record with a falsy relevance_score, even outside the curated domain', () => {
        // Judgment call: relevance_score 0/null reads as "hand-verified, no
        // score applies" everywhere, not just on curated_master_tab records -
        // see the fix report for the product-decision note on this heuristic.
        render(<SavedPolicy policy={policy({ domain_id: 'de-federal-heat', relevance_score: 0 })} tags={{}} />);
        expect(screen.getByText('Curated')).toBeInTheDocument();
    });
});
