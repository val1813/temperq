// Existing same-simulator daily account materials -> actual browser upload.
// Original probe/inputs stay read-only; no source strategy or broker is run.
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
const require=createRequire(new URL('../website/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
if(!process.env.TEMPERQ_NATIVE_PROBE||!process.env.TEMPERQ_NATIVE_ACCOUNT_FIXTURE) throw new Error('Set existing native probe and native account fixture paths');
const probe=path.resolve(process.env.TEMPERQ_NATIVE_PROBE);
const fixture=path.resolve(process.env.TEMPERQ_NATIVE_ACCOUNT_FIXTURE);
const native=JSON.parse(await readFile(path.join(probe,'probe.json'),'utf8'));
const declared=JSON.parse(await readFile(path.join(fixture,'fixture.json'),'utf8'));
if(native.scope!=='historical_real_prices_simulated_account_not_broker_or_forward'||native.source_independence!=='same_simulator_two_reporting_paths'||declared.schema!=='native_validation_fixture_v1'||declared.source_kind!=='simulator_native') throw new Error('Only explicitly simulated native probe/account fixtures are supported');
expect(declared.probe_sha256).toBe(createHash('sha256').update(await readFile(path.join(probe,'probe.json'))).digest('hex'));
async function verify(){
  for(const [name,digest] of Object.entries(declared.input_sha256)){
    if(name!==path.basename(name)) throw new Error('Fixture filenames must be flat');
    expect(createHash('sha256').update(await readFile(path.join(fixture,name))).digest('hex')).toBe(digest);
  }
}
await verify();
const base=process.env.TEMPERQ_INPUT_QA_URL||'http://127.0.0.1:8876';
const output=path.resolve(process.env.TEMPERQ_QA_OUTPUT||'artifacts/native-validation-evidence-qa');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('response',r=>{if(r.status()>=500) errors.push(r.url()+' '+r.status())});
try{
  await page.goto(base+'/strategy-audits/new');
  await page.locator('[name=name]').fill('历史价格 · 原生模拟日终网页验收');
  await page.locator('[name=opening_empty]').check();
  await page.locator('[name=opening_cash]').fill(String(native.opening_cash));
  await page.locator('[name=files]').setInputFiles(await Promise.all(['strategy.py','trades.csv','holdings.csv','account.csv','prices.csv'].map(async name=>({name,mimeType:'text/plain',buffer:await readFile(path.join(probe,'materials',name))}))));
  await page.getByRole('button',{name:'导入并核对材料',exact:true}).click();
  await expect(page).toHaveURL(/\/strategy-audits\/[0-9a-f]{32}$/);
  const importUrl=page.url();
  await page.locator('#validation-account summary').click();
  const form=page.locator('#validation-account form');
  for(const [role,name] of [['internal','internal.zip'],['external','external.zip'],['calendar','calendar.csv']]) await form.locator('[name='+role+']').setInputFiles(path.join(fixture,name));
  await form.locator('[name=source_kind]').selectOption('simulator_native');
  await form.locator('[name=scope_confirmed]').check();
  await form.locator('button').click();
  await expect(page).toHaveURL(/\/validation\/[0-9a-f]{32}$/);
  const url=page.url();
  await expect(page.getByRole('heading',{name:'日终账户材料匹配',exact:true})).toBeVisible();
  await expect(page.locator('main')).toContainText('模拟引擎回报');
  const report=await (await page.request.get(url+'/report.json')).json();
  expect(report.source_kind).toBe('simulator_native');
  expect(report.result.status).toBe('matched');
  expect(report.result.executions.internal_fills).toBe(native.fills);
  expect(report.result.expected_snapshot_dates.length).toBe(native.sessions+1);
  expect(report.result.real_broker_validation).toBe(false);
  expect(report.institutional_readiness).toBe('not_established');
  for(const width of [1440,390,320]){
    await page.setViewportSize({width,height:width===1440?1000:844});
    await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
    await page.screenshot({path:path.join(output,'native-account-'+width+'.png'),fullPage:true});
  }
  await verify();
  expect(errors).toEqual([]);
  await writeFile(path.join(output,'validation_evidence.json'),JSON.stringify(report,null,2));
  const summary={passed:true,importUrl,accountUrl:url,sessions:native.sessions,fills:native.fills,widths:[1440,390,320],errors,source_kind:'simulator_native',real_broker_validation:false,scope:native.scope,original_execution_model:native.execution_model};
  await writeFile(path.join(output,'result.json'),JSON.stringify(summary,null,2));
  console.log(JSON.stringify(summary));
}finally{await browser.close()}
