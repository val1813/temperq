// Explicit synthetic risk materials -> browser upload, download and fixed-cutoff checks.
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {revealEvidence} from './qa_evidence_helpers.mjs';
const require=createRequire(new URL('../website/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
if(!process.env.TEMPERQ_RISK_FIXTURE) throw new Error('Set TEMPERQ_RISK_FIXTURE to an explicitly synthetic fixture directory');
const fixture=path.resolve(process.env.TEMPERQ_RISK_FIXTURE);
const declared=JSON.parse(await readFile(path.join(fixture,'fixture.json'),'utf8'));
if(declared.schema!=='risk_synthetic_browser_fixture_v1'||declared.source_kind!=='synthetic_fixture') throw new Error('Explicit synthetic risk fixture required');
async function verify(){
  for(const [name,sha] of Object.entries(declared.input_sha256)){
    const resolved=path.resolve(fixture,name);
    if(!resolved.startsWith(fixture+path.sep)) throw new Error('Fixture path outside declared folder');
    expect(createHash('sha256').update(await readFile(resolved)).digest('hex')).toBe(sha);
  }
}
await verify();
const roles=['scope','calendar','periods','weights','exposures','factor_returns','asset_returns'];
const base=process.env.TEMPERQ_INPUT_QA_URL||'http://127.0.0.1:8877';
const output=path.resolve(process.env.TEMPERQ_QA_OUTPUT||'artifacts/risk-evidence-qa');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'chrome',args:['--disable-gpu','--renderer-process-limit=1']});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('response',r=>{if(r.status()>=500) errors.push(r.url()+' '+r.status())});
const reports={};
let importUrl;
async function screenshot(name,width){
  await page.setViewportSize({width,height:width===1440?1000:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:path.join(output,name+'-'+width+'.png'),fullPage:true});
}
async function importSample(name){
  const sample=await page.request.get(base+'/strategy-audits/sample.zip');
  expect(sample.ok()).toBe(true);
  await page.goto(base+'/strategy-audits/new');
  await page.locator('[name=name]').fill(name);
  await page.locator('[name=files]').setInputFiles({name:'sample.zip',mimeType:'application/zip',buffer:await sample.body()});
  await page.getByRole('button',{name:/^(导入并核对材料|上传并核对)$/}).click();
  await expect(page).toHaveURL(/\/strategy-audits\/[0-9a-f]{32}$/);
  return page.url();
}
async function upload(scenario,valid=true){
  await page.goto(importUrl);
  await revealEvidence(page);
  await page.locator('#validation-risk summary').click();
  const form=page.locator('#validation-risk form');
  expect(await form.locator('input[type=file]').count()).toBe(7);
  for(const role of roles){
    const name=role+(role==='scope'?'.json':'.csv');
    await form.locator('[name='+role+']').setInputFiles(path.join(fixture,scenario,name));
  }
  await form.locator('[name=source_kind]').selectOption('synthetic_fixture');
  await form.locator('[name=note]').fill('风险合成验证 <script>alert(1)</script>');
  await form.locator('[name=scope_confirmed]').check();
  if(valid){
    await form.locator('button').click();
    await expect(page).toHaveURL(/\/validation\/[0-9a-f]{32}$/);
    await expect(page.locator('main')).toContainText('模型未校准');
    await expect(page.locator('main')).toContainText('机构准入未建立');
    expect(await page.locator('main script').count()).toBe(0);
    const response=await page.request.get(page.url()+'/report.json');
    expect(response.headers()['cache-control']).toBe('no-store');
    expect(response.headers()['x-content-type-options']).toBe('nosniff');
    const report=await response.json();
    expect(report.mode).toBe('risk');
    expect(report.source_kind).toBe('synthetic_fixture');
    expect(report.result.risk_model_calibrated).toBe(false);
    expect(report.institutional_readiness).toBe('not_established');
    reports[scenario]={url:page.url(),report};
    await writeFile(path.join(output,scenario+'_evidence.json'),JSON.stringify(report,null,2));
    return report;
  }
  const responsePromise=page.waitForResponse(r=>r.request().method()==='POST'&&r.url()===importUrl+'/validation');
  await form.locator('button').click();
  expect((await responsePromise).status()).toBe(400);
  await expect(page.getByRole('alert')).toBeVisible();
  return null;
}
try{
  importUrl=await importSample('合成风险模型 · 网页端到端验收');
  const identity=new URL(importUrl).pathname.split('/').at(-1);
  const template=await page.request.get(importUrl+'/validation/templates/risk.zip');
  expect(template.ok()).toBe(true);
  expect(template.headers()['x-content-type-options']).toBe('nosniff');
  const normal=await upload('normal');
  expect(normal.strategy_id).toBe(identity);
  expect(normal.result.forecast.portfolio.variance).toBeCloseTo(.0005026666666666667,14);
  await expect(page.locator('main')).toContainText('预期尾部超限少于5次');
  await expect(page.locator('main')).toContainText('协方差秩不足');
  await expect(page.getByRole('heading',{name:'风险估计与留出诊断已计算',exact:true})).toBeVisible();
  for(const width of [1440,390,320]) await screenshot('01-normal',width);
  for(const role of roles){
    const downloaded=await page.request.get(reports.normal.url+'/inputs/'+role);
    expect(downloaded.headers()['cache-control']).toBe('no-store');
    expect(await downloaded.body()).toEqual(await readFile(path.join(fixture,'normal',role+(role==='scope'?'.json':'.csv'))));
  }
  const future=await upload('future');
  expect(future.result.forecast).toEqual(normal.result.forecast);
  expect(future.result.model_digest).toBe(normal.result.model_digest);
  const shock=await upload('shock');
  expect(shock.result.forecast).toEqual(normal.result.forecast);
  expect(shock.result.model_digest).toBe(normal.result.model_digest);
  expect(shock.result.evaluation.portfolio.exceedances).toBe(3);
  await screenshot('02-shock',320);
  const train=await upload('missing_training');
  expect(train.result.forecast).toBeNull();
  await expect(page.locator('main')).toContainText('未生成风险预测');
  await screenshot('03-missing-training',320);
  const holdout=await upload('missing_holdout');
  expect(holdout.result.forecast).toEqual(normal.result.forecast);
  expect(holdout.result.model_digest).toBe(normal.result.model_digest);
  expect(holdout.result.evaluation.portfolio.observations).toBeNull();
  await expect(page.locator('main')).toContainText('预测已保留');
  await expect(page.locator('main')).toContainText('留出材料缺口');
  await screenshot('04-missing-holdout',320);
  const zero=await upload('zero_active');
  expect(zero.result.forecast.active.volatility).toBe(0);
  expect(zero.result.evaluation.active.status).toBe('degenerate_forecast');
  await expect(page.locator('main')).toContainText('零方差预测');
  await screenshot('05-zero-active',320);
  await upload('future_weights',false);
  await expect(page.locator('main')).toContainText('截止时点');
  await screenshot('06-future-weights-rejected',320);
  await upload('calendar_gap',false);
  await expect(page.locator('main')).toContainText('calendar');
  await screenshot('07-calendar-gap-rejected',320);
  await page.goto(importUrl);
  await revealEvidence(page);
  await expect(page.getByRole('heading',{name:'已有账户、归因与风险证据',exact:true})).toBeVisible();
  expect(await page.locator('#validation-evidence a[href*="/validation/"]').count()).toBe(9); // three templates, six saved reports
  await page.locator('#validation-risk summary').click();
  await screenshot('08-upload-forms',320);
  const other=await importSample('另一份合成策略上下文');
  const otherId=new URL(other).pathname.split('/').at(-1);
  expect((await page.request.get(reports.normal.url.replace(identity,otherId))).status()).toBe(404);
  await verify();
  expect(errors).toEqual([]);
  const summary={passed:true,importUrl,normalUrl:reports.normal.url,scenarios:Object.fromEntries(Object.entries(reports).map(([name,value])=>[name,{url:value.url,status:value.report.result.status}])),
    rejected:['future_weights','calendar_gap'],cross_strategy_rejected:true,widths:[1440,390,320],errors,
    scope:'synthetic_risk_browser_acceptance_not_real_model_calibration_or_broker_validation'};
  await writeFile(path.join(output,'result.json'),JSON.stringify(summary,null,2));
  console.log(JSON.stringify(summary));
}catch(error){
  await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});
  console.error(JSON.stringify({url:page.url(),errors}));
  throw error;
}finally{await browser.close()}
