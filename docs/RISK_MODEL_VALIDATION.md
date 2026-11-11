# 固定截止时点的风险估计与留出期诊断

`audit risk-model` 针对显式给定的行业／风格模型，估计因子与证券残差的联合协方差，计算组合、基准和主动风险，并在独立于计算的后续区间检查实际波动和尾部超限。它不估计因子定义或因子收益，不选策略、不改账户，不把统计检验不拒绝解释为模型已校准。

CLI及策略导入页“账户、归因与风险证据 → 风险估计与留出诊断”均已接入。网页保存七份原材料、绑定策略材料和计算版本，并在打开/下载时重新计算，见[独立证据入口](VALIDATION_EVIDENCE.md)。没有自动并入回测审计卡或执行规则。已有因子归因入口继续按其自己的五文件约定工作。

## 运行

```powershell
.venv\Scripts\qwb audit risk-model --scope examples/risk_model_synthetic/scope.json --calendar examples/risk_model_synthetic/calendar.csv --periods examples/risk_model_synthetic/periods.csv --weights examples/risk_model_synthetic/weights.csv --exposures examples/risk_model_synthetic/exposures.csv --factor-returns examples/risk_model_synthetic/factor_returns.csv --asset-returns examples/risk_model_synthetic/asset_returns.csv --source-kind synthetic_fixture --out <全新报告.json> --json
```

七份材料都必须提供。输出独占创建，保留原输入摘要、计算代码摘要、数值库版本及实际消耗的模型材料。退出码0表示预测已计算且留出材料足够进行诊断；1表示材料不足（可能保留已完整计算的预测，但不对缺失的留出样本做部分校准）；2表示输入格式/口径错误或输出已存在。单项计算成功不代表任何机构准入。

## 输入与时点

来源主动选择`historical_data`、`synthetic_fixture`或`historical_returns_synthetic_exposures`，均不认证来源。混合真实收益与合成暴露必须声明第三种来源，不能借用真实价格把合成模型改称历史因子校准。

范围示例见[scope.json](../examples/risk_model_synthetic/scope.json)。JSON字段必须精确包含：

| 字段 | 约定 |
| --- | --- |
| schema_version | `risk_model_scope_v1` |
| start_time / cutoff_time / end_time | 带时区，起点＜训练截止＜留出终点，均对应中国市场15:00收盘 |
| assets | 1至100个唯一非现金证券，显式模型宇宙，不按缺失情况删证券 |
| factors | 1至20个因子，沿用[因子归因](FACTOR_ATTRIBUTION.md)的name/kind/definition/unit定义 |
| model_definition | 明确收益、复权、分红、模型历史版本、因子收益来源与残差解释；最多4096字 |
| holdings_complete / cash_return | 分别必须为JSON布尔true及`zero` |
| max_exposure_age_days | 0至366，按暴露观测时间计算年龄，修订时间不刷新年龄 |
| min_train_periods / min_holdout_periods | 明确配置，分别至少3和2；小例子只验证计算，不是充分样本建议 |
| shrinkage | 0至1普通十进制字符串，固定向联合矩阵对角目标收缩，不用留出期选值 |
| confidence_level | 0.9至0.999普通十进制字符串，左尾损失VaR的置信水平 |
| periods_per_year | 1至366整数；平方根年化依赖可比区间和时间独立假设 |

`calendar.csv`为`date,is_open`，0/1，完整覆盖起止日期之间每个自然日。起止必须开市；每个收益区间必须恰好连接两个相邻开市日的15:00收盘。不能把缺失交易日两边的单日收益拼成一个跨日收益。日历来自输入声明，不认证其与独立交易所日历一致。

其余CSV表头：

```csv
periods: period_id,start_time,end_time,source_ref
weights: book,code,weight,asof_time,available_at,source_ref
exposures: code,factor,asof_time,available_at,exposure,source_ref
factor_returns: period_id,factor,return,available_at,source_ref
asset_returns: period_id,code,return,available_at,source_ref
```

`periods`连续覆盖全范围，cutoff必须落在区间边界。截止时点及之前结束的全部区间是训练期，之后是留出期，不重新挑选训练子样本。

`weights`只需一套截止时点组合/基准完整权重，两个book都须有CASH行，即使权重为0；证券只可来自assets。全部权重非负、不超过1、合计为1（表示误差最多1e−12），不自动归一。权重的观测及可得时间均等于cutoff。未列的assets在该book为零权重，但仍属于显式风险宇宙并要求历史完整。

`exposures`沿用双时间版本约定：每个训练区间采用其期初已可得的最新观测，同观测采用最新已发布修订；预测采用cutoff可得的版本。晚发布的旧观测不能盖过已可得的新观测。所有assets每个声明因子都要显式提供，零暴露不等于缺数据；行业份额之和为1，风格允许负值。CASH不提供暴露。

`asset_returns`是同区间、同币种的简单总收益小数，须已经处理分红、拆分和资金流口径；严格大于−1且不超过100。`factor_returns`采用给定因子的同区间小数收益，范围−100至100。完整收益的available_at不得早于区间结束。训练收益必须在cutoff已可得；晚发布版本不能回填进训练。当前收益表每个身份只能提交一个明确时点快照，重复修订不能静默去重；上游须形成可复核版本。历史发布时间不明时，不得用归档事件日期伪造发布时间。

