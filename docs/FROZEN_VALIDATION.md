# 冻结队列、无标签预测与承诺标签评分

`audit validation`提供三阶段离线验证，补充已有合成基准。先固定样本材料哈希、审计设置、阈值、源码及依赖版本，承诺由复核者保管的标签文件SHA256；预测阶段不读标签，保存每例卡片、账务状态及错误；评分时核对原始标签字节并按预注册卡片类别统计。它不自动改阈值、运行上传的策略或启用账户。

本地文件哈希能检出不一致，**不能认证独立保管人、先后时间、未曾查看标签、标签正确性或样本代表性**。能同时改写全部材料和清单的管理员仍可重建一套一致文件。`external_holdout_declared`只表示提供者声明，不会自动变成独立盲测通过；所有评分保留`institutional_readiness=not_established`。

预测命令不接受标签文件参数，但没有为进程建立文件系统隔离，也无法识别嵌在材料、文件名、注释或其他字段里的隐含标签。独立复核者须保持标签与预测材料分离，核查是否存在泄漏；“无标签预测”仅指此命令的数据接口，不证明操作人从未接触标签。

## 三阶段命令

```powershell
python -m qwb.cli audit validation freeze --manifest manifest.json --protocol protocol.json --labels-sha256 <标签原始文件SHA256> --out plan.json --json
python -m qwb.cli audit validation predict --plan plan.json --materials-root <只读材料根目录> --out predictions.json --json
python -m qwb.cli audit validation score --plan plan.json --predictions predictions.json --labels labels.json --out score.json --json
```

退出码0表示该阶段完成且数值门槛通过（预测阶段全部例子账务verified）；1表示有不完整/错误预测，或评分failed/insufficient；2为格式、承诺、版本、材料变更或已有输出错误。退出码1仍保存完整报告。每个输出独占创建，旧结果不会覆盖；软件变化须新计划，不静默迁移旧计划。

## 输入格式

`manifest.json`没有标签字段。示例只说明字段，哈希占位符须替换为实际材料摘要。

```json
{
  "settings": {"commission_bp": 3, "minimum_commission": 5, "max_weight": 1, "thresholds": {}},
  "cases": [{
    "id": "case01", "group_id": "family01", "source_kind": "platform_export", "opening_cash": 100000,
    "files": {"case01/trades.csv": "<64位SHA256>", "case01/stock_account.csv": "<64位SHA256>"}
  }]
}
```

样本来源只能为`synthetic_fixture`、`simulator_native`或`platform_export`，不声称券商身份。仅接受CSV、TXT、PY、JSON静态文件；PY只静态解析，禁止执行；目前不支持ZIP/XLSX。每例1至16份文件，每文件8,000,000字节，全队列128,000,000字节，JSON24,000,000字节；仍受导入器单例40MB限制。缺文件、越界、符号链接、哈希不符在任何例子计算前拒绝。不依赖JSON对象字段顺序，按固定文件顺序导入。

`group_id`须覆盖同一策略家族的相关窗口、近似变体及重复回放。开发组编号写入协议，验证组与其重叠直接拒绝。材料路径不得跨例复用；按文件名及字节完全相同的输入不得拆成多个组。修改注释、重命名或相似策略的相关性无法由哈希认证，仍需独立复核组定义；新随机种子不自动形成独立策略家族。

`protocol.json`须在预测前完整声明所有要评分的缺陷类型及卡片类别，不根据预测事后添加指标或修改门槛：

```json
{
  "cohort_kind": "external_holdout_declared",
  "development_group_ids": ["development_family"],
  "defects": [{"id": "price_chain", "card_ids": ["price_jump"], "recall_min": 0.9, "minimum_groups": 30}],
  "clean_failure_max": 0.05,
  "minimum_clean_groups": 100
}
```

数字为项目拟议目标及例子，不是监管标准。原规则登记仍含未经独立校准的经验阈值，此工具不会把它们自动标为已校准。

标签文件必须覆盖所有冻结例子，不能只交成功例子：

```json
{"cases": [
  {"case_id": "case01", "defect_id": null, "manifested": false, "acceptable_act_card_ids": ["fees"]},
  {"case_id": "case02", "defect_id": "price_chain", "manifested": true, "acceptable_act_card_ids": []}
]}
```

干净样本的`defect_id=null`且`manifested=false`。标签只允许协议中已注册缺陷；`manifested=false`的缺陷不纳入召回分母，也不当作检出。`acceptable_act_card_ids`须在标签承诺前由复核者逐例确定，例如已知真实手续费问题对应的合理act。不能看完预测再放宽清单；即使只是JSON空白变化也会违反标签原始字节承诺。

## 统计和门槛

显现缺陷只在预注册卡片类别有review或act时计检出。其他类别新增卡不计该缺陷命中；act与review分别列出。这是类别匹配，不是卡片已正确解释根因的证明，根因仍需复核。

不完整账务或运行错误保留在结果中，缺陷例按漏检计；干净例计入“错误act或运行不完整”失败事件。不会通过丢掉错误例提高检出率或降低误报率。缺文件/哈希错误直接拒绝整批预测，原计划仍保留。

召回以策略组为单位：同组该类所有已显现例均命中才算组成功。干净组只要一例出现未预先认可的act或运行不完整就算组失败。报告同时保留逐例命中、全部act、全部review、未认可act和运行错误，不能把复合失败率误写成纯粹误报率。

每项使用双侧Clopper–Pearson精确二项区间；对“所有预注册缺陷召回+干净组失败率”做Bonferroni校正，使各区间在独立同分布组抽样假设下具有至少95%的整体覆盖。指标之间不必相互独立；同一指标内的策略组独立同分布和代表性仍是未经认证的假设。组内窗口数量不同会改变“全命中/任一失败”的难度，不能不加说明横比不同队列。

召回下界须达到预注册下限，干净组失败率上界须不高于上限，同时满足各自最少组数。少于最少组数为insufficient；有failed时总体为failed，不被另一项insufficient遮蔽。零事件仍有非零上界，单个策略家族无法证明机构准入，即使所有例子都命中。

## 复验与限制

```powershell
python -m pytest -q tests/test_audit_validation.py
python scripts/verify_frozen_validation.py --out <全新验证目录>
```

验证器实际执行冻结、预测、评分、更改标签拒绝、旧输出覆盖拒绝、材料变更拒绝及运行错误注入评分。四例同一合成生成器归入一个组；预计类别检出2/2，数值门槛failed是正确结果，不是机构盲测通过。注入错误预测明确单独标识，只验证评分逻辑；不冒充检测器真的产生了这些运行错误。

冻结绑定整个qwb包的Python源码字节、Python/系统版本及已安装分发包的报告版本。它不验证第三方二进制/外部源码字节、容器镜像、操作系统恢复、真实API来源或库内全局状态；不是供应链签名。计划和预测均可被持有全部材料的管理员重签，独立方签署与托管另行验收。外部标签保管、独立未见家族及真实前向时间仍须补齐。
