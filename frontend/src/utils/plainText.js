// Cleans tip note/title text for display. Some tip sources (Google News RSS
// in particular) store text that is HTML-tag-encoded as literal entities -
// e.g. `&lt;a href="https://news.google.com/rss/articles/..."&gt;Title&lt;/a&gt;`
// - which reads as garbage if rendered as-is. This decodes entities, strips
// any tag-shaped text, and collapses whitespace to recover the plain title.
// React already escapes rendered text, so this is about cleaning the SOURCE
// string, never about injecting HTML - nothing here builds or sets markup.

const TAG_PATTERN = /<[^>]*>/g;
const WHITESPACE_PATTERN = /\s+/g;
const NAMED_ENTITIES = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
};

function decodeEntitiesFallback(text) {
  return text.replace(/&(#\d+|#x[0-9a-f]+|[a-z]+);/gi, (match, entity) => {
    if (entity[0] === '#') {
      const codePoint = entity[1].toLowerCase() === 'x'
        ? parseInt(entity.slice(2), 16)
        : parseInt(entity.slice(1), 10);
      return Number.isNaN(codePoint) ? match : String.fromCodePoint(codePoint);
    }
    return NAMED_ENTITIES[entity.toLowerCase()] ?? match;
  });
}

// Decodes HTML entities via an off-DOM <textarea> - its content model is
// text-only, so setting innerHTML decodes entities without ever parsing or
// executing markup (no dangerouslySetInnerHTML, nothing attached to the
// visible document).
export function decodeHtmlEntities(text) {
  if (typeof document !== 'undefined') {
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
  }
  return decodeEntitiesFallback(text);
}

export function stripTags(text) {
  return text.replace(TAG_PATTERN, ' ');
}

export function isBareUrl(text) {
  return /^https?:\/\/\S+$/i.test(text.trim());
}

function extractHostname(url) {
  try {
    return new URL(url.trim()).hostname;
  } catch {
    return null;
  }
}

// Cleans a tip's raw note/title text to plain, human-readable text. Falls
// back to a friendly placeholder when nothing readable is left, or when the
// only content is a bare Google News redirect URL.
export function cleanTipText(rawText, fallback = 'Untitled source') {
  const collapsed = stripTags(decodeHtmlEntities(String(rawText || '')))
    .replace(WHITESPACE_PATTERN, ' ')
    .trim();

  if (!collapsed) return fallback;

  if (isBareUrl(collapsed)) {
    const hostname = extractHostname(collapsed);
    if (!hostname || hostname === 'news.google.com') {
      return fallback;
    }
    return hostname;
  }

  return collapsed;
}
