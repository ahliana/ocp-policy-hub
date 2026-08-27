// WP-40 end-user POV screen matrix. One test.describe per surface a real
// visitor or admin actually sees, each ending in a full-viewport screenshot
// under test-results/screens/ so a human can flip through what the app looks
// like without re-running the app themselves. Selectors are verified against
// the real component sources in frontend/src/components/ (see each test's
// comment), not guessed - the accessible names, class names, and aria-labels
// below are copy-pasted from what those components actually render.
//
// Patterned on help-content.spec.js and admin-containment.spec.js: same
// welcome-modal-close, same E2E_ADMIN_TOKEN sessionStorage seeding, same
// Admin-open steps (here factored into the openAdmin(page) helper below).
// Public-only surfaces (public-map, public-search-and-policies,
// public-tip-form) skip the admin token/open steps entirely, matching
// map.spec.js's simpler beforeEach - there is nothing admin about them.
//
// Expects a local, ungated deployment (no ADMIN_TOKEN) with a live backend
// and real seeded data, same as the other e2e specs:
//
//   npx playwright install chromium   # once
//   npm run e2e
//
// The no-jargon sweep over HelpNote/InfoHotspot copy already lives in
// help-content.spec.js and is deliberately not duplicated here.
const { test, expect } = require('@playwright/test');

test.use({ viewport: { width: 1280, height: 900 } });

