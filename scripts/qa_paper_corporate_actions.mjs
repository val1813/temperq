// Run only against the isolated lifecycle E2E fixture server.
import {createRequire} from 'node:module';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(new URL('../website/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
const base=process.env.TEMPERQ_QA_URL || 'http://127.0.0.1:8887';
const input=process.env.TEMPERQ_LIFECYCLE_FIXTURE;
const strategy=process.env.TEMPERQ_LIFECYCLE_STRATEGY;
if (!input || !strategy) throw Error('Isolated lifecycle fixture and strategy IDs required');
const output=path.resolve(process.env.TEMPERQ_QA_OUTPUT || 'artifacts/paper-lifecycle-browser');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'chrome'});
const page=await browser.newPage();
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('response',response=>{if(response.status()>=500) errors.push(String(response.status()))});
async function screenshot(name) {
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
}
try {
  for(const width of [1440,390,320]) {
    await page.setViewportSize({width,height:width===1440?1000:844});
    await page.goto(base+'/paper/pending');
    await expect(page.getByRole('heading',{name:'公司行动与待结权益',exact:true})).toBeVisible();
    await expect(page.locator('main')).toContainText('已确认应收');
    await expect(page.locator('main')).toContainText('2025-06-16');
    await screenshot('paper-pending-'+width);
    await page.goto(base+'/strategies/'+strategy+'/dashboard');
    await expect(page.locator('#desk-holdings-table')).toContainText('待可卖股份');
    await expect(page.locator('#desk-holdings-table')).toContainText('已确认应收');
    await expect(page.locator('#metric-cash')).toContainText('应收');
    await screenshot('strategy-pending-'+width);
  }
  await page.goto(base+'/paper/data');
  await expect(page.locator('[name=corporate_actions]')).toBeVisible();
  await screenshot('event-import-320');
  await page.locator('[name=name]').fill('lifecycle_browser_'+Date.now());
  for(const [field,name] of [['bars','complete.csv'],['calendar','calendar.csv'],['corporate_actions','events.json']]) {
    await page.locator('[name='+field+']').setInputFiles({name,mimeType:name.endsWith('.json')?'application/json':'text/csv',buffer:await readFile(path.join(input,name))});
  }
  await page.getByRole('button',{name:'保存数据更新',exact:true}).click();
  await expect(page.getByRole('heading',{name:'数据更新已保存',exact:true})).toBeVisible();
  expect(errors).toEqual([]);
  const result={passed:true,viewports:[1440,390,320],pending_claim_and_locked_shares_visible:true,event_upload_saved:true,
    errors,scope:'synthetic_browser_fixture_not_user_account_or_broker_validation'};
  await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
} finally {await browser.close()}
