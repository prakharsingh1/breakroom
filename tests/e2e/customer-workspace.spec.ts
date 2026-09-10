import { test, expect, Page } from '../../apps/web/node_modules/@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { checkAccessibility } from './accessibility';

const root = path.resolve(__dirname, '../..');
const output = path.join(root, 'artifacts/customer-workspace-browser');
const python = process.env.BREAKROOM_TEST_PYTHON || path.join(root, 'venv/bin/python');
test.skip(process.env.BREAKROOM_TEAM_E2E !== '1', 'Requires the actual local PostgreSQL team service.');
test.beforeAll(() => {
  fs.mkdirSync(output, { recursive: true });
  execFileSync(python, ['-m', 'breakroom', 'demo', '--output', path.join(output, 'source')], { cwd: root, stdio: 'pipe' });
  for (const agent of ['faulty', 'corrected']) execFileSync(python, ['-m', 'breakroom', 'prepare-upload', path.join(output, 'source', agent), '--case', 'refund-response-lost', '--redaction-key-file', path.join(output, 'redaction.key'), '--out', path.join(output, agent + '.json')], { cwd: root, stdio: 'pipe' });
});
async function login(page: Page, email: string) {
  await page.goto('/projects');
  await page.getByText('Developer test sign-in', { exact: true }).click();
  await page.getByLabel('Email for this local identity').fill(email);
  await page.getByRole('button', { name: 'Use local development identity' }).click();
  await expect(page.getByRole('heading', { name: 'Create a private project' })).toBeVisible();
}

