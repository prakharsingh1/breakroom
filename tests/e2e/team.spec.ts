import { test, expect, Page } from '../../apps/web/node_modules/@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { checkAccessibility } from './accessibility';
const root = path.resolve(__dirname, '../..');
const output = path.join(root, 'artifacts/team-browser');
const python = process.env.BREAKROOM_TEST_PYTHON || path.join(root,'.venv-api/bin/python');

test.beforeAll(() => {
  fs.mkdirSync(output,{recursive:true});
  execFileSync(python,['-m','breakroom','demo','--output',path.join(output,'source')],{cwd:root,stdio:'pipe'});
  for (const agent of ['faulty','corrected']) {
    execFileSync(python,['-m','breakroom','prepare-upload',path.join(output,'source',agent),'--case','refund-response-lost','--redaction-key-file',path.join(output,'redaction.key'),'--out',path.join(output,agent+'.json')],{cwd:root,stdio:'pipe'});
  }
});

async function login(page: Page, email: string) {
  await page.goto('/projects');
  await page.getByText('Developer test sign-in',{exact:true}).click();
  await expect(page.getByText('Local development identity',{exact:true})).toBeVisible();
  await page.getByLabel('Email for this local identity').fill(email);
  await page.getByRole('button',{name:'Use local development identity'}).click();
  await expect(page.getByRole('heading',{name:'Create a private project'})).toBeVisible();
}

