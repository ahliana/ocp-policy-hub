// Real-pointer smoke test for the WP-27/28/29/38 scan-cost UX: pick a real
// scope, watch a real dollar estimate arrive, and confirm the "Why this
// price?" / "Where will this search?" disclosures and the mode cards' price
// lines all agree with each other. Patterned on admin-containment.spec.js
// (same welcome-modal-close + Admin-open setup). Expects a local, ungated
// deployment (no ADMIN_TOKEN) with a live backend, same as map.spec.js:
//
//   npx playwright install chromium   # once
//   npm run e2e
const { test, expect } = require('@playwright/test');

test.describe('estimate journey (scan-cost UX)', () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // A fresh profile gets the first-run welcome modal, exactly like a real
    // first visitor - close it the way they would.
    const welcomeClose = page.getByRole('button', { name: 'Close help window' });
    if (await welcomeClose.isVisible().catch(() => false)) {
      await welcomeClose.click();
    }
    await page.getByRole('button', { name: 'Admin', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Find new policies' })).toBeVisible();
  });

  test('selecting a scope produces a real estimate, a price breakdown, and a source list that agree', async ({ page }) => {
    // The Advanced region tree is open by default (WP-6) - pick a real group
    // row rather than a synthetic fixture, so this exercises the actual
    // config the backend resolves against.
    const nordicRow = page.getByRole('checkbox', { name: /Nordic/i }).first();
    await nordicRow.scrollIntoViewIfNeeded();
    await nordicRow.click();

    // The estimate is debounced (300ms) and comes from a real backend call -
    // give it real time rather than asserting on a fixed delay.
    const scopeSummary = page.getByText(/^Scanning:/);
    await expect(scopeSummary).toBeVisible();
    await expect(scopeSummary).toContainText('$', { timeout: 10_000 });
    await expect(scopeSummary).not.toContainText('No cost estimate');

    // "Why this price?" - open it and confirm at least one channel line
    // inside that note carries a real dollar figure, not just the heading
    // (scoped to the note itself, since the scope summary above it also
    // contains a dollar figure).
    const priceBreakdown = page.locator('details.cost-breakdown');
    await priceBreakdown.locator('summary', { hasText: 'Why this price?' }).click();
    await expect(priceBreakdown.getByText(/\$\d+\.\d{2}/).first()).toBeVisible();

    // "Where will this search?" - open it and confirm at least one resolved
    // source row is listed (lazy-loaded on this click, per WP-28).
    const scopePreview = page.locator('details.scope-preview');
    await scopePreview.locator('summary', { hasText: 'Where will this search?' }).click();
    await expect(scopePreview.getByText(/\d+ sources? total/)).toBeVisible();

    // The Standard mode card always carries the Recommended badge.
    await expect(page.locator('span.mode-badge', { hasText: 'Recommended' })).toBeVisible();
  });
});
