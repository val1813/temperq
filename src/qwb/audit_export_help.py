"""Export instructions checked against public first-party documentation."""
from . import audit_materials as materials


def render():
    return '''<section class="export-help" id="platform-export-help"><h2>帮助：各平台怎么导出</h2>
<p>按平台展开步骤。不同回测时期分别下载，放入上方各自的格子；同一期的多份文件可一起选择。</p>
<details class="platform-help"><summary>BigQuant <small>区分结果页下载与 SDK / 旧版 raw_perf</small></summary><div class="platform-content">
<ol><li>进入 AIStudio，打开策略并完成回测。保留本次运行的策略代码，另存为 <code>.py</code> 或 <code>.txt</code>。</li>
<li>在回测结果中查看交易、持仓和日志。结果页提供下载按钮时，下载完整交易详情、持仓详情和日志；不要只保存收益曲线截图。官方知识库确认有“下载持仓详情”入口，旧版可能有条数限制，下载后请核对起止日期和行数。</li>
<li>若你使用 SDK：官方文档提供 <code>performance.raw_perf</code>（每日绩效 DataFrame）。可在 Notebook 中先查看 <code>raw_perf.columns</code>，再用 <code>raw_perf.to_csv("raw_perf.csv", index=False)</code> 保存；在文件列表选中文件、右键“下载”。旧引擎的 raw_perf 中 <code>TRA_FAC</code> 是交易、<code>POS_FAC</code> 是持仓、<code>LOG</code> 是日志。</li>
<li>将原生交易 / 持仓 CSV 与日志放入对应回测格子。嵌套的 raw_perf CSV、SDK 的 <code>report.json.gz</code> 目前不能直接当作标准交易表上传；需把交易、每日持仓、账户汇总按下方模板整理成三张表。不同 SDK / 引擎版本的字段结构需先核对。</li></ol>
<p>每日排名通常来自选股或预测模块的输出，需要单独保存；回测持仓不能恢复完整候选池排名。候选池行情、分钟价和历史上市退市信息也需要另行导出。</p>
<p class="source-links">官方来源：<a href="https://bigquant.com/wiki/doc/vac4qwmQr4" target="_blank" rel="noopener">SDK：查看回测结果</a> · <a href="https://bigquant.com/wiki/doc/ovTAM0xHqU" target="_blank" rel="noopener">旧引擎 raw_perf 字段</a> · <a href="https://bigquant.com/wiki/doc/KcZmfZ1ZVg" target="_blank" rel="noopener">CSV 保存与下载（历史文档）</a></p>
</div></details>
<details class="platform-help"><summary>聚宽 JoinQuant <small>研究环境读取已完成回测，无需重跑</small></summary><div class="platform-content">
<ol><li>打开“策略回测”，进入要审计的已完成回测详情。从地址栏 <code>backtestId=</code> 后复制回测 ID；不是策略的 <code>algorithmId</code>。</li>
<li>进入“研究环境”，新建 Python Notebook，粘贴 <a href="/static/joinquant_export.py" download>聚宽导出脚本</a> 的全部内容。把 <code>BACKTEST_ID</code> 换成刚才的 ID，然后运行。</li>
<li>脚本按官方接口调用 <code>get_backtest(ID)</code>，读取 <code>get_orders()</code> 交易详情、<code>get_positions()</code> 每日持仓、<code>get_balances()</code> 每日账户，生成 <code>temperq_trades.csv</code>、<code>temperq_holdings.csv</code>、<code>temperq_account.csv</code>，并将本次 CSV 打包为 ZIP。</li>
<li>在研究环境文件列表下载本次生成的 ZIP，放入一个回测时期格子；另上传与该次回测一致的策略代码。换一个回测 ID 再运行，即可准备下一期。不要把新旧时期的 CSV 混在同一格。</li></ol>
<p>导出的交易详情是订单层面的已成交量与成交价，不能恢复订单内每笔撮合的完整时间序列。缺少账户字段或费用时，脚本会说明或报错；不要把缺失费用补成零。日志可从回测结果另存后补充；排名需由原策略额外记录。</p>
<p class="source-links">官方来源：<a href="https://www.joinquant.com/help/api/help?name=api#get_backtest" target="_blank" rel="noopener">get_backtest：ID 与结果接口</a> · <a href="https://www.joinquant.com/help/api/help?name=api#write_file" target="_blank" rel="noopener">写入研究文件</a> · <a href="https://www.joinquant.com/research" target="_blank" rel="noopener">打开研究环境</a></p>
</div></details>
<details class="platform-help"><summary>米筐 / RQAlpha <small>使用报告目录中的 CSV</small></summary><div class="platform-content">
<ol><li>使用本地 RQAlpha 时，在原回测命令中加 <code>--report period_1_report</code>。例如下方命令；日期和资金请替换成你原来的设置，不要为了导出改变撮合、费率或策略参数。</li></ol>
<pre>rqalpha run -f strategy.py -s 2023-01-01 -e 2023-12-31 --account stock 1000000 --report period_1_report</pre>
<ol start="2"><li>用 Python 配置启动时，在 <code>mod.sys_analyser</code> 中设置 <code>enabled: True</code>、<code>report_save_path: "period_1_report"</code>。回测完成后，打开该目录。</li>
<li>取出 <code>trades.csv</code>、<code>stock_positions.csv</code>、<code>stock_account.csv</code>；保留原策略代码。它们是成交、每日持仓和账户汇总。可多选上传，也可将本次报告 CSV 压成一个 ZIP。日志需另存为 TXT。</li>
<li>换一个时期，使用另一个报告目录，例如 <code>period_2_report</code>，放入第二个回测格子。<code>--output-file result.pkl</code> 输出的是 pickle，不能直接上传；不要用 PDF、PNG 或 pickle 替代 CSV。</li></ol>
<p>米筐云端若提供“下载回测报告”，请检查下载包是否包含上述 CSV 或交易、持仓、账户明细。云端会员界面的具体按钮未完成登录核验；无法下载 CSV 时，可按模板整理或使用原有 RQAlpha 环境导出。</p>
<p class="source-links">官方来源：<a href="https://rqalpha.readthedocs.io/zh-cn/latest/intro/run_algorithm.html" target="_blank" rel="noopener">运行与配置</a> · <a href="https://github.com/ricequant/rqalpha/blob/master/rqalpha/mod/rqalpha_mod_sys_analyser/__init__.py" target="_blank" rel="noopener">--report 与 output_file</a> · <a href="https://github.com/ricequant/rqalpha/blob/master/rqalpha/mod/rqalpha_mod_sys_analyser/report/report.py" target="_blank" rel="noopener">CSV 文件清单</a></p>
</div></details>
<details class="platform-help"><summary>其他平台与补充材料 <small>标准 CSV 模板和字段要求</small></summary><div class="platform-content">
<ol><li>至少保留策略代码。财务审计建议同时提供成交、每日持仓、每日账户汇总；文件使用 UTF-8 或 GBK 编码。</li>
<li>下载对应模板，按表头填写真实数据。日期使用 <code>YYYY-MM-DD</code>，股票代码如 <code>600000.SH</code>，成交方向使用 <code>buy / sell</code>；缺失值不要编成零。</li>
<li>每日排名需包含未入选候选股票、分数及真实可用时点；候选池收盘价覆盖相同股票与时期。委托记录保留编号和状态；分钟价保留时点、量和原价格口径。</li>
<li>涨跌停价与上市退市日期来自当时可知的历史记录；不能拿今天仍上市的股票清单代替历史股票池。基础导出包通常不含全部这些材料。</li></ol>
</div></details>
<h3>材料模板</h3><div class="export-templates">''' + ''.join(
        f'<a href="/strategy-audits/templates/{kind}.zip">{materials.MATERIALS[kind][0]}</a>'
        for kind in ('fills', 'positions', 'snapshots', 'logs', 'scores', 'orders', 'prices', 'minute_prices', 'limits', 'listings')) + '''</div>
<p class="help-note">说明核对日期：2026-11-10。依据公开官方文档；登录后的界面可能随平台版本或权限不同。每份材料仍按表头识别类型，同一期不要重复提供同类文件。</p></section>'''
