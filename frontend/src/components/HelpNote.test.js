import { fireEvent, render, screen } from '@testing-library/react';
import HelpNote from './HelpNote';

describe('HelpNote', () => {
  it('is closed by default', () => {
    render(<HelpNote label="Why this price?">Body text</HelpNote>);
    const details = screen.getByText('Why this price?').closest('details');
    expect(details).not.toHaveAttribute('open');
  });

  it('opens on summary click', () => {
    render(<HelpNote label="Why this price?">Body text</HelpNote>);
    const summary = screen.getByText('Why this price?');
    fireEvent.click(summary);
    expect(summary.closest('details')).toHaveAttribute('open');
  });

  it('renders the label text', () => {
    render(<HelpNote label="Custom label">Body text</HelpNote>);
    expect(screen.getByText('Custom label')).toBeInTheDocument();
  });

  it('renders the children in the body', () => {
    render(<HelpNote label="Foo">Nested body content</HelpNote>);
    expect(screen.getByText('Nested body content')).toBeInTheDocument();
  });

  it('honors defaultOpen', () => {
    render(<HelpNote label="Foo" defaultOpen>Body</HelpNote>);
    expect(screen.getByText('Foo').closest('details')).toHaveAttribute('open');
  });

  it('applies an extra className alongside help-note', () => {
    render(<HelpNote label="Foo" className="cost-breakdown">Body</HelpNote>);
    const details = screen.getByText('Foo').closest('details');
    expect(details).toHaveClass('help-note');
    expect(details).toHaveClass('cost-breakdown');
  });

});
