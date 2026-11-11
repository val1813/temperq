# 日终资金、持仓及流水独立核对

`audit account-reconcile` 对比单账户、CNY、只做多现货的两份规范账本，并分别从各自期初与业务记录推演日终现金和整数库存。它补充现有订单层 `audit reconcile`；不使用回测成交生成券商内部账，不写用户账本，不认证外部来源。

## 运行

```powershell
.venv\Scripts\qwb audit account-reconcile --internal <内部账本目录> --broker <独立外部账本目录> --calendar <日历.csv> --source-kind broker_export --out <全新报告.json> --json
```

`--source-kind` 必须主动填写：`broker_export`、`simulator_native` 或 `synthetic_fixture`。输出独占创建，已有文件不覆盖；不提供输出路径时仅返回 JSON。退出码 0 表示声明范围内 matched，1 表示 breaks 或 insufficient，2 表示坏输入、缺文件或输出已存在。

两目录各需五个文件：`scope.json`、`balances.csv`、`positions.csv`、`fills.csv`、`events.csv`。单文件最多8MB，单CSV最多十万条记录。全部为UTF-8；表头须完整、唯一且没有未知字段。读取命名文件时不会导入、执行目录中的代码。CLI 同时传入的日历为独立字段约定，不从账户快照反推交易日。

## 范围与期初

每边的 `scope.json` 都须填写以下字段且完全一致：

```json
{
  "schema_version": "account_reconciliation_scope_v1",
  "account_id": "synthetic-account",
  "currency": "CNY",
  "start_date": "2025-01-03",
  "end_date": "2025-01-07",
  "cash_basis": "trade_date_total_cash",
  "positions_complete": true,
  "fills_complete": true,
  "events_complete": true
}
```

示例标识是合成账户。真实资料用统一的脱敏账户标识；不同系统的账户及订单/事件身份须先提供明确映射，不能凭价格相近拼接。币种只支持CNY。现金是**成交日总现金**，不接受可用资金、冻结资金或结算资金混用。三个完整性字段必须是真正的JSON布尔值 `true`，这只是来源声明，程序无法证明未提交业务从未发生。

期初快照日期固定为 `start_date` 前一个自然日。`balances.csv` 须含期初及区间内每个开市日；`positions.csv` 在这些日期记录全部非零持仓。即使期初为空，也须明确给出期初现金及零持仓数。缺任何资金快照为 insufficient；两边同时少一天也不能通过。声明期间最多3661个自然日，起止日均须是开市快照日。

日历 CSV 表头 `date,is_open`，须覆盖声明期间内**所有自然日**，开市为1、闭市为0。重复、缺日、未知标识、区间外日期和闭市的起止日均拒绝。日历哈希保留，来源真实性未认证。

## 五份文件字段

| 文件 | 表头 |
| --- | --- |
| balances.csv | `account_id,currency,date,cash,market_value,equity,position_count` |
| positions.csv | `account_id,currency,date,code,quantity,price,market_value` |
| fills.csv | `account_id,currency,date,order_id,trade_id,code,side,quantity,price,fee` |
| events.csv | `account_id,currency,date,event_id,kind,code,cash_delta,quantity_delta,source_ref` |

账户和币种每行必须一致。日期为 YYYY-MM-DD。金额以元、价格以元/股或元/份、数量以整数股/份表示，不能混用手数。数字接受最多16位整数及8位小数的普通十进制，不接受科学计数法、NaN和Infinity；数量绝对值最多一万亿。只支持非负总现金及只做多库存；持仓CSV只记录正持仓，零持仓省略，并通过资金表的 `position_count` 声明完整性。价格必须大于零；持仓市值和总市值非负。

成交 `side` 为 buy/sell；量、价为正，费用非负。一个交易日内成交号唯一，订单号需有双方共同身份。双方成交可有不同拆分，订单级数量、金额和费用按原有明确身份逻辑核对。非交易日成交拒绝。完整声明下，只有表头的成交或事件CSV允许表示无业务；缺文件不代表无业务。

