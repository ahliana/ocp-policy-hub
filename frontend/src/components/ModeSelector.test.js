import { fireEvent, render, screen } from '@testing-library/react';
import ModeSelector from './ModeSelector';

const STANDARD_ESTIMATE = {
  estimated_cost_usd: 4.2, estimated_cost_low_usd: 3.0, estimated_cost_high_usd: 6.0,
};
const DEEP_ESTIMATE = {
  estimated_cost_usd: 9.0, estimated_cost_low_usd: 7.0, estimated_cost_high_usd: 12.0,
};

describe('ModeSelector copy (WP-27)', () => {
  it('renders the three cards in outcome language', () => {
    render(<ModeSelector value="standard" onChange={jest.fn()} />);

    expect(screen.getByText('Standard')).toBeInTheDocument();
    expect(screen.getByText(
      'Checks the government sites we already watch for new and changed policies.',
    )).toBeInTheDocument();

    expect(screen.getByText('Discover')).toBeInTheDocument();
    expect(screen.getByText(
      "Searches the web for government sites we don't watch yet, then adds them to the watch list.",
    )).toBeInTheDocument();

    expect(screen.getByText('Deep')).toBeInTheDocument();
    expect(screen.getByText(
      'Rereads every page of the sites we watch, more thoroughly - for when you suspect something was missed.',
    )).toBeInTheDocument();
  });

  it('avoids em dashes and plain-language jargon in the card copy', () => {
    const { container } = render(<ModeSelector value="standard" onChange={jest.fn()} />);
    const text = container.textContent;
    expect(text).not.toMatch(/—/); // the pattern is the one allowed em dash: it bans them from copy
    expect(text).not.toMatch(/\bLLM\b/i);
    expect(text).not.toMatch(/\btoken\b/i);
    expect(text).not.toMatch(/\bAPI\b/);
  });
});

describe('ModeSelector Recommended badge', () => {
  it('shows the Recommended badge only on the Standard card', () => {
    const { container } = render(<ModeSelector value="standard" onChange={jest.fn()} />);
    const badges = container.querySelectorAll('span.mode-badge');
    expect(badges).toHaveLength(1);
    expect(badges[0]).toHaveTextContent('Recommended');

    const standardCard = screen.getByText('Standard').closest('.MuiCard-root');
    expect(standardCard).toContainElement(badges[0]);
  });
});

describe('ModeSelector price lines (WP-27)', () => {
  it('shows no price line on any card when no scope is selected', () => {
    render(
      <ModeSelector
        value="standard"
        onChange={jest.fn()}
        hasScope={false}
        standardEstimate={STANDARD_ESTIMATE}
        deepEstimate={DEEP_ESTIMATE}
      />,
    );

    expect(screen.queryByText(/est\. \$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Cost varies/)).not.toBeInTheDocument();
  });

  it('shows a price line on Standard and Deep from their own estimates, and the bounded-cost note on Discover', () => {
    render(
      <ModeSelector
        value="standard"
        onChange={jest.fn()}
        hasScope
        standardEstimate={STANDARD_ESTIMATE}
        deepEstimate={DEEP_ESTIMATE}
      />,
    );

    expect(screen.getByText('est. $3.00-$6.00')).toBeInTheDocument();
    expect(screen.getByText('est. $7.00-$12.00')).toBeInTheDocument();
    expect(screen.getByText('Cost varies - bounded per country')).toBeInTheDocument();
  });

  it('falls back to a single value when the estimate has no low/high spread', () => {
    render(
      <ModeSelector
        value="standard"
        onChange={jest.fn()}
        hasScope
        standardEstimate={{ estimated_cost_usd: 4.2 }}
        deepEstimate={null}
      />,
    );

    expect(screen.getByText('est. $4.20')).toBeInTheDocument();
  });

  it('shows no price line on a card whose estimate has not arrived yet', () => {
    render(
      <ModeSelector
        value="standard"
        onChange={jest.fn()}
        hasScope
        standardEstimate={STANDARD_ESTIMATE}
        deepEstimate={null}
      />,
    );

    expect(screen.getByText('est. $3.00-$6.00')).toBeInTheDocument();
    expect(screen.queryByText(/est\. \$7/)).not.toBeInTheDocument();
  });
});

describe('ModeSelector selection behavior (unchanged)', () => {
  it('calls onChange with the clicked card id', () => {
    const onChange = jest.fn();
    render(<ModeSelector value="standard" onChange={onChange} />);

    fireEvent.click(screen.getByText('Deep'));
    expect(onChange).toHaveBeenCalledWith('deep');
  });

  it('marks the selected card aria-pressed=true and the others false', () => {
    render(<ModeSelector value="deep" onChange={jest.fn()} />);

    const deepButton = screen.getByText('Deep').closest('button');
    const standardButton = screen.getByText('Standard').closest('button');
    expect(deepButton).toHaveAttribute('aria-pressed', 'true');
    expect(standardButton).toHaveAttribute('aria-pressed', 'false');
  });
});
