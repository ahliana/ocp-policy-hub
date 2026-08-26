import React from 'react';
import { render, screen } from '@testing-library/react';
import { isSafeHref, parseInlineMarkdown, renderMiniMarkdown } from './miniMarkdown';

describe('isSafeHref', () => {
  it('allows http and https', () => {
    expect(isSafeHref('https://example.com')).toBe(true);
    expect(isSafeHref('http://example.com')).toBe(true);
  });

  it('rejects javascript:, data:, and other schemes', () => {
    expect(isSafeHref('javascript:alert(1)')).toBe(false);
    expect(isSafeHref('data:text/html,<script>alert(1)</script>')).toBe(false);
    expect(isSafeHref('ftp://example.com')).toBe(false);
    expect(isSafeHref('')).toBe(false);
  });
});

describe('renderMiniMarkdown headings', () => {
  it('renders # through ###### as h1-h6', () => {
    for (let level = 1; level <= 6; level += 1) {
      const hashes = '#'.repeat(level);
      render(<div>{renderMiniMarkdown(`${hashes} Heading ${level}`)}</div>);
      expect(screen.getByRole('heading', { level })).toHaveTextContent(`Heading ${level}`);
    }
  });
});

describe('renderMiniMarkdown inline formatting', () => {
  it('renders **bold** as <strong>', () => {
    const { container } = render(<div>{renderMiniMarkdown('This is **bold** text.')}</div>);
    expect(container.querySelector('strong')).toHaveTextContent('bold');
  });

  it('renders *italic* as <em>', () => {
    const { container } = render(<div>{renderMiniMarkdown('This is *italic* text.')}</div>);
    expect(container.querySelector('em')).toHaveTextContent('italic');
  });

  it('renders _italic_ as <em>', () => {
    const { container } = render(<div>{renderMiniMarkdown('This is _italic_ text.')}</div>);
    expect(container.querySelector('em')).toHaveTextContent('italic');
  });

  it('renders `code` as <code>', () => {
    const { container } = render(<div>{renderMiniMarkdown('Run `npm test` now.')}</div>);
    expect(container.querySelector('code')).toHaveTextContent('npm test');
  });
});

describe('renderMiniMarkdown lists', () => {
  it('renders - items as an unordered list', () => {
    const { container } = render(
      <div>{renderMiniMarkdown('- First item\n- Second item')}</div>
    );
    const list = container.querySelector('ul');
    expect(list).not.toBeNull();
    const items = list.querySelectorAll('li');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('First item');
    expect(items[1]).toHaveTextContent('Second item');
  });

  it('renders * items as an unordered list', () => {
    const { container } = render(<div>{renderMiniMarkdown('* Alpha\n* Beta')}</div>);
    expect(container.querySelectorAll('ul li')).toHaveLength(2);
  });

  it('renders numbered items as an ordered list', () => {
    const { container } = render(
      <div>{renderMiniMarkdown('1. First\n2. Second\n3. Third')}</div>
    );
    const list = container.querySelector('ol');
    expect(list).not.toBeNull();
    expect(list.querySelectorAll('li')).toHaveLength(3);
  });
});

describe('renderMiniMarkdown links', () => {
  it('renders [text](url) as a safe anchor for http/https', () => {
    const { container } = render(
      <div>{renderMiniMarkdown('See [the directive](https://ec.europa.eu/law) for details.')}</div>
    );
    const link = container.querySelector('a');
    expect(link).not.toBeNull();
    expect(link).toHaveAttribute('href', 'https://ec.europa.eu/law');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(link).toHaveTextContent('the directive');
  });

  it('neutralizes a javascript: link to plain text', () => {
    const { container } = render(
      <div>{renderMiniMarkdown('[click me](javascript:alert(1))')}</div>
    );
    expect(container.querySelector('a')).toBeNull();
    expect(container).toHaveTextContent('click me');
  });

  it('neutralizes a data: link to plain text', () => {
    const { container } = render(
      <div>{renderMiniMarkdown('[open](data:text/html,<script>alert(1)</script>)')}</div>
    );
    expect(container.querySelector('a')).toBeNull();
    expect(container).toHaveTextContent('open');
  });

  it('still linkifies bare URLs outside markdown link syntax', () => {
    const { container } = render(
      <div>{renderMiniMarkdown('See https://ec.europa.eu/law for details.')}</div>
    );
    const link = container.querySelector('a');
    expect(link).toHaveAttribute('href', 'https://ec.europa.eu/law');
  });
});

describe('renderMiniMarkdown paragraphs and plain text', () => {
  it('passes plain text through unchanged', () => {
    const { container } = render(<div>{renderMiniMarkdown('No policies found.')}</div>);
    expect(container).toHaveTextContent('No policies found.');
    expect(container.querySelector('strong, em, code, a, ul, ol, h1, h2, h3, h4, h5, h6')).toBeNull();
  });

  it('preserves line breaks as separate paragraphs', () => {
    const { container } = render(
      <div>{renderMiniMarkdown('First paragraph.\nSecond paragraph.')}</div>
    );
    const paragraphs = container.querySelectorAll('p');
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0]).toHaveTextContent('First paragraph.');
    expect(paragraphs[1]).toHaveTextContent('Second paragraph.');
  });

  it('returns an empty array for empty input', () => {
    expect(renderMiniMarkdown('')).toEqual([]);
  });
});

describe('parseInlineMarkdown', () => {
  it('returns plain strings for text with no markdown syntax', () => {
    expect(parseInlineMarkdown('just text')).toEqual(['just text']);
  });
});