事件 `event_id` 在同日内唯一，`source_ref` 指向原始流水/公告/回报，不能写密钥。事件的编号/日期/种类/证券须对应，程序不猜映射：

| kind | cash_delta | quantity_delta | code |
| --- | --- | --- | --- |
| deposit / interest | 正数 | 0 | 留空 |
| withdrawal / fee / tax | 负数 | 0 | 留空 |
| dividend | 正数 | 0 | 必填 |
| split | 0 | 非零有符号整数 | 必填 |

split 用实际到账增减股数表达送转/拆并股；不能据此证明权益资格或比例正确。现金股息、独立税费分别入账；成交费用已在 fills 中列入时不得在 events 再扣一次。事件可发生在周末，按业务日滚入下一开市快照。没有“未知调整”类型可用于冲平残差；权利认购、配股价款、衍生品、融券、汇率、成本基础和复杂结算尚不支持，需保留材料而不能强行套此格式。

## 核对逻辑与结果

双方先按日期对比现金、市值、权益和持仓数，再按日期/证券对比数量、估值价和市值。各自还检查 `equity = cash + market_value`、持仓数、市值合计及逐证券 `market_value = quantity × price`。

独立推演各边账本，使用自己的期初、成交及事件：

- 现金：期初现金 − 买入金额 + 卖出金额 − 成交费用 + 已记录事件现金变化。
- 库存：期初库存 + 买入量 − 卖出量 + 已记录事件股数变化。

推演从期初持续累计，**不拿每天的错误快照重置推演值**。这能检出双方共同漏记入金或送转而保留正确日终余额的情况。逐日明细同时保留 expected、actual、残差、状态与证券身份，不跨日期、证券、订单或事件抵消。资金容差每项最多0.01元，可用 `--money-tolerance 0` 收紧；数量及双方估值价格精确相等。参数不会自动调大来吞掉差异。

报告保存双方每文件SHA-256、日历SHA-256、双方输入是否逐字节相同、逐项差异、订单层结果及各边推演检查。双方零成交时，嵌套订单层报告仍为 insufficient；在完整业务声明、完整快照且所有推演通过的情况下，账户层可为 matched。

## 合成样例与实际验证

[完整合成材料](../examples/account_reconciliation_synthetic/scope.json) 位于 `examples/account_reconciliation_synthetic`。只为演示格式，可将同一目录传给双方并显式标 synthetic_fixture；结果会记录 same_input_bytes=true，不构成独立证据。已知答案为周末入金500元、股息20元、送转增加200股、三笔交易及出金/利息/税费后，期末现金10731元、零持仓。

`scripts/verify_account_reconciliation.py` 接受已有的固定原生模拟 probe 与 SSE 日历缓存。回调成交自行重建一边账本，原生逐日现金/库存构成另一边，估值共用原收盘价。它通过真实 CLI 运行正常核对和现金加1元、持仓加100股、双方共同缺一天的负向检查。全部只写全新输出目录，原probe读前后核哈希；不重跑用户策略或连接券商。原生CSV二进制浮点尾差仅在距分币金额不超过0.00000001元时归一到分，逐日保存原差；更大的分币以下差额拒绝，失败产物保留。

本轮已完成历史真实价格模拟账户36日/38笔，以及严格开盘全合成8日/2笔的上述检查。真实价格旧模式仍有已披露时点限制；新账务核对不会将其升级为真实执行验收。完整测试数字与证据路径见 [工程验收记录](INSTITUTIONAL_ACCEPTANCE_20261105.md)。除CLI外，现可在策略导入页“账户、归因与风险证据”分别上传两份账户ZIP与日历，保存输入、代码版本及复算结果，另见 [网页独立证据](VALIDATION_EVIDENCE.md)。

matched 只表明提交材料在声明范围自洽。它不认证券商来源、原始材料完整性、价格可得时间、分红权益/税率、可用/结算资金、T+1、盘中透支或机构准入。若双方同时遗漏一笔业务并一致改写全部余额，软件无法从这些文件恢复未提交事实。
