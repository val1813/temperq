// Browser verification of independent execution inputs; all fixtures synthetic.
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {revealEvidence} from './qa_evidence_helpers.mjs';
const require=createRequire(new URL('../website/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
const base=process.env.TEMPERQ_INPUT_QA_URL||'http://127.0.0.1:8875';
const output=path.resolve(process.env.TEMPERQ_QA_OUTPUT||'artifacts/execution-evidence-qa');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'chrome',args:['--disable-gpu','--renderer-process-limit=1']});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('response',r=>{if(r.status()>=500) errors.push(r.url()+' '+r.status())});
const internal='date,order_id,trade_id,code,side,quantity,price,fee\n2024-01-02,o1,t1,510300.SH,buy,100,4.00,5\n';
const orders='order_id,code,side,quantity,decision_time,decision_price,arrival_time,arrival_price,end_time,end_price\np1,510300.SH,buy,100,2024-01-02T09:30:00+08:00,10,2024-01-02T09:31:00+08:00,11,2024-01-02T15:00:00+08:00,13\n';
const fills='order_id,trade_id,code,side,time,quantity,price,fee\np1,t1,510300.SH,buy,2024-01-02T09:32:00+08:00,60,12,5\n';
async function shot(name){
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
}
async function upload(mode,first,second){
  await revealEvidence(page);
  const details=page.locator('#execution-'+mode);
  await details.locator('summary').click();
  const form=details.locator('form');
  await form.locator('[name=first]').setInputFiles({name:'first.csv',mimeType:'text/csv',buffer:Buffer.from(first)});
  await form.locator('[name=second]').setInputFiles({name:'second.csv',mimeType:'text/csv',buffer:Buffer.from(second)});
  await form.locator('[name=source_kind]').selectOption('synthetic_fixture');
  await form.locator('[name=note]').fill('合成验收 <script>alert(1)</script>');
  await form.locator('[name=scope_confirmed]').check();
  await form.locator('button').click();
  await expect(page).toHaveURL(/\/execution\/[0-9a-f]{32}$/);
}
try{
  const sample=await page.request.get(base+'/strategy-audits/sample.zip');
  expect(sample.ok()).toBe(true);
  await page.goto(base+'/strategy-audits/new');
  await page.locator('[name=name]').fill('合成材料 · 执行证据网页验收');
  await page.locator('[name=files]').setInputFiles({name:'sample.zip',mimeType:'application/zip',buffer:await sample.body()});
  await page.getByRole('button',{name:/^(导入并核对材料|上传并核对)$/}).click();
  await expect(page).toHaveURL(/\/strategy-audits\/[0-9a-f]{32}$/);
  const importUrl=page.url();
  await upload('reconcile',internal,internal.replace(',100,',',200,'));
  const reconcileUrl=page.url();
  await expect(page.getByRole('heading',{name:'存在订单差异',exact:true})).toBeVisible();
  await expect(page.locator('main')).toContainText('合成测试材料');
  await expect(page.locator('main script')).toHaveCount(0);
  const result=await page.request.get(reconcileUrl+'/report.json');
  expect(result.headers()['cache-control']).toBe('no-store');
  expect((await result.json()).result.breaks).toBe(1);
  expect(await (await page.request.get(reconcileUrl+'/inputs/internal.csv')).text()).toBe(internal);
  for(const width of [1440,390,320]){
    await page.setViewportSize({width,height:width===1440?1000:844});
    await shot('01-reconcile-'+width);
  }
  await page.goto(importUrl);
  await upload('tca',orders,fills);
  const tcaUrl=page.url();
  await expect(page.getByRole('heading',{name:'分解已计算',exact:true})).toBeVisible();
  const tca=await (await page.request.get(tcaUrl+'/report.json')).json();
  expect(tca.result.totals.total_cost).toBe('245');
  expect(tca.result.totals.opportunity_cost).toBe('80');
  expect(tca.source_kind).toBe('synthetic_fixture');
  for(const width of [320,390,1440]){
    await page.setViewportSize({width,height:width===1440?1000:844});
    await shot('02-tca-'+width);
  }
  await page.goto(importUrl);
  await revealEvidence(page);
  await expect(page.getByRole('heading',{name:'已有执行证据',exact:true})).toBeVisible();
  expect(await page.locator('#execution-evidence a[href="'+new URL(tcaUrl).pathname+'"]').count()).toBe(1);
  await page.setViewportSize({width:320,height:844});
  await page.locator('#execution-reconcile summary').click();
  await shot('03-import-320');
  expect(errors).toEqual([]);
  const summary={passed:true,importUrl,reconcileUrl,tcaUrl,errors,scope:'synthetic_browser_acceptance_not_broker_evidence'};
  await writeFile(path.join(output,'result.json'),JSON.stringify(summary,null,2));
  console.log(JSON.stringify(summary));
}catch(error){
  await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});
  console.error(JSON.stringify({url:page.url(),errors}));
  throw error;
}finally{await browser.close()}
