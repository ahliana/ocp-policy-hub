import { cleanTipText, decodeHtmlEntities, isBareUrl, stripTags } from './plainText';

describe('decodeHtmlEntities', () => {
  it('decodes common named entities', () => {
    expect(decodeHtmlEntities('AT&amp;T announces &lt;plan&gt;')).toBe('AT&T announces <plan>');
  });

  it('decodes numeric entities', () => {
    expect(decodeHtmlEntities('caf&#233;')).toBe('café');
  });

  it('leaves plain text untouched', () => {
    expect(decodeHtmlEntities('nothing special here')).toBe('nothing special here');
  });
});

describe('stripTags', () => {
  it('removes real HTML tags', () => {
    expect(stripTags('<b>Bold</b> text')).toBe(' Bold  text');
  });

  it('leaves text with no tags untouched', () => {
    expect(stripTags('plain text')).toBe('plain text');
  });
});

describe('isBareUrl', () => {
  it('recognizes a string that is only a URL', () => {
    expect(isBareUrl('https://news.google.com/rss/articles/CBMi123')).toBe(true);
  });

  it('rejects a string with a URL plus other text', () => {
    expect(isBareUrl('See https://example.com for more')).toBe(false);
  });

  it('rejects plain text', () => {
    expect(isBareUrl('Germany requires reporting')).toBe(false);
  });
});

describe('cleanTipText', () => {
  it('decodes entity-encoded anchor markup down to the visible title', () => {
    const raw = '&lt;a href="https://news.google.com/rss/articles/CBMi123"&gt;Germany updates heat reuse rule&lt;/a&gt;';
    expect(cleanTipText(raw)).toBe('Germany updates heat reuse rule');
  });

  it('strips real (unescaped) tags too', () => {
    expect(cleanTipText('<a href="https://example.com">Real title</a>')).toBe('Real title');
  });

  it('passes clean plain text through unchanged', () => {
    expect(cleanTipText('Sweden requires district heat reporting')).toBe(
      'Sweden requires district heat reporting'
    );
  });

  it('collapses internal whitespace left behind by tag stripping', () => {
    expect(cleanTipText('&lt;span&gt;Title&lt;/span&gt;  with   spaces')).toBe('Title with spaces');
  });

  it('falls back to a friendly default for empty input', () => {
    expect(cleanTipText('')).toBe('Untitled source');
    expect(cleanTipText(null)).toBe('Untitled source');
    expect(cleanTipText(undefined)).toBe('Untitled source');
  });

  it('falls back to a friendly default for whitespace/tag-only input', () => {
    expect(cleanTipText('&lt;br/&gt;')).toBe('Untitled source');
  });

  it('falls back to a friendly default for a bare Google News RSS redirect URL', () => {
    expect(cleanTipText('https://news.google.com/rss/articles/CBMi123abc')).toBe('Untitled source');
  });

  it('falls back to the domain for a bare URL from another host', () => {
    expect(cleanTipText('https://example.com/some/article')).toBe('example.com');
  });

  it('accepts a custom fallback value', () => {
    expect(cleanTipText('', 'No title available')).toBe('No title available');
  });
});
