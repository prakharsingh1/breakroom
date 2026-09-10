import { test, expect } from '../../apps/web/node_modules/@playwright/test';
import { checkAccessibility } from './accessibility';

test('public pages and executed evidence pass automated accessibility checks', async ({page}) => {
  test.setTimeout(180_000);
  await page.emulateMedia({reducedMotion:'reduce'});
  for (const width of [390,768,1440]) {
    await page.setViewportSize({width,height:1000});
    for (const [name,url] of [['home','/'],['library','/fire-drills'],['local-drill','/fire-drills/pending-refund-succeeds'],['docs','/docs'],['pricing','/pricing'],['runs-empty','/runs'],['compare-empty','/compare'],['team-signin','/projects']]) {
      await page.goto(url);
      await expect(page.locator('main h1')).toBeVisible();
      if (name==='library') await expect(page.locator('.drill-row')).toHaveCount(24);
      if (name==='local-drill') await expect(page.getByRole('heading',{name:'Run this drill locally'})).toBeVisible();
      if (name==='team-signin') await expect(page.getByRole('region',{name:'Account access'}).or(page.locator('main [role=alert]'))).toBeVisible();
      await checkAccessibility(page,name+'-'+width);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
    }
  }
  await page.goto('/demo');
  await page.getByRole('button',{name:'▶ Run faulty reference',exact:true}).click();
  await expect(page.getByTestId('refund-total')).toHaveText('₹2,000');
  await checkAccessibility(page,'demo-executed');
  await page.getByRole('button',{name:'Corrected reference',exact:true}).click();
  await page.getByRole('button',{name:'▶ Run corrected reference',exact:true}).click();
  await expect(page.getByTestId('refund-total')).toHaveText('₹1,000');
  await page.getByRole('link',{name:'Compare runs →',exact:true}).click();
  await expect(page.getByText('Compatible evaluation contracts')).toBeVisible();
  await checkAccessibility(page,'compare-executed');
  await page.goto('/runs');
  await page.locator('.saved-run').first().click();
  await expect(page.getByRole('heading',{name:'Ordered event log'})).toBeVisible();
  await checkAccessibility(page,'recorded-evidence');
  await page.goto('/');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link',{name:'Skip to content'})).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('main')).toBeFocused();
  expect(await page.evaluate(()=>getComputedStyle(document.documentElement).scrollBehavior)).toBe('auto');
});
