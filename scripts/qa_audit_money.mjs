// Browser acceptance uses the built-in synthetic ZIP in an isolated workspace.
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(new URL('../website/package.json', import.meta.url));
const {chromium, expect} = require('@playwright/test');
const base = process.env.TEMPERQ_AUDIT_QA_URL || 'http://127.0.0.1:8890';
const output = path.resolve(process.env.TEMPERQ_QA_OUTPUT || 'artifacts/audit-money-browser-v1');
await mkdir(output, {recursive:true});
const browser = await chromium.launch({headless:true, channel:'chrome',args:['--disable-gpu','--renderer-process-limit=1']});
const page = await browser.newPage({viewport:{width:1440,height:1000}});
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('response', response => {if(response.status() >= 500) errors.push(response.url());});
async function shot(name, fullPage=true) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1)).toBe(true);
  await page.screenshot({path:path.join(output,name+'.png'),fullPage});
}
try {
  await page.goto(base);
  await expect(page.getByRole('heading',{name:'上传策略，开始审计',exact:true})).toBeVisible();
  await expect(page.locator('.nav')).not.toContainText('训练');
  for(const width of [1440,390,320]) {
    await page.setViewportSize({width,height:width===1440?1000:844});
    await shot('upload-'+width);
  }
  const sample = await page.request.get(base+'/strategy-audits/sample.zip');
  expect(sample.ok()).toBe(true);
  await page.locator('[name=name]').fill('合成示例 · 金额可复算');
  await page.locator('[name=files]').setInputFiles({name:'synthetic.zip',mimeType:'application/zip',buffer:await sample.body()});
  await expect(page.locator('#audit-file-list')).toContainText('synthetic.zip');
  await page.locator('.audit-upload-options > summary').click();
  await page.locator('[name=opening_empty]').check();
  await page.locator('[name=opening_cash]').fill('10000');
  await page.getByRole('button',{name:'上传并核对',exact:true}).click();
  await expect(page.getByRole('heading',{name:'01 · 材料核对',exact:true})).toBeVisible();
  await shot('confirmation-320');
  await page.locator('[name=same_run]').check();
  await page.getByRole('button',{name:'一键审计',exact:true}).click();
  await expect(page.locator('#audit-money')).toContainText('改完有什么帮助');
  await expect(page.locator('#audit-money')).toContainText('不能相加');
  for(const width of [1440,390,320]) {
    await page.setViewportSize({width,height:width===1440?1000:844});
    await page.locator('#audit-money').scrollIntoViewIfNeeded();
    await shot('money-'+width,false);
  }
  const reportLink = await page.getByRole('link',{name:'查看完整报告与可复算明细',exact:true}).getAttribute('href');
  await page.goto(base+reportLink);
  const response = await page.request.get(base+reportLink+'/impact-money.csv');
  expect(response.ok()).toBe(true);
  expect(await response.text()).toContain('net_pnl');
  const standalone = await page.request.get(base+reportLink+'/report.html');
  expect(await standalone.text()).toContain('改完有什么帮助');
  expect(errors).toEqual([]);
  await writeFile(path.join(output,'acceptance.json'),JSON.stringify({status:'passed',base,report:reportLink,
    synthetic:true,widths:[1440,390,320],uploadConfirmAudit:true,monetaryCSV:true,standaloneReport:true,errors},null,2));
} finally {await browser.close();}
