"""Public audit-quality evidence; no strategy performance or private materials."""
import argparse
import hashlib
import json
from pathlib import Path

from build_real_strategy_cases import shell

LABELS = {
    'r03': ('原始导出r03', '账目闭合，分钟量覆盖完整'),
    'r38': ('原始导出r38', '账目闭合，分钟量覆盖完整'),
    's14': ('原始导出s14', '账目闭合，分钟量覆盖完整'),
    's38': ('原始导出s38', '账目闭合，分钟量覆盖完整'),
    's22': ('原始导出s22', '账目闭合，分钟量覆盖完整'),
    'reverse_fills': ('倒序排列成交行', '资金与参与率结论不随文件行序变化'),
    'fragment_one_fill': ('同一订单的一笔成交拆成两行', '总数量、总费用不变，核心审计结论不变'),
    'missing_minute': ('删除一个分钟量记录', '指出缺失的日期、证券及09:35，覆盖率下降'),
    'conflicting_minute': ('增加同分钟冲突成交量', '指出冲突分钟，不取最后一行充数'),
    'signal_future_date': ('把一条信号可用时刻改到次日', '新增一条成交早于信号的发现'),
    'signal_missing_time': ('删除一条信号可用时刻', '明确报告未覆盖，不判为通过'),
    'omit_one_fill': ('删除一笔成交，其余账户材料不变', '账目不能继续通过'),
    'change_one_fee': ('只改一笔手续费，其余账户材料不变', '账目不能继续通过'),
}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(root, out):
    manifest = read(root / 'manifest.json')
    if not manifest['complete'] or not manifest['all_checks_passed'] or not manifest['programs_unchanged']:
        raise ValueError('Qualification did not complete successfully')
    for name, digest in manifest['files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path) != digest:
            raise ValueError('Qualification evidence changed')
    results, protocol = read(root / 'results.json'), read(root / 'protocol.json')
    if set(x['id'] for x in results) != set(LABELS) or len(results) != 13:
        raise ValueError('All baselines and mutations must be retained')
    public = {'kind': 'known_answer_development_audit_regression_not_blind',
              'strategy_execution': False, 'strategy_parameter_changes': False,
              'independent_strategy_groups': 1, 'qualification_cases': 13,
              'scope': '五份相关既有原始导出，加八项已知答案材料变换；不代表真实策略总体准确率、独立盲测或前瞻验证。',
              'evidence_manifest_sha256': sha(root / 'manifest.json'),
              'auditor_source_sha256': protocol['programs'],
              'cases': results}
    body = '<p class="eyebrow">TemperQ审计器 · 固定材料验收</p><h1>查得对不对，<br>先拿已知答案检验。</h1><p class="lead">保持策略不变，只改变导出材料：删成交、改费用、删分钟量、把信号日期移到次日。看审计器是否查出对应问题；材料只是换了行序时，结论是否保持不变。</p>'
    body += '<p class="note">这是开发回归，材料与预期答案此前已知。五份导出来自同一四ETF策略家族，不是五份独立验证。本页13项全部满足预定检查，不等于TemperQ对真实策略的准确率为100%。未执行策略代码，也未优化策略参数。</p>'
    body += '<section><h2>这轮直接修了哪些审计问题</h2><ul><li><strong>现金要算到本分钟前。</strong>上一收盘现金必须加减当天更早成交及费用，避免回款依赖误报或漏报。</li><li><strong>信号时间必须带日期和时区。</strong>不再只比钟点；冲突或缺失可用时刻列为未覆盖。</li><li><strong>开盘缺条与时段边界分开。</strong>五分钟量窗口不索要不存在的开盘前连续交易分钟，真实缺条仍逐项列出。</li><li><strong>95%分位不能盖住峰值。</strong>分位未过线但少数窗口超线时，单独提示，不写成全部正常。</li><li><strong>发现要能复核。</strong>新增资金衔接、信号时序、时序未覆盖和成交量补件CSV，报告保留日期、证券与计算依据。</li></ul></section>'
    body += '<section><h2>13项固定材料检查，完整展示</h2><div class="table-wrap"><table><thead><tr><th>材料或变换</th><th>预定判断</th><th>结果</th></tr></thead><tbody>'
    for row in results:
        label, expectation = LABELS[row['id']]
        body += f'<tr><th>{label}</th><td>{expectation}</td><td>{"符合预期" if row["check_passed"] else "未达到"}</td></tr>'
    body += '</tbody></table></div></section>'
    base = results[0]
    damaged = next(x for x in results if x['id'] == 'missing_minute')
    miss = damaged['capacity']['uncovered'][0]
    body += f'<section><h2>补件要求具体到哪一条</h2><p>原r03材料的分钟量覆盖为{base["capacity"]["fill_coverage"]:.0%}。仅删除一条记录后，覆盖降为{damaged["capacity"]["fill_coverage"]:.2%}，其余账户材料仍能闭合。审计器明确列出：{miss["date"]}，{miss["code"]}，缺少{", ".join(miss["missing_minutes"])}的成交量。</p><p>这说明“账对上”与“某项成交证据齐全”是两种判断；不能因为净值还能核对，就让缺件的执行检查通过。</p></section>'
    body += '<section><h2>这里没有替用户下什么结论</h2><p>同分钟回款依赖是一项需要核对委托与成交先后顺序的事实，不自动等于违规或策略无效。10%参与率是未校准情景门槛；峰值或分位超线不等于已证实不能成交。缺信号时刻不等于用了未来数据，而是该项尚不能判断。</p><p>本轮只验收这些已知缺陷与表示变化，不为其余审计模块背书。没有真实外部标签、独立保管或足够多非相关策略，不能用本页计算普遍检出率。</p></section>'
    body += f'<details><summary>派生结果与证据摘要</summary><p><a href="audit-quality.json">下载13项检查的派生结果JSON</a>。完整原材料、运行前协议、报告、明细及文件摘要在本地留存，不随网站重发。</p><p>完整证据清单SHA256：<code>{public["evidence_manifest_sha256"]}</code></p><p>本页不展示策略收益，也不提供买卖或调参建议。旧策略对照仅是此前的研究记录，不作为审计器准确率证明。</p></details>'
    out.mkdir(parents=True, exist_ok=False)
    (out / 'audit-quality.html').write_text(shell('审计器固定材料验收', body), encoding='utf-8')
    (out / 'audit-quality.json').write_text(json.dumps(public, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Generated audit-quality evidence: {out}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--qualification-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    build(args.qualification_root, args.out)
