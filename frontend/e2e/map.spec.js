// Real-pointer smoke test for the map's click / double-click / drag / admin
// flows. Keep assertions on shapes and roles, not exact counts - the data
// grows. Expects a local, ungated deployment (no ADMIN_TOKEN), which is the
// development default.
const { test, expect } = require('@playwright/test');

const US_NAME = /United States of America: \d+ sources, \d+ policies/;

test.describe('world map real-pointer flows', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // A fresh profile gets the first-run welcome modal, exactly like a real
    // first visitor - close it the way they would.
    const welcomeClose = page.getByRole('button', { name: 'Close help window' });
    if (await welcomeClose.isVisible().catch(() => false)) {
      await welcomeClose.click();
    }
    await expect(page.getByRole('button', { name: US_NAME })).toBeVisible();
  });

  test('click a country opens its panel with a view-policies action', async ({ page }) => {
    await page.getByRole('button', { name: US_NAME }).click();
    await expect(
      page.getByRole('button', { name: /View \d+ found (policy|policies)/ }),
    ).toBeVisible();
  });

  test('view-policies filters the discovered list to the place', async ({ page }) => {
    await page.getByRole('button', { name: US_NAME }).click();
    await page.getByRole('button', { name: /View \d+ found (policy|policies)/ }).click();
    await expect(page.getByText(/United States - \d+ (policy|policies)/)).toBeVisible();
    await page.getByRole('button', { name: /Clear United States filter/ }).click();
    await expect(page.getByText(/United States - \d+ (policy|policies)/)).toHaveCount(0);
  });

  test('double-click drills into states and a state opens its region panel', async ({ page }) => {
    await page.getByRole('button', { name: US_NAME }).dblclick();
    const california = page.getByRole('button', { name: /California: \d+ sources/ });
    await expect(california).toBeVisible();
    await california.click();
    await expect(
      page.getByRole('button', { name: /View \d+ found (policy|policies)/ }),
    ).toBeVisible();
  });

  test('a drag pans without killing the next click', async ({ page }) => {
    const us = page.getByRole('button', { name: US_NAME });
    const box = await us.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2 + 40, { steps: 8 });
    await page.mouse.up();
    // The regression this file exists for: after interacting, a real click
    // must still open the panel (pointer capture must not swallow it).
    await page.getByRole('button', { name: US_NAME }).click();
    await expect(
      page.getByRole('button', { name: /View \d+ found (policy|policies)/ }),
    ).toBeVisible();
  });

  test('searching for a native-language term surfaces a matching policy card', async ({ page }) => {
    await page.getByPlaceholder('Search policies...').fill('Abwärme');
    await expect(page.locator('.saved-policy-name').first()).toBeVisible();
  });

  test('zooming out past minimum returns to the world map', async ({ page }) => {
    await page.getByRole('button', { name: US_NAME }).dblclick();
    const california = page.getByRole('button', { name: /California: \d+ sources/ });
    await expect(california).toBeVisible();
    // boundingBox coordinates are viewport-relative and the map can sit
    // below the fold - bring it into view and take fresh coordinates, or
    // the wheels land on the page (scrolling it) instead of the map.
    await california.scrollIntoViewIfNeeded();
    const box = await california.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    // The drill-down opens fully zoomed out, so two real wheel-out notches
    // cross the hysteresis threshold and must eject back to the world view.
    // Spaced apart - back-to-back wheels in one frame can coalesce into a
    // single event, which the hysteresis deliberately treats as one attempt.
    // The drill-down opens fully zoomed out, so two real wheel-out notches
    // cross the hysteresis threshold and must eject back to the world view.
    // Spaced apart - back-to-back wheels in one frame can coalesce into a
    // single event, which the hysteresis deliberately treats as one attempt.
    await page.mouse.wheel(0, 120);
    await page.waitForTimeout(150);
    await page.mouse.wheel(0, 120);
    await expect(page.getByRole('button', { name: US_NAME })).toBeVisible();
  });

  test('the World control button exits the drill-down', async ({ page }) => {
    await page.getByRole('button', { name: US_NAME }).dblclick();
    await page.getByRole('button', { name: 'Back to world map' }).click();
    await expect(page.getByRole('button', { name: US_NAME })).toBeVisible();
  });

  test('admin area toggles and hides operator tools by default', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Find new policies' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Admin', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Find new policies' })).toBeVisible();
    await page.getByRole('button', { name: 'Exit admin' }).click();
    await expect(page.getByRole('heading', { name: 'Find new policies' })).toHaveCount(0);
  });
});
