import AxeBuilder from '../../apps/web/node_modules/@axe-core/playwright';
import { expect, Page } from '../../apps/web/node_modules/@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

export async function checkAccessibility(page: Page, name: string) {
  const result = await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa','best-practice']).analyze();
  const summary = {
    page: name, engine: result.testEngine, checked_at: result.timestamp,
    passed_rules: result.passes.length,
    violations: result.violations.map(v => ({id:v.id,impact:v.impact,help:v.help,helpUrl:v.helpUrl,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))})),
    needs_manual_review: result.incomplete.map(v => ({id:v.id,help:v.help,targets:v.nodes.map(n=>n.target)}))
  };
  const output = path.resolve(__dirname,'../../artifacts/accessibility');
  fs.mkdirSync(output,{recursive:true});
  fs.writeFileSync(path.join(output,name+'.json'),JSON.stringify(summary,null,2)+'\n');
  expect.soft(summary.violations, name+' accessibility violations').toEqual([]);
}
