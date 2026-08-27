// Real-pointer smoke test for WP-30/WP-31's help content: every HelpNote and
// InfoHotspot renders real text, and none of it leaks the internal
// vocabulary the content spec (20260826_1300_PolicyPulse_Phase_D_Content_
// Spec.md) bans from user-facing copy. Patterned on admin-containment.spec.js
// and estimate-journey.spec.js (same welcome-modal-close + Admin-open setup,
// plus a real scope selection so the conditionally-rendered notes - "Why
// this price?", "Where will this search?", and their nested diagrams -
// are present too). Expects a local, ungated deployment (no ADMIN_TOKEN)
// with a live backend, same as map.spec.js:
//
//   npx playwright install chromium   # once
//   npm run e2e
const { test, expect } = require('@playwright/test');

// Case-insensitive banned terms, plus the standalone word "API" checked
// separately (case-sensitive, word-boundary) per the content spec - the API
// key settings window's own title is outside help bodies/hotspot tips and
// is never collected here, so it can't trip this check.
const BANNED_TERMS = [
  'claude', 'sonnet', 'haiku', 'llm', 'token', 'endpoint', 'backend',
  'frontend', 'sql', 'pydantic',
];

// Opens every details.help-note summary currently in the DOM, one at a time,
// re-querying after each click - opening a note can reveal a nested one
// (e.g. the "See it as a picture" diagrams), so a single pass would miss it.
// Bounded well above the known note count so a genuine bug fails loudly
// instead of hanging.
async function openAllHelpNotes(page) {
  const MAX_ITERATIONS = 30;
  for (let i = 0; i < MAX_ITERATIONS; i += 1) {
    const closedSummaries = page.locator('details.help-note:not([open]) > summary');
    // eslint-disable-next-line no-await-in-loop
    const remaining = await closedSummaries.count();
    if (remaining === 0) return;
    // eslint-disable-next-line no-await-in-loop
    await closedSummaries.first().click();
  }
  throw new Error('openAllHelpNotes did not settle - a note may not be opening on click');
}

test.describe('help content (WP-30/WP-31 HelpNote and InfoHotspot copy)', () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    const welcomeClose = page.getByRole('button', { name: 'Close help window' });
    if (await welcomeClose.isVisible().catch(() => false)) {
      await welcomeClose.click();
    }
    await page.getByRole('button', { name: 'Admin', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Find new policies' })).toBeVisible();

    // A real scope so the cost-breakdown and scope-preview HelpNotes (which
    // only render once an estimate/resolved source list exists) are present
    // too, not just the always-on ones.
    const nordicRow = page.getByRole('checkbox', { name: /Nordic/i }).first();
    await nordicRow.scrollIntoViewIfNeeded();
    await nordicRow.click();
    await expect(page.getByText(/^Scanning:/)).toContainText('$', { timeout: 10_000 });
  });

  test('every HelpNote opens to a non-empty body with no banned vocabulary', async ({ page }) => {
    await openAllHelpNotes(page);

    const bodies = page.locator('details.help-note[open] > .help-note-body');
    const bodyCount = await bodies.count();
    expect(bodyCount).toBeGreaterThan(0);

    let combinedText = '';
    for (let i = 0; i < bodyCount; i += 1) {
      // eslint-disable-next-line no-await-in-loop
      const text = await bodies.nth(i).innerText();
      expect(text.trim().length).toBeGreaterThan(0);
      combinedText += ` ${text}`;
    }

    // Every InfoHotspot on the page, opened one at a time via keyboard focus
    // (not a click - WP-37's toggletip opens on focus for keyboard users).
    const hotspotTriggers = page.locator('.info-hotspot-trigger');
    const hotspotCount = await hotspotTriggers.count();
    expect(hotspotCount).toBeGreaterThan(0);

    for (let i = 0; i < hotspotCount; i += 1) {
      const trigger = hotspotTriggers.nth(i);
      // eslint-disable-next-line no-await-in-loop
      await trigger.focus();
      const tip = trigger.locator('xpath=following-sibling::span[contains(@class, "info-hotspot-tip")]');
      // eslint-disable-next-line no-await-in-loop
      await expect(tip).toBeVisible();
      // eslint-disable-next-line no-await-in-loop
      const tipText = await tip.innerText();
      expect(tipText.trim().length).toBeGreaterThan(0);
      combinedText += ` ${tipText}`;
    }

    const lowerText = combinedText.toLowerCase();
    for (const term of BANNED_TERMS) {
      expect(lowerText).not.toContain(term);
    }
    expect(combinedText).not.toMatch(/\bAPI\b/);
  });
});
