"""Every rule an audit conclusion rests on, with its kind and source.

Three kinds, never mixed: ``fact`` (exchange and tax rules, accounting identities),
``method`` (a published or documented method), and ``threshold`` (a judgement call
by the project, not yet tested on samples). Only thresholds may be adjusted.
Reports store the values they used, so a changed threshold yields a new report.
"""
from __future__ import annotations

from dataclasses import dataclass, field

VERSION = "audit_rules_v2"


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str                     # fact | method | threshold
    text: str
    source: str
    value: object = None
    unit: str = ""
    applies_to: tuple = ("stock", "fund", "bond")
    range: tuple | None = None    # thresholds only: (min, max)

    def __post_init__(self):
        if self.kind not in {"fact", "method", "threshold"}:
            raise ValueError(f"{self.id}: unknown rule kind {self.kind}")
        if (self.kind == "threshold") != (self.range is not None):
            raise ValueError(f"{self.id}: thresholds, and only thresholds, need an allowed range")


UNTESTED = "经验阈值，未经样本检验"

RULES = {r.id: r for r in (
    # Facts
    Rule("F-ACCOUNT", "fact", "权益 = 现金 + 持仓市值；持仓变化 = 成交；现金变化 = 成交金额 + 费用", "账务恒等式"),
    Rule("F-TICK-STOCK", "fact", "股票最小报价单位 0.01 元", "沪深交易所交易规则", .01, "元", ("stock",)),
    Rule("F-TICK-FUND", "fact", "ETF、基金、债券最小报价单位 0.001 元", "沪深交易所交易规则", .001, "元", ("fund", "bond")),
    Rule("F-STAMP", "fact", "卖出股票按成交日适用税率缴印花税；2023-08-28 起 0.05%，此前 0.1%；基金和债券免征",
         "财政部、税务总局公告"),
    Rule("F-MIN-COMMISSION", "fact", "佣金不足最低佣金时按最低佣金收取", "券商佣金规则；最低额由合同约定"),
    # Methods
    Rule("M-TICK-HALF", "method", "固定原成交、按每边半个最小价位计算价差压力情景；不认证实盘成本下限", "作者 STRUCT 稿（SSRN，未经同行评审）；适用边界见 audit_research.tick_analysis"),
    Rule("M-FIFO", "method", "按先进先出配对买卖，计算每笔已平仓交易的持有期和盈亏", "通用会计配对方法"),
    Rule("M-SLIPPAGE", "method", "回测已计的滑点按平台文档换算为每边成本，价差情景只补差额",
         "聚宽 set_slippage 文档；RQAlpha 滑点模型文档"),
    Rule("M-RANK-IC", "method", "每个调仓日，用全部候选标的的分数与到下一调仓日的收益做秩相关（Spearman IC）",
         "因子检验常用方法（信息系数）"),
    Rule("M-PREMISE-HAC", "method", "前提检验的主 t 值 = IC 序列均值 / Newey-West（Bartlett 核）标准误；滞后期数 L = floor(4(n/100)^(2/9))，"
         "至少 1，按信号期计；期数少于 max(30, 4(L+1)) 不给 t；同时报告 L/2、L、2L；普通 t 只作诊断",
         "Newey & West (1987, Econometrica)；滞后规则为常用经验式，非最优选择"),
    Rule("M-PREMISE-POWER", "method", "最小可检测 IC = (显著性 t 门槛 + 0.84) × IC 标准差 / √期数，0.84 对应八成检验力",
         "统计检验力的正态近似（单侧）"),
    Rule("M-RANK-REPLAY", "method", "用策略导出的每日排名回放换仓次数；不重算收益", "本项目，见 audit_signals"),
    # Thresholds
    Rule("T-HOLD-SHORT", "threshold", "持有不超过这个自然日数算短持有", UNTESTED, 5, "自然日", range=(1, 60)),
    Rule("T-PRICE-LOW", "threshold", "低于这个价格的股票算低价股", UNTESTED, 5., "元", ("stock",), (1., 50.)),
    Rule("T-MIN-COMM-MATERIAL", "threshold", "最低佣金多付超过这个金额才建议处理", UNTESTED, 500., "元", range=(0., 100000.)),
    Rule("T-BUFFER-RANK", "threshold", "保留带：持有的标的排进前几名就不卖", UNTESTED, 2, "名", range=(2, 10)),
    Rule("T-WHIPSAW-DAYS", "threshold", "卖出后这么多个信号日内又排回第一，算来回", UNTESTED, 5, "信号日", range=(1, 60)),
    Rule("T-CHURN-SHARE", "threshold", "保留带少换的次数占换仓次数达到这个比例才建议改", UNTESTED, .2, "比例", range=(0., 1.)),
    Rule("T-CHURN-MIN", "threshold", "保留带至少少换这么多次才建议改", UNTESTED, 3, "次", range=(1, 100)),
    Rule("T-CASH-BUFFER", "threshold", "全仓下单时预留的现金比例", UNTESTED, .01, "比例", range=(.001, .1)),
    Rule("T-CASH-SHORT", "threshold", "拒单所差金额不超过下单金额的这个比例，算“差一点”", "基准校准：0.002–0.03 检出都是 8/8、无缺陷误报 0/24，对检出不敏感", .01, "比例", range=(.001, .1)),
    Rule("T-FEE-GAP-SHARE", "threshold", "费用差额占复算费用达到这个比例才出现费用卡", UNTESTED, .01, "比例", range=(0., .1)),
    Rule("T-DD-TOLERANCE", "threshold", "改版最大回撤比原版多出这么多（百分点）就不算更好", UNTESTED, .01, "比例", range=(0., .2)),
    Rule("T-PREMISE-T", "threshold", "IC 均值的 t 统计量达到这个值，才说前提成立或反了（全期与各分段相同）", UNTESTED, 2., "", range=(1., 5.)),
    Rule("T-PREMISE-MIN-PERIODS", "threshold", "至少这么多个调仓周期才检验前提", UNTESTED, 20, "期", range=(5, 500)),
    Rule("T-TIMING-SHARE", "threshold", "换个成交时点，盈亏变化达到净盈亏的这个比例，就说结论依赖成交时点", UNTESTED, .25, "比例", range=(.01, 1.)),
    Rule("M-FILL-TIMING", "method", "固定原成交股数和费用，把成交价换成同日其他分钟的收盘价，比较盈亏；不重跑策略决策", "本项目，见 audit_timing"),
    Rule("T-FILL-BEST-SHARE", "threshold", "成交价落在当天最有利一成以内的比例达到这个值（且显著高于随机的 10%），就标为可疑", UNTESTED, .25, "比例", range=(.12, .9)),
    Rule("M-FILL-RANGE", "method", "把每笔成交价放进当天最高最低价区间，和均匀分布比较；买在低位、卖在高位的比例显著超过随机即可疑", "本项目，见 audit_fill_quality"),
    Rule("M-FILL-FLOOR", "method", "每笔买入按成交后 10 分钟内的最高价、卖出按最低价重算，股数费用不变；是执行的悲观下限，不是估计", "本项目，见 audit_fill_quality"),
    Rule("M-SEQUENCE", "method", "从期初或上一收盘现金滚入当日更早成交及费用，判断同分钟回款依赖；缺成交时间或现金到账时刻的日期不判定。信号按完整日期、时区及时间比较，缺失或冲突记录不判通过", "本项目，见 audit_sequence"),
    Rule("T-CAPITAL-L1", "threshold", "整数手数下目标权重的乐观最小偏离（L1，资产与现金两侧）超过这个值，即证明该资金规模兑现不了", UNTESTED, .2, "比例", range=(.01, 2.)),
    Rule("M-CAPITAL-FLOOR", "method", "在整手、预算约束下求目标权重的最小 L1 偏离（混合整数规划），忽略费用、T+1、量限和路径，是乐观下界", "整数规划，本项目见 audit_capital"),
    Rule("M-FULFILMENT", "method", "逐标的逐日盈亏按前一日收盘是否持有、是否在当日目标内分组；收盘持仓按次日开盘价计隔夜盈亏", "会计分组，本项目见 audit_fulfilment"),
    Rule("M-MARKET", "method", "日收益对平台基准日收益做最小二乘回归，得 β、年化超额及其 t 值；按日对数收益看最好 1% 的日子的贡献", "CAPM 回归；本项目见 audit_market"),
    Rule("M-LIMIT-LOCK", "method", "全天只有一个价格且等于涨停（跌停）价的封板日，买入（卖出）成交记为可能拿不到", "A 股涨跌停制度；本项目见 audit_limits"),
    Rule("M-SPEC", "method", "只检查用户在 strategy_spec 里声明的承诺；卖出失败、买单没买够等市场原因造成的偏离单列不计", "本项目，见 audit_spec"),
    Rule("M-DSR", "method", "按声明的尝试次数计算 Deflated Sharpe：挑最好一个时纯靠运气可达的夏普门槛，以及校正后夏普大于零的概率", "Bailey 与 López de Prado（2014）"),
    Rule("M-LISTINGS", "method", "打分或成交早于上市日、晚于退市日即记为用了未来名单；股票候选池回测期内无一退市时提示可能的幸存者偏差", "本项目，见 audit_listings"),
    Rule("T-PARTICIPATION", "threshold", "成交附近五个连续分钟内本账户全部买卖成交量占同窗口市场成交量的比例上限；沪深股票/基金时段边界整体平移且不跨午休，只作事后情景，不认证真实容量", UNTESTED, .1, "比例", range=(.01, .5)),
    Rule("M-DECISIONS", "method", "成交与策略自记的每日选择对照；同日换仓按委托编号判断买卖提交顺序；T+0 品种是否从不当天卖出；打分对下一期与再下一期收益的 IC 对比", "本项目，见 audit_decisions"),
    Rule("T-PRICE-JUMP", "threshold", "相邻交易日价格变化超过这个比例即列出（主板 10%、创业板科创板 20%、北交所 30%，留 1 个百分点余量）", "A 股涨跌幅制度；基准校准：0.11–0.30 检出都是 8/8，低于 0.21 时真实策略出现误报", .21, "比例", range=(.11, .5)),
    Rule("M-STATE", "method", "策略自记的每日持仓与账户收盘持仓逐日比较，不一致即记为状态失配", "本项目，见 audit_state"),
    Rule("T-EXIT-RUN", "threshold", "卖单连续失败这么多个交易日，退出卡从需核对升为需处理", "基准与盲测校准：3 时无缺陷用例 2/30 被升为需处理（模拟里正常重试的连续被拒），5 时 0/30，盲测检出与真实策略结果不变，故取 5", 5, "交易日", range=(1, 20)),
    Rule("T-STATE-DAYS", "threshold", "策略自记持仓与账户不一致的天数达到这个值才出卡", "基准校准：1–10 检出不变、无缺陷误报 0；现有样本区分不出差别", 3, "交易日", range=(1, 60)),
    Rule("T-IDLE-AFTER-SALE", "threshold", "先买后卖的换仓日，收盘现金占权益达到这个比例，算卖出的钱闲置", "基准与盲测校准：0.03–0.2 盲测检出都是 10/10、无缺陷 0/30；0.1 以下才标出真实策略 32 号（已核实属实），故保留 0.1", .1, "比例", range=(.01, .5)),
    Rule("T-LOOKAHEAD-T", "threshold", "打分对下一期收益的 IC t 值达到这个值、且再下一期不到它的三分之一，提示可能用了未来数据", "基准与盲测校准：2.5–4 盲测检出 9–10/10、无缺陷 0/30；真实策略国九 t=3.3，阈值低于 3.3 时只靠第二条件挡住，故保留 4 作余量", 4., "", range=(2., 10.)),
    Rule("T-EXIT-LAPSE", "threshold", "退出失败后仍持有且连续这么多个交易日没有新卖单，记为退出被放弃", UNTESTED, 3, "交易日", range=(1, 20)),
    Rule("T-ALLOC-GAP", "threshold", "实际权重偏离目标超过目标的这个比例，记为偏离日", "基准校准：0.10–0.15 时无缺陷误报 0/24、真实策略 1/7；0.05 起无缺陷出现误报，故保留 0.15 留出余量", .15, "比例", range=(.01, 1.)),
    Rule("M-ACTIVITY", "method", "卖单没成交完且收盘仍持有，记为退出未完成；收盘无任何持仓的交易日记为空仓，统计最长连续空仓", "本项目，见 audit_activity"),
    Rule("T-PARTIAL-DAYS", "threshold", "买单只成交一部分后，连续这么多个交易日没有补单才列出", "基准校准：1–10 日检出都是 6/6、无缺陷误报 0/24，对检出不敏感", 5, "交易日", range=(1, 60)),
    Rule("M-PARTIAL-ENTRY", "method", "按委托记录的下单数和成交数找部分成交的买单，跟踪到该标的下一次委托或成交为止", "本项目，见 audit_completion"),
    Rule("T-GRID-BLOCK-SHARE", "threshold", "多窗口审计：一组首尾相接的分段里，最好一段占全部盈利分段之和达到这个比例，算结果集中在一段行情（至少 3 段时判断）",
         UNTESTED, .6, "比例", range=(.3, 1.)),
    Rule("T-GRID-TOP-DAYS", "threshold", "多窗口审计：参考窗口去掉净盈亏最好和最差各这么多个交易日后，净盈亏不再为正，算结果靠少数几天（两头同时去掉，避免把高波动误判为集中）",
         UNTESTED, 5, "交易日", range=(1, 20)),
    Rule("T-GRID-PATH-GAP", "threshold", "多窗口审计：独立起跑收益与连续账户同期收益之差超过这个值（绝对值），记为账户结果不能复现",
         UNTESTED, .01, "比例", range=(.001, .2)),
    Rule("T-GRID-WEIGHT-L1", "threshold", "多窗口审计：两条路径同日实际权重（含现金）的 L1 距离超过这个值，计为权重分岔日（显示门槛，不是经济门槛）",
         UNTESTED, .001, "比例", range=(0., .5)),
    Rule("T-GRID-RETURN-BP", "threshold", "多窗口审计：两条路径同日收益差超过这么多基点，计为收益分岔日（显示门槛）",
         UNTESTED, 1., "基点", range=(.01, 100.)),
    Rule("T-GRID-EPISODE-SHARE", "threshold", "多窗口审计：持仓分岔期间的标准化损益差达到展示本金的这个比例，或期间有执行结局不同的同一指令，列为重点分岔（全部分岔仍保留）",
         UNTESTED, .001, "比例", range=(0., .1)),
    Rule("M-GRID-PAIR", "method", "独立起跑格与参考窗口同日期的连续切片配对：连续切片收益 = 末日权益 / 配对首日前一日权益 − 1；"
         "分信号与选择、组合目标、指令、执行结局、账户五层各自比较，各报第一次观测到的差异；目标只取决策记录，不由实际持仓反推",
         "本项目，见 audit_grid 与 docs/STRATEGY_AUDIT.md；SPEC TEMPERQ_CLAIM_GRID_OFFLINE_SPEC_v1 §7"),
    Rule("T-GRID-MIN-SESSIONS", "threshold", "多窗口审计：交易日少于这个数的窗口，年化收益只作参考、以区间收益为准",
         UNTESTED, 120, "交易日", range=(20, 252)),
    Rule("T-FEE-GAP-MIN", "threshold", "费用差额至少这么多元才出现费用卡", UNTESTED, 1., "元", range=(0., 1000.)),
)}


