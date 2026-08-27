// Real-viewport smoke test for WP-15 (admin frame containment). Confirms
// every panel inside the admin area stays within its right edge, and that
// the page itself never grows a horizontal scrollbar, at both a laptop and
// a wide-desktop viewport. Expects a local, ungated deployment (no
// ADMIN_TOKEN), which is the development default - same setup as
// map.spec.js:
//
//   npx playwright install chromium   # once
//   npm run e2e
const { test, expect } = require('@playwright/test');

const VIEWPORTS = [
  { width: 1280, height: 900 },
  { width: 1920, height: 1080 },
];

VIEWPORTS.forEach(({ width, height }) => {
  test.describe(`admin frame containment at ${width}x${height}`, () => {
    test.use({ viewport: { width, height } });

    test.beforeEach(async ({ page }) => {
    // On an admin-gated stack (local .env with ADMIN_TOKEN, or production),
    // sign in the way the app does: the token seeds sessionStorage before
    // load, passed via E2E_ADMIN_TOKEN at runtime - never committed.
    const adminToken = process.env.E2E_ADMIN_TOKEN;
    if (adminToken) {
      await page.addInitScript((t) => sessionStorage.setItem('admin-token', t), adminToken);
    }
      await page.goto('/');
      // A fresh profile gets the first-run welcome modal, exactly like a
      // real first visitor - close it the way they would.
      const welcomeClose = page.getByRole('button', { name: 'Close help window' });
      if (await welcomeClose.isVisible().catch(() => false)) {
        await welcomeClose.click();
      }
      await page.getByRole('button', { name: 'Admin', exact: true }).click();
      await expect(page.getByRole('heading', { name: 'Find new policies' })).toBeVisible();
    });

    test('every direct child panel stays within the admin area right edge', async ({ page }) => {
      const adminArea = page.locator('.admin-area');
      await expect(adminArea).toBeVisible();

      // One atomic DOM read. A count()-then-nth() loop raced the panels'
      // own data fetches: a re-render between the two calls can drop a
      // child, and boundingBox() on the vanished index waits out the whole
      // test timeout (seen live at 1920px on first run).
      const geometry = await page.evaluate(() => {
        const area = document.querySelector('.admin-area');
        const areaRect = area.getBoundingClientRect();
        return {
          right: areaRect.right,
          children: Array.from(area.children).map((child) => {
            const r = child.getBoundingClientRect();
            return { className: child.className, width: r.width, right: r.right };
          }),
        };
      });

      expect(geometry.children.length).toBeGreaterThan(0);
      for (const child of geometry.children) {
        // Collapsed/zero-size children (e.g. a hidden note) contribute
        // nothing to overflow and have no meaningful box to check.
        if (child.width === 0) continue;
        expect(child.right, `${child.className} exceeds the admin frame`)
          .toBeLessThanOrEqual(geometry.right + 1);
      }
    });

    test('the page has no horizontal scrollbar', async ({ page }) => {
      const overflow = await page.evaluate(() => (
        document.body.scrollWidth - document.body.clientWidth
      ));
      expect(overflow).toBeLessThanOrEqual(0);
    });
  });
});
