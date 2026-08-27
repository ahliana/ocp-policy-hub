import { render, screen } from '@testing-library/react';
import CostFunnelDiagram from './CostFunnelDiagram';

const ESTIMATE = {
  domain_count: 5,
  estimated_pages: 500,
  estimated_screening_calls: 50,
  estimated_analysis_calls: 25,
};

describe('CostFunnelDiagram (WP-38)', () => {
  it('renders nothing when there is no estimate', () => {
    const { container } = render(<CostFunnelDiagram estimate={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the exact numbers from the estimate for each stage', () => {
    render(<CostFunnelDiagram estimate={ESTIMATE} />);

    expect(screen.getByText('Sources')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();

    expect(screen.getByText('Pages checked')).toBeInTheDocument();
    expect(screen.getByText('~500')).toBeInTheDocument();

    expect(screen.getByText('Fast AI pass')).toBeInTheDocument();
    expect(screen.getByText('~50')).toBeInTheDocument();

    expect(screen.getByText('Full AI read')).toBeInTheDocument();
    expect(screen.getByText('~25')).toBeInTheDocument();
  });

  it('has role img and a summarizing aria-label', () => {
    render(<CostFunnelDiagram estimate={ESTIMATE} />);
    const svg = screen.getByRole('img');
    expect(svg).toHaveAttribute(
      'aria-label',
      'A flow from 5 sources, to ~500 pages checked, to ~50 fast AI passes, to ~25 full AI reads.',
    );
  });

  it('uses the viewBox and light-mode palette from the spec', () => {
    render(<CostFunnelDiagram estimate={ESTIMATE} />);
    const svg = screen.getByRole('img');
    expect(svg).toHaveAttribute('viewBox', '0 0 640 140');

    const rects = svg.querySelectorAll('rect');
    expect(rects).toHaveLength(4);
    rects.forEach((rect) => {
      expect(rect).toHaveAttribute('fill', '#eef2f7');
      expect(rect).toHaveAttribute('stroke', '#d7dee8');
    });
  });

  it('avoids plain-language jargon in the aria-label', () => {
    render(<CostFunnelDiagram estimate={ESTIMATE} />);
    const label = screen.getByRole('img').getAttribute('aria-label');
    expect(label).not.toMatch(/\bLLM\b/i);
    expect(label).not.toMatch(/\btoken\b/i);
    expect(label).not.toMatch(/\bAPI\b/);
  });
});
