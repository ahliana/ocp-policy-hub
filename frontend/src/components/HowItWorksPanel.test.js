import { render, screen, within } from '@testing-library/react';
import HowItWorksPanel from './HowItWorksPanel';

describe('HowItWorksPanel top-level help note (WP-31)', () => {
  it('renders the top-level label, closed by default', () => {
    render(<HowItWorksPanel />);
    const summary = screen.getByText(
      'How PolicyPulse works - from government website to the public map',
    );
    const details = summary.closest('details');
    expect(details).toHaveClass('help-note');
    expect(details).not.toHaveAttribute('open');
  });

  it('shows the intro sentence', () => {
    render(<HowItWorksPanel />);
    expect(screen.getByText(
      'Here is the whole journey a policy takes through PolicyPulse, and where you steer each step.',
    )).toBeInTheDocument();
  });
});

describe('HowItWorksPanel stages', () => {
  it('names all eight stages', () => {
    render(<HowItWorksPanel />);
    expect(screen.getByText('Gather.')).toBeInTheDocument();
    expect(screen.getByText('Keyword screen.')).toBeInTheDocument();
    expect(screen.getByText('Fast AI pass.')).toBeInTheDocument();
    expect(screen.getByText('Full AI read.')).toBeInTheDocument();
    expect(screen.getByText('Automatic checks.')).toBeInTheDocument();
    expect(screen.getByText('Save and record.')).toBeInTheDocument();
    expect(screen.getByText('Human review.')).toBeInTheDocument();
    expect(screen.getByText('The public map.')).toBeInTheDocument();
  });

  it('renders each stage name in bold', () => {
    render(<HowItWorksPanel />);
    expect(screen.getByText('Gather.').tagName).toBe('STRONG');
  });

  it('shows a "Where you control it" line only for the four stages the spec calls out', () => {
    render(<HowItWorksPanel />);
    const controlLines = screen.getAllByText(/^Where you control it:/);
    expect(controlLines).toHaveLength(4);

    expect(screen.getByText('Where you control it: the Sources list, and the Tips inbox.'))
      .toBeInTheDocument();
    expect(screen.getByText('Where you control it: the Keywords panel.')).toBeInTheDocument();
    expect(screen.getByText('Where you control it: the cost level in the settings window.'))
      .toBeInTheDocument();
    expect(screen.getByText(
      'Where you control it: the review queue, the Library, and the public visibility setting.',
    )).toBeInTheDocument();
  });

  it('does not give the Full AI read stage a "Where you control it" line', () => {
    render(<HowItWorksPanel />);
    const stageItem = screen.getByText('Full AI read.').closest('li');
    expect(within(stageItem).queryByText(/Where you control it/)).not.toBeInTheDocument();
  });
});

describe('HowItWorksPanel cost paragraph', () => {
  it('shows the cost paragraph verbatim', () => {
    render(<HowItWorksPanel />);
    expect(screen.getByText(
      'Only three steps spend money: the fast AI pass, the full AI read, and the short report '
      + 'written at the end of a scan. Everything else is free. The estimate on the scan panel '
      + 'prices exactly those steps before you run anything.',
    )).toBeInTheDocument();
  });
});

describe('HowItWorksPanel diagram (WP-38-style)', () => {
  it('nests a closed-by-default "See it as a picture" note inside the top-level note', () => {
    render(<HowItWorksPanel />);
    const nestedSummary = screen.getByText('See it as a picture');
    const nestedDetails = nestedSummary.closest('details');
    expect(nestedDetails).not.toHaveAttribute('open');

    const outerDetails = screen.getByText(
      'How PolicyPulse works - from government website to the public map',
    ).closest('details');
    expect(outerDetails).toContainElement(nestedDetails);
  });

  it('renders an inline SVG with the spec aria-label and all six stage boxes', () => {
    render(<HowItWorksPanel />);
    const svg = screen.getByRole('img');
    expect(svg).toHaveAttribute(
      'aria-label',
      'A policy flows from gathering through the keyword screen, a fast AI pass, a full AI read, '
      + 'and human review before reaching the public map.',
    );
    expect(within(svg).getByText('Gather')).toBeInTheDocument();
    expect(within(svg).getByText('Keyword screen')).toBeInTheDocument();
    expect(within(svg).getByText('Fast AI pass')).toBeInTheDocument();
    expect(within(svg).getByText('Full AI read')).toBeInTheDocument();
    expect(within(svg).getByText('Review')).toBeInTheDocument();
    expect(within(svg).getByText('Public map')).toBeInTheDocument();
  });
});

describe('HowItWorksPanel has no data dependency', () => {
  it('never calls fetch (no live data, no hardcoded source count)', () => {
    const fetchSpy = jest.fn();
    global.fetch = fetchSpy;
    render(<HowItWorksPanel />);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('avoids plain-language jargon words anywhere in its copy', () => {
    const { container } = render(<HowItWorksPanel />);
    const text = container.textContent;
    expect(text).not.toMatch(/\bclaude\b/i);
    expect(text).not.toMatch(/\bsonnet\b/i);
    expect(text).not.toMatch(/\bhaiku\b/i);
    expect(text).not.toMatch(/\bllm\b/i);
    expect(text).not.toMatch(/\btoken\b/i);
    expect(text).not.toMatch(/\bAPI\b/);
    expect(text).not.toMatch(/—/);
  });
});
