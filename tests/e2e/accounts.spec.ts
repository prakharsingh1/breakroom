import { test, expect } from '../../apps/web/node_modules/@playwright/test';
import path from 'node:path';
import { checkAccessibility } from './accessibility';
const root = path.resolve(__dirname, '../..');

test('real password account persists, owns a private workspace, and revokes other sessions on password change', async ({page,browser}) => {
  test.setTimeout(120_000);
  const email='account-browser-'+Date.now()+'@example.invalid';
  const password='Initial-passphrase-for-test-42';
  const nextPassword='Replacement-passphrase-for-test-73';
  await page.goto('/signup');
  await expect(page.getByRole('heading',{name:'Create your account',exact:true})).toBeVisible();
  await page.getByLabel('Your name',{exact:true}).fill('Alex Morgan');
  await page.getByLabel('Email address',{exact:true}).fill(email);
  await page.getByLabel('Password',{exact:true}).fill(password);
  const registered=page.waitForResponse(r=>r.url().endsWith('/auth/register'));
  await page.getByRole('button',{name:'Create account',exact:true}).click();
  expect((await registered).status()).toBe(201);
  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByRole('heading',{name:'Welcome back, Alex.'})).toBeVisible();
  await expect(page.getByText('No projects yet. Create your first one to begin.')).toBeVisible();
  const auth=await (await page.request.get('/api/team/me')).json();
  expect(auth.user.email).toBe(email);expect(auth.user.email_verified).toBe(false);
  const projectResponse=page.waitForResponse(r=>r.url().endsWith('/api/team/projects')&&r.request().method()==='POST');
  await page.getByLabel('Project name',{exact:true}).fill('Customer support checks');
  await page.getByRole('button',{name:'Create project →'}).click();
  const project=await(await projectResponse).json();expect(project.role).toBe('owner');
  await expect(page.locator('.workspace-project-row')).toHaveCount(1);
  for(const width of [390,768,1440]){
    await page.setViewportSize({width,height:1000});
    await checkAccessibility(page,'customer-dashboard-'+width);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:path.join(root,'output/playwright/customer-dashboard-'+width+'.png'),fullPage:true});
  }
  const other=await browser.newContext({baseURL:'http://127.0.0.1:3000'});
  try{
    expect((await other.request.get('/api/team/projects/'+project.id)).status()).toBe(401);
    const otherPage=await other.newPage();
    await otherPage.goto('/signin');
    await otherPage.getByLabel('Email address',{exact:true}).fill(email);
    await otherPage.getByLabel('Password',{exact:true}).fill(password);
    await otherPage.getByRole('button',{name:'Sign in',exact:true}).click();
    await expect(otherPage).toHaveURL(/\/projects$/);
    await expect(otherPage.locator('.workspace-project-row')).toHaveCount(1);
    await page.goto('/account');
    await expect(page.getByText('Email verification pending',{exact:true})).toBeVisible();
    await page.getByLabel('Current password',{exact:true}).fill(password);
    await page.getByLabel('New password',{exact:true}).fill(nextPassword);
    await page.getByLabel('Confirm new password',{exact:true}).fill(nextPassword);
    await page.getByRole('button',{name:'Update password',exact:true}).click();
    await expect(page.getByText('Password updated. Other sessions have been signed out.')).toBeVisible();
    expect((await other.request.get('/api/team/projects/'+project.id)).status()).toBe(401);
    await page.goto('/projects');await page.getByRole('button',{name:'Sign out',exact:true}).click();
    await page.getByLabel('Email address',{exact:true}).fill(email);
    await page.getByLabel('Password',{exact:true}).fill(password);
    await page.getByRole('button',{name:'Sign in',exact:true}).click();
    await expect(page.getByRole('main').getByRole('alert')).toContainText('Email or password is incorrect.');
    await page.getByLabel('Password',{exact:true}).fill(nextPassword);
    await page.getByRole('button',{name:'Sign in',exact:true}).click();
    await expect(page.locator('.workspace-project-row')).toHaveCount(1);
    await page.reload();await expect(page.locator('.workspace-project-row')).toHaveCount(1);
    const me=await(await page.request.get('/api/team/me')).json();
    expect((await page.request.delete('/api/team/projects/'+project.id,{headers:{Origin:'http://127.0.0.1:3000','X-CSRF-Token':me.csrf_token}})).status()).toBe(200);
  } finally {await other.close();}
});

test('account forms work with keyboard and expose honest recovery and invalid-link states',async({page})=>{
  for(const width of [390,768,1440]){
    await page.setViewportSize({width,height:1000});await page.goto('/signup');
    await expect(page.getByRole('button',{name:'Create account',exact:true})).toBeVisible();
    await checkAccessibility(page,'customer-signup-'+width);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:path.join(root,'output/playwright/customer-signup-'+width+'.png'),fullPage:true});
  }
  await page.getByRole('button',{name:'Back to sign in',exact:true}).click();
  await page.getByRole('button',{name:'Forgot password?',exact:true}).click();
  const config=await(await page.request.get('/api/team/auth/config')).json();
  if(!config.mail_available)await expect(page.getByRole('button',{name:'Send reset link'})).toBeDisabled();
  for(const url of ['/verify-email','/reset-password']){await page.goto(url);await expect(page.getByRole('main').getByRole('alert')).toContainText('This link is missing or invalid.');await checkAccessibility(page,url.slice(1)+'-invalid');}
});
