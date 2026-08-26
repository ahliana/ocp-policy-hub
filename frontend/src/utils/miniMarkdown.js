import React from 'react';

// A small, safe markdown-to-React renderer for the subset of Markdown the
// Ask box's AI answers actually emit: headings, bold, italic, inline code,
// links, and ordered/unordered lists. Everything below builds React
// elements/strings directly - never raw HTML - so it is XSS-safe by
// construction and works under a strict CSP with no dangerouslySetInnerHTML.

const SAFE_HREF_PATTERN = /^https?:\/\//i;
const BARE_URL_PATTERN = /(https?:\/\/[^\s)]+)/g;
const INLINE_PATTERN = /\[([^\]]+)\]\((\S+?)\)|\*\*([^*]+)\*\*|`([^`]+)`|\*([^*]+)\*|_([^_]+)_/g;
const HEADING_PATTERN = /^(#{1,6})\s+(.*)$/;
const UNORDERED_ITEM_PATTERN = /^[-*]\s+(.*)$/;
const ORDERED_ITEM_PATTERN = /^\d+\.\s+(.*)$/;

// Only http/https links are ever rendered as clickable anchors - javascript:,
// data:, and any other scheme are neutralized to plain text.
export function isSafeHref(href) {
  return SAFE_HREF_PATTERN.test(href || '');
}

function linkifyPlainText(text, keyPrefix) {
  const nodes = [];
  let lastIndex = 0;
  let index = 0;
  for (const match of text.matchAll(BARE_URL_PATTERN)) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    nodes.push(
      <a key={`${keyPrefix}-url-${index}`} href={match[0]} target="_blank" rel="noopener noreferrer">
        {match[0]}
      </a>
    );
    lastIndex = match.index + match[0].length;
    index += 1;
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

// Parses **bold**, *italic*/_italic_, `code`, [text](url) and bare URLs
// within a single line of text into an array of React nodes/strings.
export function parseInlineMarkdown(text, keyPrefix = 'inline') {
  const nodes = [];
  let lastIndex = 0;
  let index = 0;

  for (const match of text.matchAll(INLINE_PATTERN)) {
    if (match.index > lastIndex) {
      nodes.push(...linkifyPlainText(text.slice(lastIndex, match.index), `${keyPrefix}-pre-${index}`));
    }

    const [, linkText, linkHref, bold, code, italicStar, italicUnderscore] = match;
    const key = `${keyPrefix}-tok-${index}`;

    if (linkText !== undefined) {
      nodes.push(
        isSafeHref(linkHref) ? (
          <a key={key} href={linkHref} target="_blank" rel="noopener noreferrer">
            {linkText}
          </a>
        ) : (
          linkText
        )
      );
    } else if (bold !== undefined) {
      nodes.push(<strong key={key}>{bold}</strong>);
    } else if (code !== undefined) {
      nodes.push(<code key={key}>{code}</code>);
    } else if (italicStar !== undefined) {
      nodes.push(<em key={key}>{italicStar}</em>);
    } else if (italicUnderscore !== undefined) {
      nodes.push(<em key={key}>{italicUnderscore}</em>);
    }

    lastIndex = match.index + match[0].length;
    index += 1;
  }

  if (lastIndex < text.length) {
    nodes.push(...linkifyPlainText(text.slice(lastIndex), `${keyPrefix}-tail`));
  }

  return nodes;
}

// Parses a full answer into an array of block-level React elements:
// headings (#..######), unordered/ordered lists, and paragraphs (one per
// non-blank line, which preserves the line breaks the model uses between
// separate thoughts).
export function renderMiniMarkdown(text) {
  if (!text) return [];

  const lines = String(text).split('\n');
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const trimmed = lines[index].trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    const headingMatch = trimmed.match(HEADING_PATTERN);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const HeadingTag = `h${level}`;
      const key = `block-${blocks.length}`;
      blocks.push(
        <HeadingTag key={key}>{parseInlineMarkdown(headingMatch[2], key)}</HeadingTag>
      );
      index += 1;
      continue;
    }

    if (UNORDERED_ITEM_PATTERN.test(trimmed)) {
      const items = [];
      while (index < lines.length && UNORDERED_ITEM_PATTERN.test(lines[index].trim())) {
        items.push(lines[index].trim().match(UNORDERED_ITEM_PATTERN)[1]);
        index += 1;
      }
      const key = `block-${blocks.length}`;
      blocks.push(
        <ul key={key}>
          {items.map((item, itemIndex) => (
            // eslint-disable-next-line react/no-array-index-key
            <li key={`${key}-item-${itemIndex}`}>
              {parseInlineMarkdown(item, `${key}-item-${itemIndex}`)}
            </li>
          ))}
        </ul>
      );
      continue;
    }

    if (ORDERED_ITEM_PATTERN.test(trimmed)) {
      const items = [];
      while (index < lines.length && ORDERED_ITEM_PATTERN.test(lines[index].trim())) {
        items.push(lines[index].trim().match(ORDERED_ITEM_PATTERN)[1]);
        index += 1;
      }
      const key = `block-${blocks.length}`;
      blocks.push(
        <ol key={key}>
          {items.map((item, itemIndex) => (
            // eslint-disable-next-line react/no-array-index-key
            <li key={`${key}-item-${itemIndex}`}>
              {parseInlineMarkdown(item, `${key}-item-${itemIndex}`)}
            </li>
          ))}
        </ol>
      );
      continue;
    }

    const key = `block-${blocks.length}`;
    blocks.push(<p key={key}>{parseInlineMarkdown(trimmed, key)}</p>);
    index += 1;
  }

  return blocks;
}