def defaults():
    return {r.id: r.value for r in RULES.values() if r.kind == "threshold"}


def resolve(overrides=None):
    """Threshold values for one audit; facts and methods cannot be overridden."""
    values = defaults()
    for key, value in (overrides or {}).items():
        rule = RULES.get(key)
        if rule is None or rule.kind != "threshold":
            raise ValueError(f"不能调整的规则：{key}")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{key} 须为数字")
        low, high = rule.range
        if not low <= value <= high:
            raise ValueError(f"{key} 须在 {low}–{high} {rule.unit} 之间")
        values[key] = type(rule.value)(value)
    return values


def snapshot(values):
    """What a report stores: registry version and every threshold value used."""
    return {"version": VERSION, "thresholds": dict(values)}


def cite(*ids):
    """Rule references for a conclusion, in display form."""
    out = []
    for rid in ids:
        rule = RULES[rid]
        out.append({"id": rid, "kind": rule.kind, "text": rule.text, "source": rule.source})
    return out


# Evidence levels shared by every card and check (see docs/specs/AUDIT_EVIDENCE_SPEC.md §3).
EVIDENCE = {
    "E1": "记录事实：导出记录与账务恒等式上的复算",
    "E2": "描述：事后分组，不证明改了会更好",
    "E3": "情景：固定原成交，在声明假设下估计",
    "E3c": "条件情景：有前提未确认，结论随它变",
    "E4": "同区间复跑：同一段历史重跑的比较，仍是样本内",
    "E5": "独立验证段通过：冻结候选后在未看过的验证段通过",
    "E6": "前向观察：模拟盘或实盘记录",
    "N": "待补材料：缺少计算所需材料",
}


