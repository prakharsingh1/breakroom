import { test, expect } from '../../apps/web/node_modules/@playwright/test';
import { spawn, ChildProcess } from 'node:child_process';
import path from 'node:path';
import fs from 'node:fs';
import { checkAccessibility } from './accessibility';
const root = path.resolve(__dirname, '../..');
test.skip(process.env.BREAKROOM_SANDBOX_E2E !== '1', 'Requires an explicitly configured isolated worker and encrypted source storage');
let worker: ChildProcess;
test.beforeAll(async () => {
  const python = process.env.BREAKROOM_TEST_PYTHON || path.join(root, 'venv-team/bin/python');
  worker = spawn(python, ['-m', 'breakroom_api.sandbox.worker'], { cwd: root,
    env: { ...process.env, PYTHONPATH: [path.join(root, 'apps/api'), path.join(root, 'packages/breakroom-core/src')].join(path.delimiter), BREAKROOM_TEAM_ENV: 'test', BREAKROOM_TEAM_DB_SCHEMA: process.env.CI ? 'browser_tests' : 'public', BREAKROOM_TEAM_DEV_LOGIN: '1', BREAKROOM_TEAM_PUBLIC_ORIGIN: 'http://127.0.0.1:3000' }, stdio: 'ignore' });
});
test.afterAll(async () => {
  if (worker && worker.exitCode === null) {
    worker.kill('SIGTERM');
    await Promise.race([new Promise(resolve => worker.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 12000))]);
    if (worker.exitCode === null) worker.kill('SIGKILL');
  }
});

test('upload an agent ZIP, execute isolated trials and inspect actual effects', async ({ page }) => {
  test.setTimeout(180000);
  const suffix = Date.now();
  await page.goto('/projects');
  await page.getByText('Developer test sign-in', { exact: true }).click();
  await page.getByLabel('Email for this local identity').fill('sandbox-'+suffix+'@example.invalid');
  await page.getByRole('button', { name: 'Use local development identity' }).click();
  await expect(page.getByRole('heading', { name: 'Create a private project' })).toBeVisible();
  const session = await (await page.request.get('/api/team/me')).json();
  const headers = { Origin: 'http://127.0.0.1:3000', 'X-CSRF-Token': session.csrf_token };
  const created = await page.request.post('/api/team/projects', { headers, data: { name: 'Agent sandbox QA', retention_days: 1 } });
  expect(created.status()).toBe(201);
  const project = await created.json(); const base = '/api/team/projects/'+project.id+'/sandbox';
  try {
    await expect.poll(async () => (await (await page.request.get(base)).json()).available, { timeout: 30000 }).toBe(true);
    await page.goto('/projects/'+project.id);
    await page.getByRole('button', { name: 'Agent sandbox', exact: true }).click();
    await expect(page.getByText('Worker available', { exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'No sandbox runs yet.' })).toBeVisible();
    await checkAccessibility(page, 'sandbox-empty');
    const downloadEvent = page.waitForEvent('download');
    await page.getByRole('link', { name: 'Download starter ZIP' }).click();
    const zip = await downloadEvent;
    const zipPath = path.join(root,'output/playwright/sandbox-starter.zip'); await zip.saveAs(zipPath);
    expect(fs.statSync(zipPath).size).toBeGreaterThan(1000);
    await page.getByLabel('Agent name', {exact:true}).fill('Refund assistant corrected');
    await page.getByLabel('Agent ZIP', {exact:true}).setInputFiles(zipPath);
    await page.getByRole('button',{name:'Save agent version',exact:true}).click();
    await expect(page.getByRole('status').filter({hasText:'Agent version saved.'})).toBeVisible();
    await page.getByRole('button',{name:'Stress: 72 trials',exact:true}).click();
    await expect(page.locator('.sandbox-plan strong')).toHaveText('72 trials');
    await page.getByLabel('Repetitions',{exact:true}).fill('3');
    await expect(page.getByRole('button',{name:'Start sandbox run →',exact:true})).toBeDisabled();
    await page.getByRole('button',{name:'Quick check',exact:true}).click();
    await page.getByRole('button',{name:'Start sandbox run →',exact:true}).click();
    await expect(page.locator('.sandbox-run-row')).toHaveCount(1);
    await expect(page.locator('.sandbox-investigator').getByRole('status')).toHaveText('1 / 1 trials recorded · completed',{timeout:60000});
    await expect(page.locator('.sandbox-run-row .status')).toContainText('PASS');
    await expect(page.getByTestId('refund-total')).toHaveText('₹1,000');
    await expect(page.locator('.refund-record')).toHaveCount(1);
    await expect(page.locator('.sandbox-investigator')).toContainText('Model access disabled');
    const downloadRun = page.waitForEvent('download');
    await page.getByRole('button',{name:'Download run evidence',exact:true}).click();
    const reportDownload = await downloadRun; const reportPath = path.join(root,'output/playwright/sandbox-run.json'); await reportDownload.saveAs(reportPath);
    const report = JSON.parse(fs.readFileSync(reportPath,'utf8'));
    expect(report.verdict).toBe('PASS'); expect(report.trials[0].report.final_state.refunds).toHaveLength(1);
    for (const width of [390,768,1440]) {
      await page.setViewportSize({width,height:1000});
      await page.evaluate(() => window.scrollTo(0,0));
      expect(await page.evaluate(() => document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
      await checkAccessibility(page,'sandbox-evidence-'+width);
      await page.screenshot({path:path.join(root,'output/playwright/sandbox-'+width+'.png'),fullPage:true});
      await page.locator('.sandbox-intro').screenshot({path:path.join(root,'output/playwright/sandbox-intro-'+width+'.png')});
      await page.locator('.evidence-pair').screenshot({path:path.join(root,'output/playwright/sandbox-evidence-'+width+'.png')});
    }
    await page.getByRole('combobox',{name:'Agent source',exact:true}).selectOption('github');
    await expect(page.getByLabel('GitHub repository',{exact:true})).toBeVisible();
    await expect(page.getByLabel('Commit SHA',{exact:true})).toHaveAttribute('minlength','40');
    await page.getByText('Model provider keys · 0 configured',{exact:true}).click();
    await page.getByLabel('Provider API key',{exact:true}).fill('fixture-not-a-real-provider-key-'+suffix);
    await page.getByRole('button',{name:'Save provider key',exact:true}).click();
    await expect(page.getByLabel('Provider API key',{exact:true})).toHaveValue('');
    await expect(page.getByRole('button',{name:'Revoke openai key',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Revoke openai key',exact:true}).click();
    await expect(page.getByText('Model provider keys · 0 configured',{exact:true})).toBeVisible();
  } finally {
    expect((await page.request.delete('/api/team/projects/'+project.id,{headers})).status()).toBe(200);
  }
});
