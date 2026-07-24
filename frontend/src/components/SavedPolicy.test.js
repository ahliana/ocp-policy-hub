import React from 'react';
import { render, screen } from '@testing-library/react';
import SavedPolicy from './SavedPolicy';

function policy(overrides = {}) {
    return {
        policy_name: 'Heat Reuse Act',
        jurisdiction: 'Sweden',
        policy_type: 'law',
        relevance_score: 7,
        summary: 'A summary',
        review_status: 'new',
        ...overrides,
    };
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