test('customer workspace guides setup, derives real suite checks, filters reports and accepts a private invitation', async ({ page, browser }) => {
  test.setTimeout(150_000);
  const suffix = Date.now();
  const name = 'Customer workspace ' + suffix;
  const inviteEmail = 'workspace-member-' + suffix + '@example.invalid';
  await login(page, 'workspace-owner-' + suffix + '@example.invalid');
  const me = await (await page.request.get('/api/team/me')).json();
  const headers = { Origin: 'http://127.0.0.1:3000', 'X-CSRF-Token': me.csrf_token };
  const created = await page.request.post('/api/team/projects', { headers, data: { name, retention_days: 30 } });
  expect(created.status()).toBe(201);
  const project = await created.json();
  const base = '/api/team/projects/' + project.id;
  const teammate = await browser.newContext({ baseURL: 'http://127.0.0.1:3000' });
  try {
    await page.goto('/projects/' + project.id);
    await expect(page.getByRole('heading', { name: 'Bring your first test result.' })).toBeVisible();
    expect(await page.locator('.project-report-row').count()).toBe(0);
    await page.getByRole('button', { name: 'Connect your agent →', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Connect your agent', exact: true })).toBeVisible();
    await expect(page.locator('.command').last()).toContainText(project.id);
    await expect(page.locator('.command').last()).toContainText('--allow-localhost-http');
    await checkAccessibility(page, 'customer-connect');
    await page.getByRole('button', { name: 'Private suites', exact: true }).click();
    await expect(page.locator('.drill-choice')).toHaveCount(24);
    await page.getByLabel('Release suite name').fill('Required refund recovery');
    await page.getByLabel('Find a Fire Drill').fill('Missing Response');
    await expect(page.locator('.drill-choice')).toHaveCount(1);
    await page.locator('.drill-choice input').check();
    await page.getByRole('button', { name: 'Save release suite', exact: true }).click();
    const suite = page.locator('.release-suite').filter({ has: page.getByRole('heading', { name: 'Required refund recovery', exact: true }) });
    await expect(suite).toContainText('INCONCLUSIVE');
    await expect(suite).toContainText('MISSING');
    await checkAccessibility(page, 'customer-suite-missing');
    for (const agent of ['faulty', 'corrected']) {
      await page.getByRole('button', { name: 'Reports', exact: true }).click();
      await page.getByLabel('Prepared JSON report').setInputFiles(path.join(output, agent + '.json'));
      await page.getByRole('button', { name: 'Upload reviewed report', exact: true }).click();
      await expect(page.locator('.project-report-row')).toHaveCount(agent === 'faulty' ? 1 : 2);
      await page.getByRole('button', { name: 'Private suites', exact: true }).click();
      await expect(suite).toContainText(agent === 'faulty' ? '1 failing' : '1 passing');
    }
    await page.getByRole('button', { name: 'Reports', exact: true }).click();
    await page.getByRole('combobox', { name: 'Report verdict', exact: true }).selectOption('FAIL');
    await expect(page.locator('.project-report-row')).toHaveCount(1);
    await expect(page.locator('.project-report-row')).toContainText('FAIL');
    await page.getByLabel('Search reports', { exact: true }).fill('nothing-matches-this');
    await expect(page.getByRole('heading', { name: 'No reports match these filters.' })).toBeVisible();
    await page.getByRole('button', { name: 'Clear report filters' }).click();
    await expect(page.locator('.project-report-row')).toHaveCount(2);
    await page.getByRole('combobox', { name: 'Sort reports', exact: true }).selectOption('oldest');
    await expect(page.locator('.project-report-row').first()).toContainText('FAIL');
    await page.getByRole('combobox', { name: 'Sort reports', exact: true }).selectOption('newest');
    await expect(page.locator('.project-report-row').first()).toContainText('PASS');
    await page.getByRole('button', { name: 'Private suites', exact: true }).click();
    await page.getByLabel('Release suite name').fill('All required scenarios');
    await page.getByRole('button', { name: 'Select all 24 drills' }).click();
    await page.getByRole('button', { name: 'Save release suite', exact: true }).click();
    const allSuite = page.locator('.release-suite').filter({ has: page.getByRole('heading', { name: 'All required scenarios', exact: true }) });
    await expect(allSuite).toContainText('INCONCLUSIVE');
    await expect(allSuite).toContainText('23 missing');
    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.evaluate(() => window.scrollTo(0, 0));
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await checkAccessibility(page, 'customer-suites-' + width);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: path.join(root, 'output/playwright/customer-suites-' + width + '.png'), fullPage: true });
    }
    await page.getByRole('button', { name: 'Overview', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Project overview', exact: true })).toBeVisible();
    const counts = page.getByLabel('Retained report counts');
    await expect(counts).toContainText('2Reports retained');
    await expect(counts).toContainText('1Passing reports');
    await expect(counts).toContainText('1Failing reports');
    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await checkAccessibility(page, 'customer-overview-' + width);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: path.join(root, 'output/playwright/customer-overview-' + width + '.png'), fullPage: true });
    }
    await page.getByRole('button', { name: 'Members', exact: true }).click();
    await page.getByLabel('Invite email', { exact: true }).fill(inviteEmail);
    await page.getByRole('button', { name: 'Create invitation link', exact: true }).click();
    const invite = page.getByLabel('New invitation link', { exact: true });
    await expect(invite).toBeVisible();
    const url = await invite.inputValue();
    expect(new URL(url).pathname).toBe('/invite');
    expect(new URL(url).search).toBe('');
    expect(new URL(url).hash).toMatch(/^#token=.+/);
    await page.getByRole('button', { name: 'Copy invitation link', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Invitation link copied.' })).toBeVisible();
    const invitedPage = await teammate.newPage();
    await invitedPage.goto(url);
    await invitedPage.getByText('Developer test sign-in', { exact: true }).click();
    await invitedPage.getByLabel('Email for this local identity').fill(inviteEmail);
    await invitedPage.getByRole('button', { name: 'Use local development identity' }).click();
    await expect(invitedPage.getByRole('button', { name: 'Accept invitation', exact: true })).toBeVisible();
    await invitedPage.getByRole('button', { name: 'Accept invitation', exact: true }).click();
    await expect(invitedPage.getByRole('heading', { name: 'Invitation accepted' })).toBeVisible();
    expect(new URL(invitedPage.url()).hash).toBe('');
    await invitedPage.getByRole('link', { name: 'Open project →', exact: true }).click();
    await expect(invitedPage.locator('.project-report-row')).toHaveCount(2);
    await expect(invitedPage.getByText('Your viewer role can read reports.', { exact: false })).toBeVisible();
    await page.reload();
    await page.getByRole('button', { name: 'Members', exact: true }).click();
    await expect(page.locator('.invitation-list')).toContainText('Accepted');
    await page.getByLabel('Invite email', { exact: true }).fill('revoked-' + suffix + '@example.invalid');
    await page.getByRole('button', { name: 'Create invitation link', exact: true }).click();
    await expect(page.getByLabel('New invitation link', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Revoke invitation for revoked-' + suffix + '@example.invalid', exact: true }).click();
    await expect(page.locator('.invitation-list')).toContainText('Revoked');
    await expect(page.getByLabel('New invitation link', { exact: true })).toHaveCount(0);
  } finally {
    await teammate.close();
    const removed = await page.request.delete(base, { headers });
    expect(removed.status()).toBe(200);
  }
});