// Shared by every admin-* surface below: seeds the admin token the same way
// the app reads it (sessionStorage, before load), closes the first-run
// welcome modal exactly like a real visitor would, then opens Admin and
// waits for a panel that only renders once the admin area is unlocked -
// same sequence as help-content.spec.js / admin-containment.spec.js /
// estimate-journey.spec.js.
async function openAdmin(page) {
  const adminToken = process.env.E2E_ADMIN_TOKEN;
  if (adminToken) {
    await page.addInitScript((t) => sessionStorage.setItem('admin-token', t), adminToken);
  }
  await page.goto('/');
  const welcomeClose = page.getByRole('button', { name: 'Close help window' });
  if (await welcomeClose.isVisible().catch(() => false)) {
    await welcomeClose.click();
  }
  await page.getByRole('button', { name: 'Admin', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Find new policies' })).toBeVisible();
}

async function closeWelcomeModal(page) {
  await page.goto('/');
  const welcomeClose = page.getByRole('button', { name: 'Close help window' });
  if (await welcomeClose.isVisible().catch(() => false)) {
    await welcomeClose.click();
  }
}

// 1. public-map
// Source: frontend/src/components/WorldMapSvg.js renders
//   <svg role="group" aria-label="World map of PolicyPulse coverage">
// frontend/src/components/MapLegend.js renders
//   <div role="group" aria-label="Coverage legend, click to filter">
//     one <button class="wm-legend-item"> per tier
test.describe('public-map', () => {
  test.beforeEach(async ({ page }) => {
    await closeWelcomeModal(page);
  });

  test('world map and coverage legend render', async ({ page }) => {
    const map = page.getByRole('group', { name: 'World map of PolicyPulse coverage' });
    await expect(map).toBeVisible();

    const legend = page.getByRole('group', { name: 'Coverage legend, click to filter' });
    await expect(legend).toBeVisible();
    await expect(legend.locator('button.wm-legend-item').first()).toBeVisible();

    await page.screenshot({ path: 'test-results/screens/01-public-map.png', fullPage: false });
  });
});

// 2. public-search-and-policies
// Source: frontend/src/App.js wraps PolicyList in
//   <section class="app-stage" aria-label="Discovered policies">
// frontend/src/components/SavedPolicy.js renders each card as
//   <button class="saved-policy-header" aria-expanded=...>
//     <span class="saved-policy-name">{displayName}</span> ...
//   and, once expanded, <div class="saved-policy-details"><h4>Key Information</h4>...
test.describe('public-search-and-policies', () => {
  test.beforeEach(async ({ page }) => {
    await closeWelcomeModal(page);
  });

  test('discovered policies list opens a policy detail with a title', async ({ page }) => {
    const discovered = page.getByRole('region', { name: 'Discovered policies' });
    await expect(discovered).toBeVisible();

    const firstPolicyButton = discovered.locator('.saved-policy-header').first();
    const title = firstPolicyButton.locator('.saved-policy-name');
    await expect(firstPolicyButton).toBeVisible();
    await expect(title).toBeVisible();

    await firstPolicyButton.scrollIntoViewIfNeeded();
    await firstPolicyButton.click();

    await expect(firstPolicyButton).toHaveAttribute('aria-expanded', 'true');
    await expect(title).toBeVisible();
    await expect(discovered.getByRole('heading', { name: 'Key Information' }).first())
      .toBeVisible();

    await page.screenshot({
      path: 'test-results/screens/02-public-search-and-policies.png',
      fullPage: false,
    });
  });
});

// 3. public-tip-form
// Source: frontend/src/components/LeadsInbox.js renders
//   <section class="leads-inbox" aria-label="Tips inbox">, isExpanded defaults
//   to true so the form is visible on load with no click needed:
//   <form class="leads-suggest-form">
//     <input aria-label="Policy URL" type="url" .../>
//     <input aria-label="Note" type="text" .../>
//     <button type="submit">Suggest a tip</button>
test.describe('public-tip-form', () => {
  test.beforeEach(async ({ page }) => {
    await closeWelcomeModal(page);
  });

  test('tip submission form fields render', async ({ page }) => {
    const tipsInbox = page.getByRole('region', { name: 'Tips inbox' });
    await tipsInbox.scrollIntoViewIfNeeded();
    await expect(tipsInbox).toBeVisible();

    await expect(tipsInbox.getByRole('textbox', { name: 'Policy URL' })).toBeVisible();
    await expect(tipsInbox.getByRole('textbox', { name: 'Note' })).toBeVisible();
    await expect(tipsInbox.getByRole('button', { name: 'Suggest a tip' })).toBeVisible();
    // Do NOT submit - this is a read-only render check.

    await page.screenshot({ path: 'test-results/screens/03-public-tip-form.png', fullPage: false });
  });
});

// 4. admin-scan-panel
// Source: frontend/src/components/ModeSelector.js renders the Standard card
//   with <span class="mode-badge">Recommended</span>. RegionSelector.js
//   renders a MUI SimpleTreeView (role="tree"); the Nordic group is a
//   role="treeitem" whose checkbox carries no accessible name of its own -
//   same pattern as help-content.spec.js / estimate-journey.spec.js.
//   DomainScanPanel.js renders <p class="scan-scope-summary">Scanning: ...
//   and, once a cost breakdown exists, <details class="cost-breakdown"><summary>
//   Why this price?</summary> and <details class="scope-preview"><summary>
//   Where will this search?</summary>, plus a nested "See it as a picture"
//   note wrapping CostFunnelDiagram.js's <svg role="img" aria-label="A flow
//   from ...">.
test.describe('admin-scan-panel', () => {
  test.beforeEach(async ({ page }) => {
    await openAdmin(page);
  });

  test('mode cards, scope tree, cost breakdown and scope preview render', async ({ page }) => {
    await expect(page.locator('span.mode-badge', { hasText: 'Recommended' })).toBeVisible();

    await expect(page.getByRole('tree')).toBeVisible();
    const nordicItem = page.getByRole('treeitem', { name: /Nordic/i }).first();
    await nordicItem.scrollIntoViewIfNeeded();
    await nordicItem.locator('input[type="checkbox"]').first().check();

    const scopeSummary = page.getByText(/^Scanning:/);
    await expect(scopeSummary).toContainText('$', { timeout: 10_000 });

    const priceBreakdown = page.locator('details.cost-breakdown');
    await priceBreakdown.locator('summary', { hasText: 'Why this price?' }).click();
    await expect(priceBreakdown.getByText(/\$\d+\.\d{2}/).first()).toBeVisible();

    const scopePreview = page.locator('details.scope-preview');
    await scopePreview.locator('summary', { hasText: 'Where will this search?' }).click();
    await expect(scopePreview.getByText(/\d+ sources? total/)).toBeVisible();

    // The funnel SVG sits inside the NESTED "See it as a picture" note
    // within Why-this-price - it must be opened before the SVG is visible.
    await priceBreakdown
      .locator('summary', { hasText: 'See it as a picture' })
      .click();
    await expect(page.getByRole('img', { name: /^A flow from/ })).toBeVisible();

    await page.screenshot({ path: 'test-results/screens/04-admin-scan-panel.png', fullPage: false });
  });
});

// 5. admin-visibility
// Source: frontend/src/components/PublicVisibilityControl.js renders
//   <div role="radiogroup" aria-label="Public visibility"> with three
//   <label class="public-visibility-option"> rows, each holding a
//   <span class="public-visibility-option-label"> and a
//   <span class="public-visibility-option-hint"> as separate lines.
test.describe('admin-visibility', () => {
  test.beforeEach(async ({ page }) => {
    await openAdmin(page);
  });

  test('three visibility options render as separate lines with hints', async ({ page }) => {
    const visibility = page.getByRole('radiogroup', { name: 'Public visibility' });
    await visibility.scrollIntoViewIfNeeded();
    await expect(visibility).toBeVisible();

    const options = visibility.locator('.public-visibility-option');
    await expect(options).toHaveCount(3);
    for (let i = 0; i < 3; i += 1) {
      // eslint-disable-next-line no-await-in-loop
      await expect(options.nth(i).locator('.public-visibility-option-label')).toBeVisible();
      // eslint-disable-next-line no-await-in-loop
      await expect(options.nth(i).locator('.public-visibility-option-hint')).toBeVisible();
    }

    await page.screenshot({ path: 'test-results/screens/05-admin-visibility.png', fullPage: false });
  });
});

// 6. admin-review-queue
// Source: frontend/src/components/ReviewInbox.js renders
//   <section class="review-inbox" aria-label="Review queue"><h2>New finds to
//   review{(n)}</h2> and, only once there is at least one pending item,
//   <label class="review-early-first-toggle"><input type="checkbox"/>Early
//   signals first</label>. If the queue is empty the toggle does not render
//   (the component short-circuits to an "all caught up" message) - this test
//   assumes real, unreviewed local data, matching how the rest of this repo's
//   e2e specs assume real backend data.
test.describe('admin-review-queue', () => {
  test.beforeEach(async ({ page }) => {
    await openAdmin(page);
  });

  test('review inbox heading and Early signals first toggle', async ({ page }) => {
    const reviewInbox = page.getByRole('region', { name: 'Review queue' });
    await reviewInbox.scrollIntoViewIfNeeded();
    await expect(reviewInbox.getByRole('heading', { name: /New finds to review/ })).toBeVisible();

    const earlyFirstToggle = reviewInbox.getByRole('checkbox', { name: 'Early signals first' });
    await expect(earlyFirstToggle).toBeVisible();
    await earlyFirstToggle.check();
    await expect(earlyFirstToggle).toBeChecked();

    await page.screenshot({ path: 'test-results/screens/06-admin-review-queue.png', fullPage: false });
  });
});

// 7. admin-library
// Source: frontend/src/components/LibraryView.js renders
//   <section class="library-view" aria-label="Library"><h2>Library -
//   everything in the database</h2> and, once rows exist,
//   <div class="library-table-wrap"><table class="library-table"> with
//   column headers Name/Jurisdiction/Stage/Status/Score/Source/Discovered.
test.describe('admin-library', () => {
  test.beforeEach(async ({ page }) => {
    await openAdmin(page);
  });

  test('library table renders with column headers', async ({ page }) => {
    const library = page.getByRole('region', { name: 'Library' });
    await library.scrollIntoViewIfNeeded();
    await expect(library.getByRole('heading', { name: /Library - everything in the database/ }))
      .toBeVisible();

    const tableWrap = library.locator('.library-table-wrap');
    await expect(tableWrap.locator('table.library-table')).toBeVisible();
    await expect(tableWrap.getByRole('columnheader', { name: 'Name' })).toBeVisible();
    await expect(tableWrap.getByRole('columnheader', { name: 'Jurisdiction' })).toBeVisible();

    await page.screenshot({ path: 'test-results/screens/07-admin-library.png', fullPage: false });
  });
});

// 8. admin-cost-planner
// Source: frontend/src/components/CostPlanner.js renders
//   <div class="cost-planner" aria-label="Cost planner"> (a plain div, not a
//   region-role element) with <h2>Cost planner</h2>, a
//   <details class="cost-planner-help-note"> HelpNote, a
//   <select id="cost-planner-scope-a" multiple> scope multiselect, and a
//   "Recent scans" <h3> followed by either <p class="recent-scans-empty">
//   or a <table class="recent-scans-table">.
test.describe('admin-cost-planner', () => {
  test.beforeEach(async ({ page }) => {
    await openAdmin(page);
  });

  test('cost planner heading, help note, recent scans and scope multiselect render', async ({ page }) => {
    const costPlanner = page.locator('.cost-planner');
    await costPlanner.scrollIntoViewIfNeeded();
    await expect(costPlanner.getByRole('heading', { name: 'Cost planner', level: 2 })).toBeVisible();
    await expect(costPlanner.locator('details.cost-planner-help-note')).toBeVisible();
    await expect(costPlanner.getByRole('heading', { name: 'Recent scans', level: 3 })).toBeVisible();

    const recentScansEmpty = costPlanner.locator('.recent-scans-empty');
    const recentScansTable = costPlanner.locator('.recent-scans-table');
    await expect(recentScansEmpty.or(recentScansTable)).toBeVisible();

    await expect(costPlanner.locator('#cost-planner-scope-a')).toBeVisible();

    await page.screenshot({ path: 'test-results/screens/08-admin-cost-planner.png', fullPage: false });
  });
});

// 9. admin-sources
// Source: frontend/src/components/SourcesPanel.js renders
//   <div class="sources-panel" aria-label="Sources panel"> with
//   <h2>Sources - what PolicyPulse watches</h2>, a
//   <details class="sources-help-note"> HelpNote, a search
//   <input id="sources-search"> labeled "Search sources", and
//   <div class="admin-table-wrap"><table class="sources-table">.
test.describe('admin-sources', () => {
  test.beforeEach(async ({ page }) => {
    await openAdmin(page);
  });

  test('sources heading, help note, search input and table wrap render', async ({ page }) => {
    const sourcesPanel = page.locator('.sources-panel');
    await sourcesPanel.scrollIntoViewIfNeeded();
    await expect(
      sourcesPanel.getByRole('heading', { name: 'Sources - what PolicyPulse watches', level: 2 }),
    ).toBeVisible();
    await expect(sourcesPanel.locator('details.sources-help-note')).toBeVisible();
    await expect(sourcesPanel.getByRole('textbox', { name: 'Search sources' })).toBeVisible();
    await expect(sourcesPanel.locator('.admin-table-wrap table.sources-table')).toBeVisible();

    await page.screenshot({ path: 'test-results/screens/09-admin-sources.png', fullPage: false });
  });
});

// 10. admin-keywords
// Source: frontend/src/components/KeywordsPanel.js renders
//   <div class="keywords-panel" aria-label="Keywords panel"> with
//   <h2>Keywords - what counts as relevant</h2>, a
//   <details class="keywords-help-note"> HelpNote, and one
//   <details class="keyword-category"><summary>{category} (weight N)</summary>
//   row per category.
test.describe('admin-keywords', () => {
  test.beforeEach(async ({ page }) => {
    await openAdmin(page);
  });

  test('keywords heading, help note and a category details row render', async ({ page }) => {
    const keywordsPanel = page.locator('.keywords-panel');
    await keywordsPanel.scrollIntoViewIfNeeded();
    await expect(
      keywordsPanel.getByRole('heading', { name: 'Keywords - what counts as relevant', level: 2 }),
    ).toBeVisible();
    await expect(keywordsPanel.locator('details.keywords-help-note')).toBeVisible();

    const categoryRows = keywordsPanel.locator('details.keyword-category');
    await expect(categoryRows.first()).toBeVisible();
    expect(await categoryRows.count()).toBeGreaterThan(0);

    await page.screenshot({ path: 'test-results/screens/10-admin-keywords.png', fullPage: false });
  });
});

// 11. admin-schedules
// Source: frontend/src/components/SchedulesPanel.js renders
//   <div class="schedules-panel" aria-label="Schedules panel"> with
//   <h2>Schedules - in-app scheduled scans</h2>, a top
//   <details class="schedules-top-help-note"> HelpNote, an
//   <h3>New schedule</h3> form (editingId starts null, so it always reads
//   "New schedule" on a fresh load) with labeled fields Schedule name/Scope
//   (domains/group)/Cadence type/Day/Monthly ceiling (USD), and a second
//   <details class="ceiling-help-note"> HelpNote next to the ceiling field.
test.describe('admin-schedules', () => {
  test.beforeEach(async ({ page }) => {
    await openAdmin(page);
  });

  test('schedules heading, both help notes and new schedule form fields render', async ({ page }) => {
    const schedulesPanel = page.locator('.schedules-panel');
    await schedulesPanel.scrollIntoViewIfNeeded();
    await expect(
      schedulesPanel.getByRole('heading', { name: 'Schedules - in-app scheduled scans', level: 2 }),
    ).toBeVisible();
    await expect(schedulesPanel.locator('details.schedules-top-help-note')).toBeVisible();

    await expect(schedulesPanel.getByRole('heading', { name: 'New schedule', level: 3 })).toBeVisible();
    await expect(schedulesPanel.getByRole('textbox', { name: 'Schedule name' })).toBeVisible();
    await expect(schedulesPanel.getByRole('textbox', { name: 'Scope (domains/group)' })).toBeVisible();
    await expect(schedulesPanel.getByRole('combobox', { name: 'Cadence type' })).toBeVisible();
    await expect(schedulesPanel.getByRole('combobox', { name: 'Day' })).toBeVisible();
    await expect(schedulesPanel.getByRole('spinbutton', { name: 'Monthly ceiling (USD)' }))
      .toBeVisible();
    await expect(schedulesPanel.locator('details.ceiling-help-note')).toBeVisible();

    await page.screenshot({ path: 'test-results/screens/11-admin-schedules.png', fullPage: false });
  });
});

// 12. admin-notifications
// Source: frontend/src/components/NotificationsPanel.js renders
//   <div class="notifications-panel" aria-label="Notifications panel"> with
//   <h2>Email notifications</h2>, a <details class="notifications-help-note">
//   HelpNote, an add form (Email address input, a Topics fieldset with Early
//   signals/Operational alerts checkboxes, a How often select), either
//   <p class="notifications-empty">Nobody is subscribed yet.</p> or a
//   <table class="notifications-table">, and - whenever GET
//   /api/notifications/status reports smtp_configured: false, true locally -
//   a <p class="notifications-smtp-note">Email sending is not set up yet...</p>.
test.describe('admin-notifications', () => {
  test.beforeEach(async ({ page }) => {
    await openAdmin(page);
  });

  test('email notifications heading, help note, add form and not-set-up note render', async ({ page }) => {
    const notificationsPanel = page.locator('.notifications-panel');
    await notificationsPanel.scrollIntoViewIfNeeded();
    await expect(
      notificationsPanel.getByRole('heading', { name: 'Email notifications', level: 2 }),
    ).toBeVisible();
    await expect(notificationsPanel.locator('details.notifications-help-note')).toBeVisible();

    await expect(notificationsPanel.getByRole('textbox', { name: 'Email address' })).toBeVisible();
    await expect(notificationsPanel.getByRole('checkbox', { name: 'Early signals' })).toBeVisible();
    await expect(notificationsPanel.getByRole('checkbox', { name: 'Operational alerts' }))
      .toBeVisible();
    await expect(notificationsPanel.getByRole('combobox', { name: 'How often' })).toBeVisible();

    const emptyState = notificationsPanel.locator('.notifications-empty');
    const rows = notificationsPanel.locator('table.notifications-table');
    await expect(emptyState.or(rows)).toBeVisible();

    await expect(notificationsPanel.locator('.notifications-smtp-note'))
      .toContainText('Email sending is not set up yet');

    await page.screenshot({
      path: 'test-results/screens/12-admin-notifications.png',
      fullPage: false,
    });
  });
});

// 13. admin-how-it-works
// Source: frontend/src/components/HowItWorksPanel.js renders
//   <div class="how-it-works-panel" aria-label="How PolicyPulse works">
//   wrapping a single top-level HelpNote,
//   <details class="how-it-works-note"><summary>How PolicyPulse works - from
//   government website to the public map</summary>, whose body lists all
//   eight stages ("Gather.", "Keyword screen.", "Fast AI pass.", "Full AI
//   read.", "Automatic checks.", "Save and record.", "Human review.", "The
//   public map.") each in its own <strong>, and nests a second HelpNote,
//   <details class="how-it-works-diagram-note"><summary>See it as a
//   picture</summary>, wrapping <svg role="img" aria-label="A policy flows
//   from gathering ...">.
test.describe('admin-how-it-works', () => {
  test.beforeEach(async ({ page }) => {
    await openAdmin(page);
  });

  test('How PolicyPulse works note opens with all eight stages and its diagram', async ({ page }) => {
    const howItWorks = page.locator('.how-it-works-panel');
    await howItWorks.scrollIntoViewIfNeeded();

    const mainNote = howItWorks.locator('details.how-it-works-note');
    await mainNote.locator('summary', { hasText: 'How PolicyPulse works' }).click();

    const stageNames = [
      'Gather.', 'Keyword screen.', 'Fast AI pass.', 'Full AI read.',
      'Automatic checks.', 'Save and record.', 'Human review.', 'The public map.',
    ];
    for (const name of stageNames) {
      // eslint-disable-next-line no-await-in-loop
      await expect(mainNote.getByText(name, { exact: true })).toBeVisible();
    }

    const diagramNote = mainNote.locator('details.how-it-works-diagram-note');
    await diagramNote.locator('summary', { hasText: 'See it as a picture' }).click();
    await expect(page.getByRole('img', { name: /^A policy flows from gathering/ })).toBeVisible();

    await page.screenshot({
      path: 'test-results/screens/13-admin-how-it-works.png',
      fullPage: false,
    });
  });
});
