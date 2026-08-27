import { fireEvent, render, screen } from '@testing-library/react';
import InfoHotspot from './InfoHotspot';

function getTrigger() {
  return screen.getByRole('button', { name: 'More info' });
}

describe('InfoHotspot', () => {
  it('renders a "?" trigger with the label as its accessible name', () => {
    render(<InfoHotspot label="More info">Tip text</InfoHotspot>);
    expect(getTrigger()).toHaveTextContent('?');
  });

  it('the tip is absent from the accessibility tree when closed', () => {
    render(<InfoHotspot label="More info">Tip text</InfoHotspot>);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'false');
  });

  it('click opens the tip and sets aria-expanded', () => {
    render(<InfoHotspot label="More info">Tip text</InfoHotspot>);
    fireEvent.click(getTrigger());
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('status')).toHaveTextContent('Tip text');
  });

  it('a second click closes it (toggle)', () => {
    render(<InfoHotspot label="More info">Tip text</InfoHotspot>);
    fireEvent.click(getTrigger());
    fireEvent.click(getTrigger());
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('Escape closes it after a click opened it (sticky)', () => {
    render(<InfoHotspot label="More info">Tip text</InfoHotspot>);
    fireEvent.click(getTrigger());
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'true');

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('a click outside closes a sticky (click-opened) tip', () => {
    render(
      <div>
        <InfoHotspot label="More info">Tip text</InfoHotspot>
        <button type="button">Elsewhere</button>
      </div>,
    );
    fireEvent.click(getTrigger());
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'true');

    fireEvent.mouseDown(screen.getByRole('button', { name: 'Elsewhere' }));
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'false');
  });

  it('mouse enter opens it and mouse leave closes it when opened by hover', () => {
    render(<InfoHotspot label="More info">Tip text</InfoHotspot>);
    fireEvent.mouseEnter(getTrigger());
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'true');

    fireEvent.mouseLeave(getTrigger());
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'false');
  });

  it('mouse leave does NOT close a tip that a click made sticky', () => {
    render(<InfoHotspot label="More info">Tip text</InfoHotspot>);
    fireEvent.click(getTrigger());
    fireEvent.mouseEnter(getTrigger());
    fireEvent.mouseLeave(getTrigger());
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'true');
  });

  it('a click on a hover-opened tip PINS it instead of closing it', () => {
    // Real browsers fire mouseenter before click, so the tip is already
    // open (non-sticky) when a mouse user clicks to keep it - the click
    // must pin, not toggle-close under them.
    render(<InfoHotspot label="More info">Tip text</InfoHotspot>);
    fireEvent.mouseEnter(getTrigger());
    fireEvent.click(getTrigger());
    fireEvent.mouseLeave(getTrigger());
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'true');
  });

  it('focus opens the tip', () => {
    render(<InfoHotspot label="More info">Tip text</InfoHotspot>);
    fireEvent.focus(getTrigger());
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('blur closes a non-sticky (focus-opened) tip', () => {
    render(<InfoHotspot label="More info">Tip text</InfoHotspot>);
    fireEvent.focus(getTrigger());
    fireEvent.blur(getTrigger());
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'false');
  });

  it('blur does NOT close a tip that a click made sticky', () => {
    render(<InfoHotspot label="More info">Tip text</InfoHotspot>);
    fireEvent.click(getTrigger());
    fireEvent.focus(getTrigger());
    fireEvent.blur(getTrigger());
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'true');
  });
});