KIND_NAMES = {"fact": "规则事实", "method": "有出处的方法", "threshold": "经验阈值"}
ASSET_NAMES = {"stock": "股票", "fund": "ETF/基金", "bond": "债券"}


def _fmt(rule, value):
    if rule.unit == "比例":
        return f"{value:.1%}".replace(".0%", "%")
    return f"{value:g} {rule.unit}".strip() if isinstance(value, (int, float)) else str(value)


def document():
    """docs/AUDIT_RULES.md, generated from RULES; a test keeps the file and the code identical."""
    out = ["# 审计规则登记表", "",
           f"注册表版本：`{VERSION}`。本文件由 `qwb.audit_rules.document()` 生成，请勿手改；"
           "改规则时改代码并重新生成，测试会核对两者一致。", "",
           "每条审计结论都标注它用到的规则编号。三类规则分开：规则事实不可调；有出处的方法写明来源；"
           "经验阈值是项目自己的判断，尚未经样本检验，可在审计设置的“判断口径”里调整，报告保存当次使用的值。", ""]
    for kind in ("fact", "method", "threshold"):
        rows = [r for r in RULES.values() if r.kind == kind]
        out += [f"## {KIND_NAMES[kind]}", ""]
        if kind == "threshold":
            out += ["| 编号 | 含义 | 默认值 | 允许范围 | 适用资产 | 来源 |", "| --- | --- | --- | --- | --- | --- |"]
            for r in rows:
                low, high = r.range
                assets = "、".join(ASSET_NAMES[a] for a in r.applies_to)
                out.append(f"| {r.id} | {r.text} | {_fmt(r, r.value)} | {_fmt(r, low)} – {_fmt(r, high)} | {assets} | {r.source} |")
        else:
            out += ["| 编号 | 内容 | 来源 |", "| --- | --- | --- |"]
            out += [f"| {r.id} | {r.text} | {r.source} |" for r in rows]
        out.append("")
    out += ["## 证据等级", "", "| 等级 | 含义 |", "| --- | --- |"]
    out += [f"| {k} | {v} |" for k, v in EVIDENCE.items()]
    return "\n".join(out) + "\n"