test('private team journey uses PostgreSQL, explicit real reports, scoped keys and tenant checks',async ({page,browser}) => {
  test.setTimeout(120_000);
  const suffix=Date.now();
  const projectName='Browser release '+suffix;
  const owner='owner-'+suffix+'@example.invalid';
  const viewer='viewer-'+suffix+'@example.invalid';
  const viewerContext=await browser.newContext({baseURL:'http://127.0.0.1:3000'});
  const viewerPage=await viewerContext.newPage();
  await login(viewerPage,viewer);
  await login(page,owner);
  const creation=page.waitForResponse(r => r.url().endsWith('/api/team/projects') && r.request().method()==='POST');
  await page.getByLabel('Project name',{exact:true}).fill(projectName);
  await page.getByRole('button',{name:'Create project →'}).click();
  const project=await (await creation).json();
  expect(project.role).toBe('owner');
  await page.getByRole('link',{name:new RegExp(projectName)}).click();
  await expect(page.getByRole('heading',{name:'Uploaded reports',exact:true})).toBeVisible();
  const summaries=[];
  for (const agent of ['faulty','corrected']) {
    await page.getByLabel('Prepared JSON report').setInputFiles(path.join(output,agent+'.json'));
    await expect(page.getByText('No data has been uploaded by selecting this file.')).toBeVisible();
    expect(await page.locator('.project-report-row').count()).toBe(summaries.length);
    const upload=page.waitForResponse(r => r.url().endsWith('/reports') && r.request().method()==='POST');
    await page.getByRole('button',{name:'Upload reviewed report'}).click();
    const response=await upload;
    expect(response.status()).toBe(201);
    summaries.push(await response.json());
    await expect(page.locator('.project-report-row')).toHaveCount(summaries.length);
  }
  expect(summaries.map(r => r.verdict)).toEqual(['FAIL','PASS']);
  await page.getByRole('combobox',{name:'Baseline',exact:true}).selectOption(summaries[0].id);
  await page.getByRole('combobox',{name:'Candidate',exact:true}).selectOption(summaries[1].id);
  await page.getByRole('button',{name:'Compare reports',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Compatible report contracts'})).toBeVisible();
  for (const width of [390,768,1440]) {
    await page.setViewportSize({width,height:1000});
    await page.evaluate(() => window.scrollTo(0,0));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({path:path.join(root,'output/playwright/team-project-'+width+'.png'),fullPage:true});
    await checkAccessibility(page,'team-project-'+width);
  }
  await page.locator('.project-report-row').first().click();
  await expect(page.getByText('PRIVATE UPLOAD / CUSTOMER-GENERATED')).toBeVisible();
  await checkAccessibility(page,'team-report');
  const download=page.waitForEvent('download');
  await page.getByRole('button',{name:'Download minimized JSON'}).click();
  const downloaded=await download;
  await downloaded.saveAs(path.join(output,'downloaded.json'));
  expect(JSON.parse(fs.readFileSync(path.join(output,'downloaded.json'),'utf8')).kind).toBe('customer_generated_report');
  await page.getByRole('link',{name:'← Project reports'}).click();
  await page.getByRole('button',{name:'Private suites',exact:true}).click();
  const prepared=JSON.parse(fs.readFileSync(path.join(output,'corrected.json'),'utf8'));
  await page.getByLabel('Suite name',{exact:true}).fill('Private reviewed cases');
  await page.getByLabel('Case metadata (JSON array)').fill(JSON.stringify([{case_id:prepared.report.case.case_id,case_version:prepared.report.case.case_version,manifest_hash:prepared.report.case.manifest_hash}]));
  await page.getByRole('button',{name:'Save private suite'}).click();
  await expect(page.getByRole('heading',{name:'Private reviewed cases'})).toBeVisible();
  await page.getByRole('button',{name:'Members',exact:true}).click();
  await page.getByLabel('Member email',{exact:true}).fill(viewer);
  await page.getByRole('button',{name:'Add member',exact:true}).click();
  await expect(page.getByText('Project membership updated.')).toBeVisible();
  await viewerPage.goto('/projects/'+project.id);
  await expect(viewerPage.locator('.project-report-row')).toHaveCount(2);
  await expect(viewerPage.getByText('Your viewer role can read reports.',{exact:false})).toBeVisible();
  await viewerPage.getByRole('button',{name:'API keys',exact:true}).click();
  await expect(viewerPage.getByText('Only a project owner can manage keys.')).toBeVisible();
  await expect(viewerPage.getByRole('button',{name:'Billing',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'API keys',exact:true}).click();
  await page.getByLabel('Key name',{exact:true}).fill('Browser upload key');
  const keyResponse=page.waitForResponse(r => r.url().endsWith('/keys') && r.request().method()==='POST');
  await page.getByRole('button',{name:'Create scoped key'}).click();
  const key=await (await keyResponse).json();
  expect(key.secret).toBeTruthy();
  await page.getByRole('button',{name:'I’ve saved this key'}).click();
  await expect(page.getByLabel('New API key')).toHaveCount(0);
  await checkAccessibility(page,'team-keys');
  const denied=await page.request.get('/api/team/projects/'+project.id+'/reports',{headers:{Authorization:'Bearer '+key.secret}});
  expect(denied.status()).toBe(403);
  await page.getByRole('button',{name:'Revoke Browser upload key'}).click();
  await expect(page.getByText('Key revoked.',{exact:true})).toBeVisible();
  const revoked=await page.request.get('/api/team/projects/'+project.id+'/reports',{headers:{Authorization:'Bearer '+key.secret}});
  expect(revoked.status()).toBe(401);
  const me=await page.request.get('/api/team/me');
  expect(Object.values(me.headers()).join(' ')).not.toContain('breakroom-local-proxy-development-only');
  await page.getByRole('button',{name:'Billing',exact:true}).click();
  await expect(page.getByText('Checkout unavailable — no billing configured')).toBeVisible();
  await expect(page.getByRole('button',{name:'Open test checkout ↗'})).toHaveCount(0);
  await checkAccessibility(page,'team-billing-disabled');
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByLabel('Report retention (days)').fill('7');
  await page.getByRole('button',{name:'Save retention'}).click();
  await expect(page.getByText('Retention updated.',{exact:true})).toBeVisible();
  await page.getByLabel('Type the project name to confirm').fill(projectName);
  await page.getByRole('button',{name:'Delete project permanently'}).click();
  await expect(page.getByRole('heading',{name:'Project deleted.'})).toBeVisible();
  for (const ending of ['', '/evidence','/export']) {
    const deleted=await viewerPage.request.get('/api/team/projects/'+project.id+'/reports/'+summaries[0].id+ending);
    expect(deleted.status()).toBe(404);
  }
  await viewerContext.close();
});

test('billing interface explains disabled and test states, preserves retries and confirms cancellation',async ({page})=>{
  // Billing API fixtures isolate UI behavior. Provider/auth/entitlement correctness
  // is checked separately by signed-event PostgreSQL tests; no Stripe request occurs.
  await login(page,'billing-ui-'+Date.now()+'@example.invalid');
  const me=await (await page.request.get('/api/team/me')).json();
  const response=await page.request.post('/api/team/projects',{headers:{Origin:'http://127.0.0.1:3000','X-CSRF-Token':me.csrf_token},data:{name:'Billing interface test',retention_days:30}});
  expect(response.status()).toBe(201);
  const project=await response.json();
  const base='/api/team/projects/'+project.id;
  let state={mode:'disabled',configured:false,provider:'stripe',amount_minor:4900,currency:'USD',interval:'month',status:'disabled',access:'local_development',cancel_at_period_end:false,current_period_end:null as string|null,paid_through:null as string|null,can_cancel:false,sync_state:'ready',limits:{seats:25,reports:1000,storage_bytes:268435456},usage:{seats:1,reports:0,storage_bytes:0},checkout_available:false,notice:'Billing is disabled. The local core remains available.'};
  const checkoutKeys:string[]=[];const cancellations:boolean[]=[];
  await page.route('**'+base+'/billing**',async route=>{
    const req=route.request();
    if(req.method()==='GET'){await route.fulfill({json:state});return;}
    expect(req.headers()['x-csrf-token']).toBe(me.csrf_token);
    if(req.url().endsWith('/reconcile')){await route.fulfill({json:state});return;}
    expect(req.headers()['idempotency-key']).toMatch(/^[a-f0-9]{64}$/);
    if(req.url().endsWith('/checkout')){
      expect(req.postDataJSON()).toEqual({plan:'team'});checkoutKeys.push(req.headers()['idempotency-key']);
      await route.fulfill({status:503,json:{detail:'Checkout result is uncertain; retry this exact Idempotency-Key. No access was granted.'}});return;
    }
    const immediate=!req.postDataJSON().at_period_end;cancellations.push(!immediate);
    state={...state,status:immediate?'canceled':'active',access:immediate?'read_only':'paid',can_cancel:!immediate,cancel_at_period_end:!immediate};
    await route.fulfill({json:state});
  });
  try{
    await page.goto('/projects/'+project.id);
    await page.getByRole('button',{name:'Billing',exact:true}).click();
    await expect(page.getByText('Checkout unavailable — no billing configured')).toBeVisible();
    await expect(page.getByRole('button',{name:'Open test checkout ↗'})).toHaveCount(0);
    await checkAccessibility(page,'billing-disabled');
    state={...state,mode:'test',configured:true,status:'none',access:'read_only',checkout_available:true,notice:'A test subscription is required. No live charges are enabled.'};
    await page.getByRole('button',{name:'Refresh billing state'}).click();
    await page.getByRole('button',{name:'Open test checkout ↗'}).click();
    await expect(page.getByRole('alert').filter({hasText:'Checkout result is uncertain'})).toBeVisible();
    await page.reload();
    await page.getByRole('button',{name:'Billing',exact:true}).click();
    await page.getByRole('button',{name:'Open test checkout ↗'}).click();
    await expect.poll(()=>checkoutKeys.length).toBe(2);
    expect(checkoutKeys[0]).toBe(checkoutKeys[1]);
    await page.goto('/projects/'+project.id+'?billing=returned');
    await page.getByRole('button',{name:'Billing',exact:true}).click();
    await expect(page.locator('dd').filter({hasText:'read only'})).toBeVisible();
    const future=new Date(Date.now()+86400000).toISOString();
    state={...state,status:'active',access:'paid',can_cancel:true,checkout_available:false,paid_through:future,current_period_end:future,notice:'Stripe test mode only. No live payment or merchant approval is claimed.'};
    await page.getByRole('button',{name:'Refresh billing state'}).click();
    await expect(page.getByRole('button',{name:'Cancel at period end'})).toBeVisible();
    for(const width of [390,768,1440]){
      await page.setViewportSize({width,height:1000});await page.evaluate(()=>window.scrollTo(0,0));
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
      await checkAccessibility(page,'billing-test-'+width);
      await page.screenshot({path:path.join(root,'output/playwright/billing-test-'+width+'.png'),fullPage:true});
    }
    await page.getByRole('button',{name:'Cancel at period end'}).click();
    await expect(page.getByText('Cancellation is scheduled.',{exact:false})).toBeVisible();
    expect(cancellations).toEqual([true]);
    await page.getByRole('button',{name:'Cancel immediately…'}).click();
    expect(cancellations).toEqual([true]);
    await page.getByRole('button',{name:'Keep subscription'}).click();
    expect(cancellations).toEqual([true]);
    await page.getByRole('button',{name:'Cancel immediately…'}).click();
    await page.getByRole('button',{name:'Confirm immediate cancellation'}).click();
    await expect(page.locator('dd').filter({hasText:'canceled'})).toBeVisible();
    expect(cancellations).toEqual([true,false]);
  } finally {
    const removed=await page.request.delete(base,{headers:{Origin:'http://127.0.0.1:3000','X-CSRF-Token':me.csrf_token}});
    expect(removed.status()).toBe(200);
  }
});
