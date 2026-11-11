// Opt-in bridge from an existing historical native probe to the running web UI.
// No source strategy is executed, no source exports are changed, no broker used.
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(new URL('../website/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
if(!process.env.TEMPERQ_NATIVE_PROBE) throw new Error('Set TEMPERQ_NATIVE_PROBE to an existing native probe directory');
const probe=path.resolve(process.env.TEMPERQ_NATIVE_PROBE);
const native=JSON.parse(await readFile(path.join(probe,'probe.json'),'utf8'));
if(native.scope!=='historical_real_prices_simulated_account_not_broker_or_forward'||native.source_independence!=='same_simulator_two_reporting_paths') throw new Error('Only an explicitly simulated native probe is accepted');
const base=process.env.TEMPERQ_INPUT_QA_URL||'http://127.0.0.1:8875';
const output=path.resolve(process.env.TEMPERQ_QA_OUTPUT||'artifacts/native-execution-ui-qa');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
try{
  await page.goto(base+'/strategy-audits/new');
  await page.locator('[name=name]').fill('历史真实行情 · 原生模拟成交网页验收');
  await page.locator('[name=opening_empty]').check();
  await page.locator('[name=opening_cash]').fill(String(native.opening_cash));
  const files=await Promise.all(['strategy.py','trades.csv','holdings.csv','account.csv','prices.csv'].map(async name=>({name,mimeType:'text/plain',buffer:await readFile(path.join(probe,'materials',name))})));
  await page.locator('[name=files]').setInputFiles(files);
  await page.getByRole('button',{name:'导入并核对材料',exact:true}).click();
  await expect(page).toHaveURL(/\/strategy-audits\/[0-9a-f]{32}$/);
  const importUrl=page.url();
  await page.locator('#execution-reconcile summary').click();
  const form=page.locator('#execution-reconcile form');
  await form.locator('[name=first]').setInputFiles(path.join(probe,'recorded_executions.csv'));
  await form.locator('[name=second]').setInputFiles(path.join(probe,'native_executions.csv'));
  await form.locator('[name=source_kind]').selectOption('simulator_native');
  await form.locator('[name=scope_confirmed]').check();
  await form.locator('button').click();
  await expect(page).toHaveURL(/\/execution\/[0-9a-f]{32}$/);
  await expect(page.getByRole('heading',{name:'订单层全部匹配',exact:true})).toBeVisible();
  await expect(page.locator('main')).toContainText('模拟引擎回报');
  const url=page.url();
  const report=await (await page.request.get(url+'/report.json')).json();
  expect(report.result.internal_fills).toBe(native.fills);
  expect(report.result.broker_fills).toBe(native.fills);
  expect(report.result.status).toBe('matched');
  expect(report.source_kind).toBe('simulator_native');
  expect(report.result.source_independence).toBe('not_verified');
  await page.screenshot({path:path.join(output,'native-reconciliation.png'),fullPage:true});
  await writeFile(path.join(output,'execution_evidence.json'),JSON.stringify(report,null,2));
  const result={passed:true,scope:'historical_real_prices_simulated_two_reporting_paths_not_broker',fills:native.fills,importUrl,reportUrl:url};
  await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
}catch(error){
  await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});
  throw error;
}finally{await browser.close()}
