// Open the audit edition's optional evidence disclosure through a real click.
// The full workbench renders the same forms directly.
export async function revealEvidence(page) {
  const summary=page.locator('summary').filter({hasText:/^独立成交、账户与风险证据（选填）$/});
  if(await summary.count() && await summary.locator('..').getAttribute('open')===null) await summary.click();
}