训练需要全部assets和全部因子收益；任意缺失或不可得都会使预测不足，不删日、不补零。留出评价需要全部assets的收益；不要求留出期因子收益或暴露，因为评价直接使用证券真实收益，不借助未来模型解释回填预测。未来因子/暴露即使提供也不进入训练。留出任一证券缺一期时，不给部分样本的校准结果；完整预测仍可保留。

单文件最多8MiB，全部最多48MiB；单CSV最多十万行，最多2000个区间，区间数加期末快照后乘证券数及因子数最多250000项。字段有长度界限，拒绝重复表头、额外列、空字段、NaN、Infinity及科学记数法。

## 风险计算与分解

每个训练区间t，期初可得证券暴露为B_t，给定因子收益为F_t，证券实际收益为R_t：

`e_t = R_t − B_t F_t`，联合向量`z_t = [F_t, e_t]`。

使用完整训练期去均值样本协方差S（分母n−1），按已声明λ计算`Σ = (1−λ) S + λ diag(S)`。不使用矩阵求逆；报告秩、维度及最小特征值。奇异矩阵被披露，不通过偷偷加方差下限来伪装充分样本。仅负的组合方差舍入尾差可归零，并单列调整量；实质负方差拒绝。

预测暴露B_0只取cutoff已可得版本。book权重为w，联合载荷`c = [wᵀB_0, wᵀ]`，方差`V = cᵀΣc`。分开列示：

- 因子方差。
- 残差方差，包含证券残差之间的协方差。
- 因子与残差交叉方差，可正可负。
- 联合分量方差贡献`c_j(Σc)_j`及波动贡献；另用`[B_0,I]Σ[B_0,I]ᵀ`给出证券协方差及逐证券贡献。

主动风险使用`w_portfolio − w_benchmark`重新计算，包含双方共享证券和协方差，不把两个波动相减，也不假设组合和基准独立。贡献可以为负；方差份额不是非负预算建议。没有风险预算优化或自动限仓。

当暴露恒定且λ=0时，联合模型对证券风险的投影可退化为证券样本协方差的分解。闭合仅证明代数及数值一致，不能用作模型预测有效的证据。

## 留出诊断与边界

预测权重保持cutoff的固定目标，每一留出区间用`wᵀR_t`计算模型组合简单收益。这相当于每期开头无成本再平衡到该固定权重，现金收益为零；不是买入持有、真实策略净值或真实执行回放。这里观察的证券收益独立于计算所用的未来因子材料。

输出逐期观察收益、实际波动、均值、RMS、实际/预测波动比及条件于IID高斯的95%波动比区间。高斯单期VaR/ES使用零条件均值，训练均值另列；ES仅计算模型值，没有完成独立ES回测。

损失严格大于VaR才计超限，输出次数、频率、标记、预期次数、精确二项检验和Kupiec覆盖率似然比检验。另列相邻超限0/1转移数、Christoffersen独立性和条件覆盖诊断；没有足够0/1状态变化时不给独立性p值，转移行样本小于5时明确标记。检验有IID、渐近或分布前提，不替代压力状态覆盖。预期超限次数小于5时单列样本太少，不以“大p值”宣布通过。

由于总区间最多2000期，99.9%置信水平下无法取得5次预期尾部超限，不能用此接口的小样本为该尾部水平给出充分校准结论。样本上限和最低次数提示都是工程/诊断约定，不是机构或监管认证标准。

`model_digest`只绑定实际可得的训练材料、截止权重/暴露、配置、计算代码和数值库版本；留出收益扰动不应改变它。报告另保存全部输入文件哈希，故未来输入改动仍可追溯。回顾性分期不证明模型当年已冻结，也不证明外部独立、预注册或前向验证。所有输出保持`risk_model_calibrated=false`和`institutional_readiness=not_established`。

## 已知答案与复验

[合成示例](../examples/risk_model_synthetic/scope.json)含4期训练及3期留出，一因子F与残差u训练正交，证券收益A=F+u、B=2.5F+u，给定暴露A=1、B=2。因此B残差含0.5F+u，测试不能丢掉因子/残差交叉项。

组合A/B/CASH权重0.6/0.2/0.2，手算训练方差为**0.0005026666666666667**；基准0.5/0.5/0为0.0009416666666666667；主动为0.0000776666666666667。这不是实盘风险测量，也不是足够校准样本。

```powershell
.venv\Scripts\python.exe scripts/verify_risk_model.py --output <全新目录>
.venv\Scripts\python.exe -m pytest -q tests/test_audit_risk_model.py
```

验证器实际调用CLI，覆盖已知答案、留出冲击、未来因子变化、未来暴露修订、缺训练证券、晚发布训练收益、缺留出证券、日历缺口、未来权重及拒绝覆盖旧输出十类场景。报告保存库版本、代码哈希及输入原样。单元测试另含现金/零主动风险、重复身份、行业份额、收缩、尾部压力与超限聚集。

E盘已有API样本尚不提供本接口所需的完整历史因子模型、暴露发布版本及收益时点快照，本轮没有生成“真实因子风险校准通过”。实际接口范围见[数据能力实测](INSTITUTIONAL_DATA_SOURCES_20261106.md)。
