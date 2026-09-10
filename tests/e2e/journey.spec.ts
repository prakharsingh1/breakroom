import { test, expect } from '../../apps/web/node_modules/@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
const screenshots = path.resolve(__dirname, '../../output/playwright');

test('real flagship effects, correction, compatible comparison and executable export', async ({ page }) => {
  await page.goto('/demo');
  await expect(page.getByText('No run yet', { exact: true })).toBeVisible();
  const faultyResponse = page.waitForResponse(r => r.url().endsWith('/api/runs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '▶ Run faulty reference', exact: true }).click();
  const faulty = await (await faultyResponse).json();
  expect(faulty.report.execution.status).toBe('completed');
  expect(faulty.report.verdict).toBe('FAIL');
  expect(faulty.report.final_state.refunds).toHaveLength(2);
  expect(faulty.report.final_state.refunds.reduce((n: number, r: {amount_minor:number}) => n + r.amount_minor, 0)).toBe(200000);
  await expect(page.getByTestId('refund-total')).toHaveText('₹2,000');
  await expect(page.getByText('Newly executed run', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Corrected reference', exact: true }).click();
  const correctedResponse = page.waitForResponse(r => r.url().endsWith('/api/runs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '▶ Run corrected reference', exact: true }).click();
  const corrected = await (await correctedResponse).json();
  expect(corrected.report.verdict).toBe('PASS');
  expect(corrected.report.final_state.refunds).toHaveLength(1);
  await expect(page.getByTestId('refund-total')).toHaveText('₹1,000');
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Export runnable test ↓', exact: true }).click();
  const download = await downloadPromise;
  fs.mkdirSync(screenshots, { recursive: true });
  await download.saveAs(path.join(screenshots, 'breakroom-regression.zip'));
  expect(fs.statSync(path.join(screenshots, 'breakroom-regression.zip')).size).toBeGreaterThan(500);
  await page.getByRole('link', { name: 'Compare runs →', exact: true }).click();
  await expect(page.getByText('Compatible evaluation contracts')).toBeVisible();
  await expect(page.getByTestId('refund-total')).toHaveText(['₹2,000', '₹1,000']);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(screenshots, 'compare-desktop.png'), fullPage: true });
  await page.goto('/runs');
  await expect(page.getByText('Saved recordings from this browser.', { exact: false })).toBeVisible();
  await expect(page.locator('.saved-run')).toHaveCount(2);
  await page.locator('.saved-run').first().click();
  await expect(page.getByText('SAVED BROWSER RECORDING / SCRIPTED REFERENCE')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Ordered event log' })).toBeVisible();
});

test('library, contract, docs command copy, pricing and keyboard access', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Fire Drills' }).click();
  await expect(page.locator('.drill-row')).toHaveCount(24);
  await page.getByRole('link', { name: /The Missing Response/ }).click();
  await expect(page.getByRole('heading', { name: 'Independent assertions' })).toBeVisible();
  await page.goto('/fire-drills/pending-refund-succeeds');
  await expect(page.getByRole('heading', { name: 'Run this drill locally' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Run faulty reference/ })).toHaveCount(0);
  await expect(page.getByText('pending_observed')).toBeVisible();
  await page.goto('/docs');
  await page.getByRole('button', { name: /Copy command: python3/ }).click();
  await expect(page.getByRole('button', { name: /Copy command: python3/ })).toHaveText('Copied');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('python -m pip install -e ./packages/breakroom-core');
  await page.goto('/pricing');
  await expect(page.getByText('Checkout unavailable — no billing configured')).toBeVisible();
  await expect(page.getByRole('button', { name: /checkout|subscribe/i })).toHaveCount(0);
});

test('expanded local drill contracts remain readable across viewport sizes', async ({ page }) => {
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/fire-drills/pending-refund-succeeds');
    await expect(page.getByRole('heading', { name: 'Run this drill locally' })).toBeVisible();
    await expect(page.getByText('Supported seeds: 0, 1, 2.')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: path.join(screenshots, 'local-drill-' + width + '.png'), fullPage: true });
  }
});

test('honest offline and expired-evidence states', async ({ page }) => {
  await page.route('**/api/runs', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'The demo service is offline.' }) }));
  await page.goto('/demo');
  await page.getByRole('button', { name: '▶ Run faulty reference', exact: true }).click();
  await expect(page.locator('main').getByRole('alert')).toContainText('offline');
  await expect(page.getByTestId('refund-total')).toHaveCount(0);
  await page.goto('/runs/not-a-real-run');
  await expect(page.getByRole('heading', { name: 'Run unavailable' })).toBeVisible();
  await expect(page.locator('main').getByRole('alert')).toContainText('expired');
});

test('unknown effects remain unknown and expired exports explain the error', async ({ page }) => {
  await page.route('**/api/runs', async route => {
    const response = await route.fetch();
    const envelope = await response.json();
    envelope.report.final_state = { available: false, reason: 'Worker state was unavailable' };
    envelope.report.verdict = 'INCONCLUSIVE';
    envelope.report.execution.status = 'errored';
    envelope.report.checks.forEach((check: {status: string}) => { check.status = 'unknown'; });
    await route.fulfill({ response, json: envelope });
  });
  await page.goto('/demo');
  await page.getByRole('button', { name: '▶ Run faulty reference', exact: true }).click();
  await expect(page.getByTestId('refund-total')).toHaveText('Unknown');
  await expect(page.getByText('No amount or effect count can be established.', { exact: false })).toBeVisible();
  await page.route('**/api/runs/*/export', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"This demo run is unknown or expired. Execute a new run."}' }));
  await page.getByRole('link', { name: 'Export runnable test ↓', exact: true }).click();
  await expect(page.locator('main').getByRole('alert')).toContainText('expired');
  await expect(page).toHaveURL(/\/demo$/);
});

for (const width of [390, 768, 1440]) {
  test('responsive screenshot and real run at ' + width, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/');
    await expect(page.locator('.drill-row')).toHaveCount(3);
    await expect(page.getByRole('heading', { level: 1 })).toContainText('Break your agent.');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    fs.mkdirSync(screenshots, { recursive: true });
    await page.screenshot({ path: path.join(screenshots, 'home-' + width + '.png'), fullPage: true });
    await page.goto('/demo');
    await page.getByRole('button', { name: '▶ Run faulty reference', exact: true }).click();
    await expect(page.getByTestId('refund-total')).toHaveText('₹2,000');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: path.join(screenshots, 'demo-' + width + '.png'), fullPage: true });
  });
}
