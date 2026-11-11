// Isolated synthetic web checks; never constructs independent broker evidence.
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {revealEvidence} from './qa_evidence_helpers.mjs';
const require=createRequire(new URL('../website/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
if(!process.env.TEMPERQ_VALIDATION_FIXTURE) throw new Error('Set TEMPERQ_VALIDATION_FIXTURE to an isolated synthetic fixture directory');
const fixture=path.resolve(process.env.TEMPERQ_VALIDATION_FIXTURE);
const declared=JSON.parse(await readFile(path.join(fixture,'fixture.json'),'utf8'));
if(declared.schema!=='validation_fixture_v1'||declared.source_kind!=='synthetic_fixture') throw new Error('Explicit synthetic fixture required');
for(const [name,digest] of Object.entries(declared.input_sha256)){
  if(name!==path.basename(name)) throw new Error('Fixture filename must be flat');
  expect(createHash('sha256').update(await readFile(path.join(fixture,name))).digest('hex')).toBe(digest);
}
const base=process.env.TEMPERQ_INPUT_QA_URL||'http://127.0.0.1:8876';
const output=path.resolve(process.env.TEMPERQ_QA_OUTPUT||'artifacts/validation-evidence-qa');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'chrome',args:['--disable-gpu','--renderer-process-limit=1']});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('response',r=>{if(r.status()>=500) errors.push(r.url()+' '+r.status())});
const factorRoot=path.resolve(new URL('../examples/factor_attribution_synthetic/',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1'));
async function shot(name){
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  if(await page.locator('table.validation-details').count()) await expect.poll(()=>page.locator('table.validation-details').first().evaluate(t=>t.offsetWidth>=760)).toBe(true);
  await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
}
async function upload(mode,files){
  await revealEvidence(page);
  const details=page.locator('#validation-'+mode);
  await details.locator('summary').click();
  const form=details.locator('form');
  for(const [role,buffer,name] of files) await form.locator('[name='+role+']').setInputFiles({name,mimeType:name.endsWith('.zip')?'application/zip':name.endsWith('.json')?'application/json':'text/csv',buffer});
  await form.locator('[name=source_kind]').selectOption('synthetic_fixture');
  await form.locator('[name=note]').fill('合成验证 <script>alert(1)</script>');
  await form.locator('[name=scope_confirmed]').check();
  await form.locator('button').click();
  await expect(page).toHaveURL(/\/validation\/[0-9a-f]{32}$/);
  await expect(page.locator('main')).toContainText('机构准入未建立');
  expect(await page.locator('main script').count()).toBe(0);
  const response=await page.request.get(page.url()+'/report.json');
  expect(response.headers()['cache-control']).toBe('no-store');
  expect(response.headers()['x-content-type-options']).toBe('nosniff');
  const report=await response.json();
  expect(report.institutional_readiness).toBe('not_established');
  expect(report.source_kind).toBe('synthetic_fixture');
  return report;
}
try{
  const sample=await page.request.get(base+'/strategy-audits/sample.zip');
  expect(sample.ok()).toBe(true);
  await page.goto(base+'/strategy-audits/new');
  await page.locator('[name=name]').fill('合成材料 · 日终与因子网页验收');
  await page.locator('[name=files]').setInputFiles({name:'sample.zip',mimeType:'application/zip',buffer:await sample.body()});
  await page.getByRole('button',{name:/^(导入并核对材料|上传并核对)$/}).click();
  await expect(page).toHaveURL(/\/strategy-audits\/[0-9a-f]{32}$/);
  const importUrl=page.url();
  const identity=new URL(importUrl).pathname.split('/').at(-1);
  for(const mode of ['account','factor']){
    const template=await page.request.get(importUrl+'/validation/templates/'+mode+'.zip');
    expect(template.ok()).toBe(true);
    expect(template.headers()['x-content-type-options']).toBe('nosniff');
  }
  const accountFiles=await Promise.all(['internal.zip','external.zip','calendar.csv'].map(async name=>[name.split('.')[0],await readFile(path.join(fixture,name)),name]));
  const account=await upload('account',accountFiles);
  const accountUrl=page.url();
  expect(account.strategy_id).toBe(identity);
  expect(account.result.status).toBe('matched');
  expect(account.result.breaks).toBe(0);
  expect(account.result.expected_snapshot_dates.length).toBe(4);
  await expect(page.getByRole('heading',{name:'日终账户材料匹配',exact:true})).toBeVisible();
  expect(await (await page.request.get(accountUrl+'/inputs/internal')).body()).toEqual(accountFiles[0][1]);
  for(const width of [1440,390,320]){
    await page.setViewportSize({width,height:width===1440?1000:844});
    await shot('01-account-'+width);
  }
  await page.goto(importUrl);
  const brokenFiles=accountFiles.map(row=>[...row]);
  brokenFiles[1][1]=await readFile(path.join(fixture,'external_break.zip'));
  const broken=await upload('account',brokenFiles);
  const brokenUrl=page.url();
  expect(broken.result.status).toBe('breaks');
  expect(broken.result.breaks).toBe(3);
  await expect(page.getByRole('heading',{name:'日终账户存在差异',exact:true})).toBeVisible();
  await shot('02-account-break-320');
  await page.goto(importUrl);
  const factorFiles=await Promise.all(['scope.json','periods.csv','weights.csv','exposures.csv','factor_returns.csv'].map(async name=>[name.split('.')[0],await readFile(path.join(factorRoot,name)),name]));
  const factor=await upload('factor',factorFiles);
  const factorUrl=page.url();
  expect(factor.strategy_input_digest).toBe(account.strategy_input_digest);
  expect(factor.result.aggregate.active.compounded_return_difference).toBe('-0.0305');
  await expect(page.locator('main')).toContainText('残差（不是alpha）');
  await expect(page.locator('main')).toContainText('-3.0500%');
  for(const width of [320,390,1440]){
    await page.setViewportSize({width,height:width===1440?1000:844});
    await shot('03-factor-'+width);
  }
  await page.goto(importUrl);
  const missingFiles=factorFiles.map(row=>[...row]);
  missingFiles[3][1]=Buffer.from(missingFiles[3][1].toString('utf8').split(/\r?\n/)[0]+'\n');
  const missing=await upload('factor',missingFiles);
  const missingUrl=page.url();
  expect(missing.result.status).toBe('insufficient');
  expect(missing.result.aggregate).toBeNull();
  await expect(page.getByRole('heading',{name:'证据不足',exact:true})).toBeVisible();
  await page.setViewportSize({width:320,height:844});
  await shot('04-factor-missing-320');
  await page.goto(importUrl);
  await revealEvidence(page);
  await expect(page.getByRole('heading',{name:'已有账户、归因与风险证据',exact:true})).toBeVisible();
  expect(await page.locator('#validation-evidence a[href*="/validation/"]').count()).toBe(7); // three templates and four reports
  await page.locator('#validation-factor summary').click();
  await shot('05-forms-320');
  expect(errors).toEqual([]);
  for(const [name,digest] of Object.entries(declared.input_sha256)) expect(createHash('sha256').update(await readFile(path.join(fixture,name))).digest('hex')).toBe(digest);
  const summary={passed:true,importUrl,accountUrl,brokenUrl,factorUrl,missingUrl,widths:[1440,390,320],errors,scope:'synthetic_browser_acceptance_not_broker_or_calibrated_risk_evidence'};
  await writeFile(path.join(output,'result.json'),JSON.stringify(summary,null,2));
  await writeFile(path.join(output,'account_evidence.json'),JSON.stringify(account,null,2));
  await writeFile(path.join(output,'factor_evidence.json'),JSON.stringify(factor,null,2));
  console.log(JSON.stringify(summary));
}catch(error){
  await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});
  console.error(JSON.stringify({url:page.url(),errors}));
  throw error;
}finally{await browser.close()}
