"""Turn measured audit modules into action cards.

Each card states what was found, the candidate changes (always including
"keep as is"), what is saved and what may be given up, how strong the
evidence is, and what would overturn the conclusion. Cards only reuse numbers
already computed by the audit modules; nothing here re-runs the strategy.
"""
from __future__ import annotations

from .audit_rules import EVIDENCE, cite

# Old evidence names → levels in docs/specs/AUDIT_EVIDENCE_SPEC.md §3.
_LEVEL = {"recomputed": "E1", "descriptive": "E2", "scenario": "E3", "needs_data": "N"}
# Rules each card rests on.
_RULES = {
    "tick_floor": ("F-TICK-STOCK", "F-TICK-FUND", "M-TICK-HALF", "M-SLIPPAGE"),
    "holding_period": ("M-FIFO", "T-HOLD-SHORT"),
    "low_price": ("M-FIFO", "T-PRICE-LOW", "F-TICK-STOCK", "M-TICK-HALF"),
    "fee_setting": ("F-STAMP", "F-MIN-COMMISSION"),
    "minimum_commission": ("F-MIN-COMMISSION", "T-MIN-COMM-MATERIAL"),
    "rank_churn": ("M-RANK-REPLAY", "M-FIFO", "T-BUFFER-RANK", "T-WHIPSAW-DAYS", "T-CHURN-SHARE", "T-CHURN-MIN"),
    "cash_buffer": ("T-CASH-SHORT", "T-CASH-BUFFER"),
    "fill_timing": ("M-FILL-TIMING", "T-TIMING-SHARE"),
    "partial_entry": ("M-PARTIAL-ENTRY", "T-PARTIAL-DAYS"),
    "failed_exits": ("M-ACTIVITY", "T-EXIT-LAPSE"),
    "state": ("M-STATE",),
    "decisions": ("M-DECISIONS",),
    "market": ("M-MARKET",),
    "limits": ("M-LIMIT-LOCK",),
    "spec": ("M-SPEC", "M-DSR"),
    "listings": ("M-LISTINGS",),
    "capacity": ("T-PARTICIPATION",),
    "price_jump": ("T-PRICE-JUMP",),
    "off_target_pnl": ("M-FULFILMENT",),
    "capital": ("M-CAPITAL-FLOOR", "T-CAPITAL-L1"),
    "allocation": ("T-ALLOC-GAP",),
    "idle": ("M-ACTIVITY",),
    "fill_quality": ("M-FILL-RANGE", "M-FILL-FLOOR", "T-FILL-BEST-SHARE"),
    "sequence": ("M-SEQUENCE",),
}
STATES = {"act": "建议处理", "review": "建议核对", "hold": "暂不改"}


def _clock(text):
    from .audit_timing import hhmm
    return hhmm(text)


def _yuan(value):
    return f"¥{value:,.0f}"


def _sequence_card(sequence):
    """Trades whose order of events the backtest took for granted: sale proceeds, signal time, code hints."""
    if not sequence:
        return None
    fund = sequence.get("funding") or {}
    late = sequence.get("signal_after_fill") or {}
    hints = sequence.get("code_hints") or {}
    parts = [x["verdict"] for x in (late, fund, hints) if x and (x.get("late") or x.get("dependent") or x.get("hints")
                                                                  or x.get('coverage_status') == 'incomplete')]
    if not parts:
        return None
    serious = late.get("late", 0) > 0 or fund.get("partial_sell_days", 0) > 0
    return {
        "id": "sequence", "state": "act" if late.get("late") else "review", "title": "先后顺序是否成立",
        "finding": "；".join(parts) + "。",
        "headline": ("有买入用了下单时还不存在的信号" if late.get("late") else
                     "换仓的买入依赖同一时刻卖出的钱，且有卖单只成交了一部分" if serious else
                     "换仓的买入依赖同一时刻卖出的回款：需核对实际成交回报" if fund.get('dependent') else
                     "代码出现开盘时读取当日日线的提示，需核对平台数据语义" if hints.get('hints') else
                     "成交或信号时间不全，不能判断先后顺序是否成立"),
        "edits": [],
        "options": [{"label": "维持原样", "effect": "卖出标的流动性好、单笔远小于成交量，且平台确实先撮合卖单时"},
                    {"label": "按已成交卖单的回款再买", "effect": "先卖、确认成交后用实际到账资金买入；卖单没成交时不买或只买够得上的部分"},
                    {"label": "按下一根分钟线撮合重跑", "effect": "平台改为下一根K线开盘价撮合（米筐 matching_type=next_bar），区分看完行情再成交的乐观程度"}],
        "saving": "未测算改动收益；这里只核对资金与信号时序",
        "sacrifice": "仅凭分钟汇总不能确定实际成交顺序或收益影响",
        "net": "先补齐逐笔成交、委托和信号可用时间；如需检验不同撮合假设，由原平台另行回放比较",
        "evidence": "recomputed" if late.get("late") else "descriptive" if fund.get('dependent') else "needs_data",
        "basis": "期初或上一收盘现金、当日更早成交及费用、完整打分可用时间、代码文字",
        "overturn": "逐笔回报证明买单按已成交卖单回款约束下单，可解释资金依赖但不消除依赖事实；若打分可用时间实为写入时间，需补真实可用时刻再判定。",
    }


def _quality_card(quality):
    """Fill prices that look too good, or a result that does not survive the worst nearby price."""
    if not quality or quality.get("status") != "suspicious":
        return None
    parts = [x["verdict"] for x in (quality.get("range"), quality.get("floor")) if x]
    rng = quality.get("range") or {}
    return {
        "id": "fill_quality", "state": "act", "title": "成交价是否拿得到",
        "finding": "；".join(parts) + "。",
        "headline": ("成交价系统性落在当天最有利的位置，疑似偷看了当天走势" if rng.get("status") == "suspicious"
                     else "按成交后几分钟的最差价重算就不再盈利：收益依赖较好的成交价（压力情景）"),
        "edits": [],
        "options": [{"label": "核对信号所用数据的时间", "effect": "确认选股只用了下单之前已经公开的数据，没有用当天收盘价、全天成交量或当天走势"},
                    {"label": "延后执行重跑", "effect": "信号不动，把执行时点延后到 9:35、10:00，并调高滑点，回平台重跑，用 qwb audit compare 比较"},
                    {"label": "改用更保守的撮合", "effect": "按下一根分钟线或成交量加权均价撮合，限制单笔占当分钟成交量"}],
        "saving": "不省钱；结果更接近实盘能拿到的价格",
        "sacrifice": "逐笔极端价格情景可能使历史净盈亏下降；实际改后结果需复跑",
        "net": "需要回原平台按更保守的执行重跑",
        "evidence": "scenario",
        "basis": "成交日全天最高最低价与成交后分钟价；股数和费用不变",
        "overturn": "策略确实只用前一日数据、在竞价截止前提交，并且单笔远小于当分钟成交量；或延后执行重跑后结论不变。",
    }


def _capacity_card(capacity):
    """Observed participation/coverage only; never prescribe a trading policy."""
    if not capacity or capacity.get('status') == 'normal':
        return None
    missing = capacity.get('fill_coverage', 0) < 1 or capacity.get('status') == 'insufficient'
    return {
        'id': 'capacity', 'state': 'review', 'title': '成交量证据与参与率',
        'headline': ('成交量材料不足，不能判断总体参与率' if missing else
                     '存在超过参与率情景门槛的窗口，需核对成交量单位与撮合假设'),
        'finding': capacity['verdict'], 'edits': [],
        'options': [{'label': '核对现有材料', 'effect': '确认成交和行情来自同一市场、同一证券，数量单位均为股/份'},
                    {'label': '按明细补件', 'effect': '补齐缺少或冲突的日期、证券、分钟成交量；保留原策略参数'}],
        'saving': '没有计算可节省费用或新增收益',
        'sacrifice': '补齐材料前，不对未覆盖成交或真实容量作判断',
        'net': '下载参与率窗口和缺项明细，核对数据来源；超过情景门槛不等于已证实无法成交',
        'evidence': 'needs_data' if missing else 'scenario',
        'basis': '成交毛数量、交易时段内五分钟观测量与明确的情景阈值',
        'overturn': '同源原始分钟量或数量单位更正后重新核算；冲击和排队结论另需校准材料',
    }


def _off_target_card(fulfilment):
    """A large part of the return came from holdings already outside the strategy's own target."""
    by = (fulfilment or {}).get("by_target") or {}
    if (fulfilment or {}).get("status") != "flagged" or not by:
        return None
    return {
        "id": "off_target_pnl", "state": "review", "title": "收益来自目标外的持仓",
        "finding": by["verdict"] + "。",
        "headline": f"{by['outside_share']:.0%} 的净盈利来自策略目标之外的持仓：收益不完全对应声明的策略",
        "edits": [],
        "options": [{"label": "维持原样", "effect": "目标外持仓本来就是策略允许的过渡状态时"},
                    {"label": "按实际成交更新策略状态", "effect": "下单后按成交回报更新内部持仓，没卖掉的仓位继续纳入管理"}],
        "saving": "不省钱；持仓更符合策略的设计",
        "sacrifice": "这部分收益如果来自没管住的仓位，修正后可能消失，也可能反过来",
        "net": "会计分组不能直接扣掉；要在原平台修正后重跑，才知道收益变多少",
        "evidence": "descriptive",
        "basis": "逐标的逐日盈亏、前一日收盘持仓和策略每日目标",
        "overturn": "策略明确允许这些仓位留在目标外，或修正后重跑收益不变。",
    }


def _allocation_card(allocation):
    """Closing weights far from the strategy's own declared targets, beyond what one board lot explains."""
    if (allocation or {}).get("status") != "gap":
        return None
    names = allocation.get("names") or []
    if allocation.get("sparse_targets"):
        beyond = max((n["plan_over_days"] / max(1, n["plan_days"]) for n in names), default=0)
        hard = beyond >= .1
    else:
        beyond = max(((n["over_days"] - n["within_lot_days"]) / max(1, n["days"]) for n in names), default=0)
        hard = beyond >= .05
    return {
        "id": "allocation", "state": "review", "title": "实际持仓没达到目标权重",
        "finding": allocation["verdict"] + "。",
        "headline": ("调仓后的收盘权重仍明显偏离策略自己声明的目标，超出一手能解释的范围" if hard else
                     "实际权重偏离目标，多数缺口在一手以内：账户规模下凑不齐整手"),
        "edits": [],
        "options": [{"label": "维持原样", "effect": "目标只是参考、允许长期偏离时"},
                    {"label": "核对执行模型与拒单原因", "effect": "先检查撮合时点、单分钟成交量限制、单位和拒单日志；全天有量不证明下单时可成交，也不能用单分钟无量推断全天买不到"},
                    {"label": "回原代码核对下单数量", "effect": "核对计算目标股数用的是总权益还是可用现金、调仓阈值和整手取整"}],
        "saving": "不省钱；持仓按声明的配置运行",
        "sacrifice": "无；收益会变，方向要重跑才知道",
        "net": "保持策略与费用不变，单独改变撮合时点或量限做敏感性复跑；用 qwb audit compare --sensitivity 比较，再决定是否修改策略",
        "evidence": "recomputed",
        "basis": "策略目标权重与每日收盘持仓市值",
        "overturn": "目标权重记录的不是策略真实使用的目标，策略有意分批调仓，或缺口由撮合时点、单分钟量限、成交量单位等执行代理造成；现有证据没有排除这些解释。",
        "causal_status": "unresolved",
        "diagnosis": allocation.get("diagnosis"),
    }


def _capital_card(capital):
    """The capital cannot hold the target weights in whole lots, even before fees and timing."""
    if (capital or {}).get("status") != "limited":
        return None
    return {
        "id": "capital", "state": "review", "title": "资金太小，凑不出目标配置",
        "finding": capital["verdict"] + "。",
        "headline": "小账户按整手买不出这组目标权重：同样的权重换个本金，实际运行的是另一个策略",
        "edits": [],
        "options": [{"label": "维持原样", "effect": "实际资金在下界可接受的规模以上时"},
                    {"label": "换单价更低的标的或允许零股", "effect": "同类资产里换一手金额更小的品种"},
                    {"label": "写明最低资金", "effect": "在策略说明里注明适用的最低本金"}],
        "saving": "不省钱",
        "sacrifice": "无",
        "net": "下界只证明做不到，做得到要按真实约束另行验证",
        "evidence": "scenario",
        "basis": "目标权重、当时价格、一手 100 份与资金规模的整数规划",
        "overturn": "实际账户资金在下界可接受的规模以上；或交易品种允许零股。",
    }


def _state_card(state):
    """The strategy's own record of what it held differs from the account."""
    if (state or {}).get("status") != "flagged":
        return None
    return {
        "id": "state", "state": "act", "title": "策略以为的持仓和实际不同",
        "finding": state["verdict"] + "。",
        "headline": f"{state['mismatch_days']} 天策略记录的持仓与账户不同：下一次决策依据的是不存在的持仓，或漏管了实际持仓",
        "edits": [],
        "options": [{"label": "维持原样", "effect": "记录的本来是目标而不是持仓时"},
                    {"label": "按成交回报更新状态", "effect": "下单后不立即改内部持仓，等成交回报，或每次决策前读取账户实际持仓"}],
        "saving": "不省钱；策略按真实持仓决策",
        "sacrifice": "收益会变，方向要重跑才知道",
        "net": "需要回原平台改状态更新逻辑重跑",
        "evidence": "recomputed",
        "basis": "策略每日记录的持仓与账户收盘持仓",
        "overturn": "策略记录的不是持仓而是别的状态（如目标），或记录时点早于当天最后一笔成交。",
    }


def _spec_card(spec):
    """Declared promises the account did not keep. Only declared keys are ever checked."""
    if not spec:
        return None
    hit = [spec[k] for k in spec.get("breached") or []]
    trials = spec.get("trials") or {}
    weak = trials.get("status") == "weak"
    if not hit and not weak:
        return None
    titles = {"cash_buffer": "现金缓冲", "max_weight": "单只上限", "max_names": "持仓只数", "flat_overnight": "不留隔夜仓",
              "rebalance": "调仓日"}
    names = "、".join(titles[k] for k in spec.get("breached") or [])
    return {
        "id": "spec", "state": "act" if hit else "review",
        "title": f"没守住声明的承诺：{names}" if hit else "试了很多版本，夏普不足以排除运气",
        "finding": "；".join(x["verdict"] for x in hit + ([trials] if weak else [])) + "。",
        "headline": (f"策略声明的 {names} 在回测里没有兑现，市场原因造成的偏离已排除" if hit else trials["verdict"]),
        "edits": [],
        "options": [{"label": "维持原样", "effect": "声明写得不准、实际策略本来就是这样时，改声明"},
                    {"label": "回原代码核对", "effect": "核对缓冲比例、权益刷新、上限和调仓日判断，改后重跑"}],
        "saving": "不省钱",
        "sacrifice": "无；收益会变，方向要重跑才知道",
        "net": "需要回原平台改代码重跑",
        "evidence": "recomputed" if hit else "descriptive",
        "basis": "用户在 strategy_spec 里声明的承诺，与成交、持仓、委托记录对照",
        "overturn": "声明本身写错，或偏离来自声明里没写的合理规则。",
    }


def _limits_card(limits):
    if (limits or {}).get("status") != "flagged":
        return None
    return {
        "id": "limits", "state": "review", "title": "封板时成交",
        "finding": limits["verdict"] + "。",
        "headline": f"{limits['locked']} 笔成交发生在涨跌停封板日：真实交易中多半排不上队",
        "edits": [],
        "options": [{"label": "维持原样", "effect": "订单很早挂出、资金量很小，确有机会成交时"},
                    {"label": "回测里禁止封板成交", "effect": "在回测引擎里打开涨跌停撮合限制，或在代码里跳过封板标的，改后重跑"}],
        "saving": "不省钱",
        "sacrifice": "封板日买不进、卖不出，收益可能变差",
        "net": "需要重跑才知道",
        "evidence": "recomputed",
        "basis": "当日涨跌停价与全天最高最低价",
        "overturn": "订单在封板前已挂出并排在前面，或成交量极小。",
    }


def _listings_card(listings):
    if (listings or {}).get("status") != "flagged" and not (listings or {}).get("survivorship_question"):
        return None
    hard = listings.get("status") == "flagged"
    return {
        "id": "listings", "state": "act" if hard else "review",
        "title": "候选池用到了未来的上市名单" if hard else "候选池可能有幸存者偏差",
        "finding": listings["verdict"] + "。",
        "headline": listings["verdict"].split("；")[0],
        "edits": [],
        "options": [{"label": "维持原样", "effect": "候选池本来就是按当时名单建的时"},
                    {"label": "按当时名单建池", "effect": "每个调仓日只用当天已上市、未退市的标的"}],
        "saving": "不省钱",
        "sacrifice": "加入后来退市的标的，收益通常会变差",
        "net": "需要重跑才知道",
        "evidence": "recomputed" if hard else "descriptive",
        "basis": "标的上市、退市日期与打分、成交日期",
        "overturn": "上市日期数据有误，或候选池是按当时名单建的。",
    }


def _decisions_card(decisions):
    """Trades that contradict the strategy's own decision record or the instrument's rules."""
    parts = [(k, (decisions or {}).get(k)) for k in ("against_selection", "selected_not_ordered", "buy_before_sell",
                                                     "t0_overnight", "no_exit_attempt", "repeated_adds", "lookahead")]
    hit = [(k, p) for k, p in parts if (p or {}).get("status") == "flagged"]
    if not hit:
        return None
    hard = [k for k, _ in hit if k != "lookahead"]
    titles = {"against_selection": "买入与策略当天的选择不符", "selected_not_ordered": "选中了却没下单",
              "buy_before_sell": "换仓时先买后卖",
              "t0_overnight": "T+0 品种被当作 T+1", "no_exit_attempt": "日内策略有几天没尝试平仓",
              "repeated_adds": "已满仓的目标被反复加仓",
              "lookahead": "打分可能用到了未来数据"}
    return {
        "id": "decisions", "state": "act" if hard else "review", "title": titles[hit[0][0]] if len(hit) == 1 else "成交与策略的决策记录不一致",
        "finding": "；".join(p["verdict"] for _, p in hit) + "。",
        "headline": "；".join(titles[k] for k, _ in hit),
        "edits": [],
        "options": [{"label": "维持原样", "effect": "这些做法本来就是策略的设计时"},
                    {"label": "回原代码核对", "effect": "核对下单读取的信号日期、卖买提交顺序和品种的交易制度，改后重跑"}],
        "saving": "不省钱",
        "sacrifice": "无；收益会变，方向要重跑才知道",
        "net": "需要回原平台改代码重跑",
        "evidence": "recomputed" if hard else "descriptive",
        "basis": "委托编号与时间、成交、策略自记的每日选择与打分、候选池价格",
        "overturn": "策略有意如此（例如有意隔夜持有、有意先买后卖），或打分本来就是超短期信号。",
    }


def _price_jump_card(prices):
    if (prices or {}).get("status") != "flagged":
        return None
    return {
        "id": "price_jump", "state": "act", "title": "价格序列有不可能的跳变",
        "finding": prices["verdict"] + "。",
        "headline": f"{prices['count']} 处单日价格变化超出涨跌幅可能：先核对复权和份额折算，再看依赖这些价格的结论",
        "edits": [],
        "options": [{"label": "维持原样", "effect": "当天确有已正确处理的折算或拆分时"},
                    {"label": "核对复权因子", "effect": "对照数据供应商的除权和份额折算记录，修正后重跑"}],
        "saving": "不省钱",
        "sacrifice": "无",
        "net": "价格修正后打分和成交都可能变，收益要重跑",
        "evidence": "recomputed",
        "basis": "候选池收盘价与收盘持仓估值价的相邻交易日变化",
        "overturn": "当天确有份额折算或拆分且已正确处理；或标的处在不设涨跌幅的新股、退市整理期。",
    }


LONG_EXIT_RUN = 5        # consecutive trading days with a failed exit (T-EXIT-RUN)


def _exit_card(activity, thresholds=None):
    """Sell orders that did not complete while the name stayed held: the exit the backtest planned did not happen."""
    exits = (activity or {}).get("exits") or {}
    if exits.get("failed", 0) < 3 and not exits.get("unrenewed"):
        return None                                          # one abandoned exit is enough to report
    # A refused exit that is sold within a day or two is market friction; one that is dropped, or that keeps
    # failing for days, means the planned exit did not happen.
    run = int((thresholds or {}).get("T-EXIT-RUN", LONG_EXIT_RUN))
    abandoned = exits.get("unrenewed", 0) > 0 or exits.get("longest_run", 0) >= run
    return {
        "id": "failed_exits", "state": "act" if abandoned else "review", "title": "卖单没卖掉",
        "finding": exits["verdict"] + "。" + (exits["unrenewed_verdict"] + "。" if exits.get("unrenewed_verdict") else ""),
        "headline": f"{exits['failed']} 笔卖单没成交完、收盘仍持有，最长连续 {exits['longest_run']} 个交易日：计划的退出没有发生",
        "edits": [],
        "options": [{"label": "维持原样", "effect": "持仓过夜本来就在策略允许范围内时"},
                    {"label": "提前退出或分笔卖出", "effect": "收盘前几分钟成交量可能为零，改到更早、成交更活跃的时点，或拆成多笔"},
                    {"label": "卖不掉时次日补卖", "effect": "检查前一日的退出是否完成，没完成就在次日开盘处理"}],
        "saving": "不省钱；持仓风险按策略本意控制",
        "sacrifice": "提前卖出可能错过尾盘的价格",
        "net": "需要回原平台改退出逻辑重跑才能知道",
        "evidence": "recomputed",
        "basis": "委托记录里卖单的下单数与成交数，以及当日收盘持仓",
        "overturn": "策略本来就允许持仓过夜，或这些卖单是可选的减仓而不是必须完成的退出。",
    }


def _idle_card(activity):
    """Long stretches with nothing held: a low drawdown that comes from not being invested."""
    idle = (activity or {}).get("idle") or {}
    from .audit_activity import LONG_IDLE
    if idle.get("longest", 0) < LONG_IDLE:
        return None
    return {
        "id": "idle", "state": "review", "title": "策略长期没持仓",
        "finding": idle["verdict"] + "。",
        "headline": f"最长连续 {idle['longest']} 个交易日没有任何持仓：低回撤可能来自没投资，而不是控制了风险",
        "edits": [],
        "options": [{"label": "维持原样", "effect": "长期空仓是策略的择时设计时"},
                    {"label": "核对空仓的触发条件", "effect": "查看策略判断空仓、决定建仓的代码，确认是信号使然还是状态变量出错（如用浮点数精确相等判断）"}],
        "saving": "不省钱",
        "sacrifice": "无",
        "net": "如果空仓是程序错误，修正后收益和回撤都会变，需重跑",
        "evidence": "recomputed",
        "basis": "每日收盘持仓与账户现金",
        "overturn": "策略的目标持仓在这些天本来就是空仓。",
    }


def _partial_card(completion):
    """A buy that filled in part and was never topped up: the target is held, the order is not complete."""
    if not completion or completion.get("status") != "found":
        return None
    w = completion["worst"]
    return {
        "id": "partial_entry", "state": "review", "title": "买单没成交完，之后也没补",
        "finding": completion["verdict"] + "。",
        "headline": f"{completion['long_episodes']} 笔买单成交不足、之后很久没补单：持有了目标，不等于完成了原买单",
        "edits": [],
        "options": [{"label": "维持原样", "effect": "策略本意就是只在信号日下一次单、不追单时"},
                    {"label": "成交不足时次日补单", "effect": "在下单函数里比较目标股数和实际持仓，差额下次补上"},
                    {"label": "分笔下单", "effect": "单笔超过当根成交量上限时，拆成多根K线成交"}],
        "saving": "不省钱；资金按策略本意投入",
        "sacrifice": "补单会增加成交次数和费用",
        "net": "需要回原平台加上补单逻辑重跑才能知道",
        "evidence": "recomputed",
        "basis": "委托记录的下单数与成交数；期间现金占比来自账户",
        "overturn": "策略本来就设计为不追单；或补单后的重跑收益不优于原版。",
    }


def _timing_card(timing):
    """Does the result depend on the minute the orders filled? Only from minute prices the user supplied."""
    if not timing or timing.get("status") not in ("sensitive", "stable"):
        return None
    sensitive = timing["status"] == "sensitive"
    judged = set(timing.get("judged_delays") or [])
    delayed = [r for r in timing["rows"] if r.get("delay") in judged] or [r for r in timing["rows"] if r.get("delay")]
    worst = min(delayed, key=lambda r: r["gain"])
    best = max(delayed, key=lambda r: r["gain"])
    flatter = timing.get("open_flatters")
    finding = (f"{timing['timed_fills']} 笔有成交时间，其中 {timing['open_fills']} 笔在 {timing['open_by']} 以前成交"
               f"（成交额占 {timing['open_share']:.0%}）。固定股数和费用，把每笔成交往后挪：{worst['label']}盈亏变化 {_yuan(worst['gain'])}，"
               f"{best['label']}盈亏变化 {_yuan(best['gain'])}；回测净盈亏 {_yuan(timing['net_pnl'])}。")
    if timing.get("max_volume_share") is not None:
        finding += f"单笔成交最多占当分钟成交量的 {timing['max_volume_share']:.0%}。"
    return {
        "id": "fill_timing", "state": "review" if sensitive else "hold", "title": "成交时点是否决定了结论",
        "finding": finding,
        "headline": (("每一档延后成交都比回测用的时点差：回测按这个时点成交偏乐观" if flatter else
                      "成交稍晚，盈亏变化和净盈亏同一量级：结论依赖成交时点") if sensitive
                     else "成交稍晚，盈亏变化远小于净盈亏：成交时点不改变结论"),
        "edits": [],
        "options": [{"label": "维持原样", "effect": "实盘确实能在回测时点按该价成交时（例如开盘竞价挂单且金额小）"},
                    {"label": "改为更可执行的时点或价格", "effect": "回原平台改调仓时点或改用开盘价/均价撮合重跑，再到『效果验证』比较"},
                    {"label": "分笔或延后执行", "effect": "单笔占当分钟成交量大时，分到多个分钟成交"}],
        "saving": "不省钱；回测结果更接近实盘能拿到的价格",
        "sacrifice": f"按延后成交计，盈亏变化最多 {_yuan(worst['gain'])}" if worst["gain"] < 0 else "延后成交都不更差",
        "net": "只换成交价、不重跑决策；这是价格敏感性，不是延迟回放，需冻结信号回原平台重跑确认",
        "evidence": "scenario",
        "basis": "按用户提供的分钟价重算成交价；股数、费用和交易日期不变",
        "overturn": "实盘成交记录显示能在回测时点按回测价成交；或换时点重跑后选股和盈亏都不变。",
        "table_kind": "timing",
        "table": [{"label": r["label"], "fills": r["fills"], "gain": r["gain"], "coverage": r["coverage"]} for r in timing["rows"]],
    }


def _tick_card(tick):
    total = tick.get("accounted_intervals") or {}
    if tick.get("status") != "verified" or not total.get("traded_notional"):
        return None
    net, cost = total["net_pnl"], total["tick_scenario_cost"]
    bp, after = total["weighted_half_spread_bp"], total["net_after_tick_scenario"]
    fraction = total.get("break_even_cost_fraction")
    bands = tick.get("price_bands") or []
    cheap = next((b for b in bands if b["id"] == "under_5"), None)
    covers = total.get("profit_covers_scenario", after >= 0)
    options = [{"label": "维持原样", "effect": "先用券商成交明细或报价数据核实实际价差，再决定是否改"}]
    if cheap and cheap["traded_notional"]:
        options.append({"label": "少做低价股",
                        "effect": f"低于 5 元的成交占成交额 {cheap['notional_share']:.0%}，每边约 {cheap['weighted_half_spread_bp']:.1f}bp，"
                                  f"约占价差情景 {_yuan(cheap['tick_scenario_cost'])}；改哪一行见『低价股是否太多』"})
    options.append({"label": "降低换手、延长持有", "effect": "直接减少需要付价差的成交次数；改哪一行见『持有期是否太短』"})
    if fraction is None or fraction <= 0:
        break_even = "现有净盈亏不足以覆盖任何正的价差情景"
    elif fraction < 1:
        break_even = f"现有净盈亏只够覆盖约 {fraction:.0%} 的价差情景，相当于每边约 {bp * fraction:.1f}bp"
    else:
        break_even = f"扣除价差情景后仍盈利；实际价差要到每边约 {bp * fraction:.0f}bp 才会吃掉全部盈利"
    already = total.get("already_charged") or 0.
    if already > .5:
        basis = (f"按每边半档的价差情景计算，合计约 {_yuan(total['tick_floor_cost'])}；{tick.get('slippage_note', '原回测已计滑点')}，"
                 f"其中约 {_yuan(already)} 已含在成交价里，只补差额 {_yuan(cost)}（每边约 {bp:.1f}bp）。")
    else:
        basis = (f"按最小价位估计，每边价差约 {bp:.1f}bp（按成交额加权），合计约 {_yuan(cost)}；"
                 f"{tick.get('slippage_note', '代码里没有设置滑点')}。")
    if cost < .5 and already > .5:
        return {
            "id": "tick_floor", "state": "hold", "title": "回测收益能否覆盖买卖价差",
            "finding": f"回测净盈亏 {_yuan(net)}。" + basis,
            "headline": "原回测设置的滑点已经不低于最小价位价差，这一项不用再扣",
            "options": [{"label": "维持原样", "effect": "滑点设置已覆盖价差下限"}],
            "saving": "无", "sacrifice": "无", "net": f"维持 {_yuan(net)}",
            "evidence": "recomputed",
            "basis": "交易所最小报价单位是规则；滑点按代码里的设置换算为每边成本",
            "overturn": "实际成交价差明显大于最小价位，例如成交清淡或单笔金额大。"}
    return {
        "id": "tick_floor", "state": "review" if not covers else "hold", "title": "回测收益能否覆盖买卖价差",
        "finding": f"回测净盈亏 {_yuan(net)}。" + basis + f"扣除后约 {_yuan(after)}。",
        "headline": break_even,
        "options": options,
        "saving": "减少交易可降低本情景成本；实际可省多少须核验成交机制与实测成本",
        "sacrifice": "剔除低价股或少交易，可能放弃这部分股票原本贡献的收益",
        "net": f"扣价差情景后 {_yuan(after)}" + ("（为负）" if after < 0 else ""),
        "evidence": "scenario",
        "basis": "各品种最小报价单位是交易规则；每边承担半档是情景假设，不是每笔成交必付成本。方法见作者 STRUCT 稿（SSRN，未经同行评审）",
        "conditions": ["实际成交是否承担每边半档价差尚未验证；报价单位不等于实际执行成本下限"],
        "overturn": "原回测已按价差或滑点扣费；或实际成交明显好于半个价位（例如集合竞价撮合）。有报价或成交明细时可校准。",
    }


def _split_lots(holding, keep):
    """Closed FIFO lots split by a threshold; falls back to the fixed bands for old reports without lots."""
    lots = holding.get("closed")
    if lots is None:
        return None
    hit = [l for l in lots if keep(l)]
    rest = [l for l in lots if not keep(l)]
    total = lambda rows, key: sum(r[key] for r in rows)
    return {"n": len(hit), "rest": len(rest), "after": total(hit, "net_after_tick_scenario"),
            "cost": total(hit, "fees") + total(hit, "tick_scenario_cost")}


def _holding_card(holding, edits, t=None):
    bands = holding.get("closed_age_bands") or []
    summary = holding.get("closed_summary") or {}
    if holding.get("status") != "verified" or not summary.get("matches"):
        return None
    days = int((t or {}).get("T-HOLD-SHORT", 5))
    rows = [{"label": b["label"], "matches": b["matches"], "net": b["net_pnl"],
             "net_after_tick": b["net_after_tick_scenario"]} for b in bands if b["matches"]]
    split = _split_lots(holding, lambda l: l["calendar_days"] <= days)
    if split is not None:
        short_n, long_n, short_after, short_cost = split["n"], split["rest"], split["after"], split["cost"]
    else:
        short = [b for b in bands if b["id"] in {"0_to_2", "3_to_5"}]
        short_n = sum(b["matches"] for b in short)
        long_n = sum(b["matches"] for b in bands if b["id"] not in {"0_to_2", "3_to_5"})
        short_after = sum(b["net_after_tick_scenario"] for b in short)
        short_cost = sum(b["fees"] + b["tick_scenario_cost"] for b in short)
    if not short_n:
        return None
    losing = short_after < 0
    few = long_n < max(5, short_n // 4)
    return {
        "id": "holding_period", "state": "review" if losing else "hold", "title": "持有期是否太短",
        "finding": f"已平仓 {summary['matches']} 笔中，持有 {days} 个自然日以内的有 {short_n} 笔，付出费用和价差约 {_yuan(short_cost)}，"
                   f"扣完后合计 {_yuan(short_after)}；持有更久的 {long_n} 笔" + ("，样本少，不能下结论。" if few else "。"),
        "headline": (f"短持有交易扣完成本后亏 {_yuan(-short_after)}，值得试着拿久一点" if losing
                     else f"短持有交易扣完成本后仍赚 {_yuan(short_after)}，拿久一点可能丢掉这部分，暂不改"),
        "edits": edits.get("holding", []),
        "table": rows,
        "options": [{"label": "维持原样", "effect": "持有期由选股信号决定时，先确认信号本身能管几天"},
                    {"label": "延长持有期或加排名保留带", "effect": "排名小幅下滑不卖，减少来回交易"}],
        "saving": f"短持有部分的成本约 {_yuan(short_cost)}；拿得更久，成交次数和这部分成本会同比例下降",
        "sacrifice": f"这些短持有交易扣完成本后合计 {_yuan(short_after)}；信号若只在几天内有效，拿久了会丢掉它",
        "net": "需要重放才能估计：用同一信号回原平台按不同持有期重跑，再到『效果验证』比较",
        "evidence": "descriptive",
        "basis": "按先进先出匹配买卖，费用按股数分摊；分组是事后描述",
        "overturn": "按每日打分重放后，延长持有期的净结果不优于原规则。",
    }


def _fee_card(fees, edits, model=None, t=None):
    if model:
        return _fee_model_card(model, edits, t or {})
    reported, modeled = fees.get("reported_fee"), fees.get("modeled_fee")
    if fees.get("status") not in {"measured", "verified"} or reported is None or modeled is None:
        return None
    gap = reported - modeled
    if abs(gap) < max(1.0, 0.01 * modeled):
        return None
    more = gap > 0
    return {
        "id": "fee_setting", "state": "review", "title": "回测费用设置",
        "finding": f"回测报告费用 {_yuan(reported)}，按你确认的佣金和成交日适用的印花税复算为 {_yuan(modeled)}，"
                   f"{'多' if more else '少'}扣 {_yuan(abs(gap))}。",
        "headline": ("回测扣费偏高，结果偏保守" if more else "回测扣费偏低，结果偏乐观"),
        "edits": edits.get("fees", []),
        "options": [{"label": "维持原样", "effect": "保守的费用不会让结果虚高"},
                    {"label": "按券商实际费率改费用设置后回原平台重测",
                     "effect": "原因见上方『改代码哪里』：佣金率偏高，或卖出仍按 2023-08-28 之前的 10bp 印花税" if more
                               else "回测佣金低于审计默认值时，先按券商合同确认哪边对，再改回测或审计设置"}],
        "saving": "不改变实际交易成本，只让回测结果更接近实盘",
        "sacrifice": "无",
        "net": f"回测净盈亏约{'低估' if more else '高估'} {_yuan(abs(gap))}",
        "evidence": "recomputed",
        "basis": "佣金按确认的费率和最低佣金，印花税按成交日期适用税率",
        "overturn": "你的券商合同费率与确认值不同；或报告费用中包含过户费等其他项目。",
    }


def _fee_model_card(model, edits, t):
    """Spec R3: (1) did the backtest charge its own declared model; (2) against the broker, only once confirmed."""
    declared, check, stamp = model["declared"], model["self_check"], model.get("stamp_check")
    reported = model["charged"]["reported"]
    scenarios = "、".join(f"{s['commission_bp']:g}bp 约 {_yuan(s['fee'])}" for s in model["scenarios"])
    lines = []
    if declared["status"] == "none":
        lines.append("代码里没有读到费用设置：可能用的是平台默认费率，也可能写在平台设置或运行配置里。"
                     "无法核对回测是否按自己的设置扣费；若有运行配置（如 RQAlpha 的 config.json），请一并上传。")
    else:
        where = "；".join(m["source"] + (f"（第 {m['line']} 行）" if m.get("line") else "（运行配置）")
                         for m in declared["models"].values()) or "仅定位到配置键"
        lines.append(f"回测声明的费用模型：{where}。")
        for cls, why in declared["missing"].items():
            lines.append(f"{ {'stock': '股票', 'fund': 'ETF/基金', 'bond': '债券'}[cls] }：{why}。")
        if check["status"] == "consistent":
            lines.append(f"按声明模型逐笔复算 {check['priced_fills']} 笔，与回测实际扣费 {_yuan(reported)} 一致。")
        elif check["status"] == "inconsistent":
            lines.append(f"按声明模型逐笔复算为 {_yuan(check['recomputed'])}，回测实际扣费 {_yuan(check['reported'])}，"
                         f"{check['mismatched_fills']} 笔对不上，差 {_yuan(check['gap'])}。")
    tax_wrong = stamp and stamp["mismatched_sells"]
    if tax_wrong:
        if stamp.get("source") == "成交导出的税费列":
            lines.append(f"成交导出的税费列：卖出股票实收印花税约 {stamp['declared_bp']:.1f}bp，成交日适用税率为 "
                         f"{'/'.join(f'{b:g}' for b in stamp['rule_bp'])}bp，{stamp['mismatched_sells']}/{stamp['sells']} 笔不符；"
                         f"实收 {_yuan(stamp['charged'])}，按适用税率应为 {_yuan(stamp['due'])}，"
                         + (f"回测多扣 {_yuan(stamp['charged'] - stamp['due'])}。" if stamp['charged'] >= stamp['due']
                            else f"回测少扣 {_yuan(stamp['due'] - stamp['charged'])}。"))
        else:
            lines.append(f"声明的卖出印花税约 {stamp['declared_bp']:.1f}bp，与成交日适用税率 {'/'.join(f'{b:g}' for b in stamp['rule_bp'])}bp "
                         f"不符的卖出 {stamp['mismatched_sells']} 笔。")
    broker, versus = model["broker"], model.get("broker_check")
    gap_min, gap_share = t.get("T-FEE-GAP-MIN", 1.), t.get("T-FEE-GAP-SHARE", .01)
    if versus is not None:
        gap = versus["gap"]
        material = abs(gap) >= max(gap_min, gap_share * versus["modeled"])
        lines.append(f"按你确认的合同口径（{broker['commission_bp']:g}bp、最低 ¥{broker['minimum_commission']:g}）复算为 "
                     f"{_yuan(versus['modeled'])}，回测{'高于' if gap > 0 else '低于'}合同口径 {_yuan(abs(gap))}。")
        headline = (f"回测成本{'高于' if gap > 0 else '低于'}合同口径约 {_yuan(abs(gap))}，结果偏{'保守' if gap > 0 else '乐观'}"
                    if material else "回测成本与合同口径一致")
        evidence, conditions = "E1", []
    else:
        lines.append(f"券商合同费率未确认；按费率情景复算：{scenarios}。确认合同费率后才判断回测成本偏高还是偏低。")
        material = False
        headline = ("回测按自己声明的模型扣费一致；合同费率未确认，只给情景区间" if check["status"] == "consistent"
                    else "合同费率未确认，只给情景区间")
        evidence, conditions = "E3c", ["券商合同费率未确认"]
    inconsistent = check["status"] == "inconsistent"
    if not (inconsistent or tax_wrong or material or declared["status"] != "complete" or versus is None):
        return None
    return {
        "id": "fee_setting", "state": "act" if inconsistent or tax_wrong else "review" if material or versus is None else "hold",
        "title": "回测费用设置",
        "finding": "".join(lines),
        "headline": ("回测没有按自己声明的费用模型扣费" if inconsistent else "卖出印花税与成交日税率不符" if tax_wrong else headline),
        "edits": edits.get("fees", []) if inconsistent or tax_wrong or material else [],
        "options": [{"label": "维持原样", "effect": "费用设置与合同一致时不用改"},
                    {"label": "在审计设置确认券商合同费率", "effect": "确认后给出回测高于或低于合同口径的单一结论"}],
        "saving": "不改变实际交易成本，只让回测结果更接近合同口径",
        "sacrifice": "无",
        "net": (f"回测净盈亏与合同口径相差约 {_yuan(abs(versus['gap']))}" if versus is not None else "取决于合同费率，见情景区间"),
        "evidence": evidence, "conditions": conditions,
        "basis": "声明模型来自代码或配置；实际扣费来自成交导出；印花税按成交日适用税率",
        "overturn": "合同还有过户费、经手费等其他项目；或平台默认费率与此处读到的设置不同。",
    }


def _minimum_card(fees, edits, t=None):
    premium, count = fees.get("minimum_premium") or 0, fees.get("small_unit_count") or 0
    material = (t or {}).get("T-MIN-COMM-MATERIAL", 500.)
    if fees.get("status") not in {"measured", "verified"} or premium <= 0 or not count:
        return None
    return {
        "id": "minimum_commission", "state": "review" if premium >= material else "hold", "title": "小额订单的最低佣金",
        "finding": f"{count} 笔订单金额低于最低佣金拐点，因最低佣金多付约 {_yuan(premium)}。",
        "headline": f"金额小于 {_yuan(material)}，影响有限" if premium < material else "小单最低佣金值得处理",
        "edits": edits.get("order_floor", []) if premium >= material else [],
        "options": [{"label": "维持原样", "effect": "金额不大时可以不改"},
                    {"label": "设置最低新增买入金额", "effect": "可在下方『按审计发现修改代码』生成改法"}],
        "saving": f"最多约 {_yuan(premium)}",
        "sacrifice": "跳过小单会多留现金、增加与目标的偏离",
        "net": "取决于跳过的那部分仓位后来涨跌",
        "evidence": "recomputed",
        "basis": "每笔佣金按比例与最低佣金取大值",
        "overturn": "订单在原平台实际是合并成交，或券商不收最低佣金。",
    }


def _price_card(holding, tick, edits, t=None):
    """Low-priced stocks pay the widest tick spread; say whether they still earned their keep."""
    from .securities import asset_class
    floor = float((t or {}).get("T-PRICE-LOW", 5.))
    cheap_stock = lambda code, price: asset_class(code) == "stock" and price < floor
    fills = tick.get("fills")
    if fills is not None:
        total_value = sum(f["notional"] for f in fills)
        share = sum(f["notional"] for f in fills if cheap_stock(f["code"], f["price"])) / total_value if total_value else 0
    else:
        share = next((b["notional_share"] for b in tick.get("price_bands") or [] if b["id"] == "under_5"), 0)
    split = _split_lots(holding, lambda l: cheap_stock(l["code"], l["entry_price"]))
    if split is not None:
        cheap = {"matches": split["n"], "net_after_tick_scenario": split["after"], "cost": split["cost"]}
    else:
        bands = {b["id"]: b for b in holding.get("entry_price_bands") or []}
        cheap = (bands.get("under_5") or {}).get("closed") or {}
        if cheap:
            cheap = {**cheap, "cost": cheap["fees"] + cheap["tick_scenario_cost"]}
    if holding.get("status") != "verified" or not cheap.get("matches") or (share or 0) < .05:
        return None
    after, cost = cheap["net_after_tick_scenario"], cheap["cost"]
    losing = after < 0
    return {
        "id": "low_price", "state": "review" if losing else "hold", "title": "低价股是否太多",
        "finding": f"低于 {floor:g} 元的股票占成交额 {share:.0%}，已平仓 {cheap['matches']} 笔，付出费用和价差约 {_yuan(cost)}，扣完后合计 {_yuan(after)}。",
        "headline": (f"低价股扣完成本后亏 {_yuan(-after)}，可以加价格门槛" if losing
                     else f"低价股扣完成本后仍赚 {_yuan(after)}，加价格门槛会丢掉这部分，暂不改"),
        "edits": edits.get("price", []),
        "options": [{"label": "维持原样", "effect": "低价股本身赚钱时，价差只是成本的一部分"},
                    {"label": f"选股时排除 {floor:g} 元以下", "effect": "直接去掉价差最贵的一段"}],
        "saving": f"这部分的费用和价差约 {_yuan(cost)}",
        "sacrifice": f"这些交易扣完成本后合计 {_yuan(after)}，剔除后由其他股票顶上，结果要重跑才知道",
        "net": "需回原平台重跑，用效果验证比较",
        "evidence": "descriptive",
        "basis": "按买入价分组，先进先出匹配买卖；价差按最小价位情景",
        "overturn": "剔除后补进来的股票表现更差；或低价股实际成交价差小于半个价位。",
    }


def _cash_buffer_card(signals, edits, t=None):
    if not signals or signals.get("cash_short_orders", 0) < 3:
        return None
    ex = signals["cash_short_examples"][0]
    keep = (t or {}).get("T-CASH-BUFFER", .01)
    return {
        "id": "cash_buffer", "state": "review", "title": "全仓下单没留费用余量",
        "finding": f"有 {signals['cash_short_orders']} 笔买单因“可用资金不足”被拒，差额最多 {_yuan(signals['cash_short_gap'])}："
                   f"例如 {ex['date']} 有 {_yuan(ex['available'])}，下单需要 {_yuan(ex['required'])}。"
                   f"策略用全部现金下单，佣金和滑点一加就超了，结果这 {signals['cash_short_days']} 天信号选中的标的没买到"
                   "（账户可能仍持有其他标的）。",
        "headline": f"按原订单金额，下单留约 {keep:.0%} 余量可覆盖这 {signals['cash_short_orders']} 笔缺口；改代码后价格和股数会变，需重跑",
        "edits": edits.get("cash_buffer", []),
        "options": [{"label": "维持原样", "effect": "没买到目标的天数由当天价格碰巧决定，回测结果含这部分偶然"},
                    {"label": f"下单金额乘 {1 - keep:g}", "effect": "留出佣金和滑点，买单不再被拒；比例是经验值"},
                    {"label": "按预算算最大整手数", "effect": "下单前按 价格+滑点、佣金（含最低佣金）和整手单位，算出现金买得起的最大股数再下单；"
                                                          "不需要经验比例，现金留得最少"}],
        "saving": "不省钱：修的是本该买到却没买到目标的天数",
        "sacrifice": f"每次少买约 {keep:.0%} 的金额，现金略多",
        "net": "没买到目标这些天的涨跌原回测没有吃到，改后结果可能更好也可能更差，需回原平台重跑",
        "evidence": "scenario",
        "basis": "委托详情里平台给出的拒单原因和金额",
        "overturn": "这些天恰好是下跌日，空仓反而躲过了亏损；重跑后对比即可看出。",
    }


def _churn_card(signals, edits, opening=None, t=None):
    if not signals or not signals["switches"]:
        return None
    t = t or {}
    avoided, switches = signals["avoided_switches"], signals["switches"]
    worth = avoided >= max(t.get("T-CHURN-MIN", 3), t.get("T-CHURN-SHARE", .2) * switches)
    opening = opening or {}
    known = opening.get("status") == "declared_empty"
    inputs = [("少换次数", f"{avoided}"), ("配对口径", "FIFO"), ("已平仓笔数", f"{signals.get('closed_lots', 0)}"),
              ("期初状态", opening.get("text", "未确认")),
              ("平均每笔成本", f"¥{signals['cost_per_round_trip']:,.2f}（费用 + 价差情景）"),
              ("每笔成本是否随审计佣金变化", "否：取导出的实际费用")]
    retained = signals.get("retained_days", 0)
    missed = signals["days"] - signals["followed_days"] - retained
    groups = signals.get("missed_groups") or []
    reasons = "；".join(f"{g['days']} 天{'全部' if g['days'] > 1 else ''}是 {g['code']}，"
                       f"{(_clock(g['time']) + ' 下单，') if _clock(g['time']) else ''}平台原因：{g['reason'].replace('委托未成交：', '')}"
                       for g in groups[:3]) or "、".join(f"{k} {v} 天" for k, v in signals["missed_reasons"].items())
    if groups and groups[0]["days"] >= max(3, .5 * missed):
        reasons += ("。要确定原因，需要该标的这些日期的分钟数据（区分成交量为 0 和缺数据），"
                    "或原平台同区间的委托和成交导出")
    return {
        "id": "rank_churn", "state": "review" if worth else "hold", "title": "排名一变就换仓",
        "finding": (f"按策略自己的排名，{signals['days']} 个信号日里第一名换了 {signals.get('top_switches', switches)} 次"
                    + (f"，目标组合（约 {signals['target_size']} 只）换了 {switches} 次" if signals.get("target_size", 1) > 1 else "")
                    + f"，平均每 {signals['average_hold_days']:.1f} 个信号日换一次；")
                   + f"每次换仓的费用和价差约 {_yuan(signals['cost_per_round_trip'])}。"
                   f"有 {signals['whipsaw_lots']} 笔卖出后 5 天内又排回第一，这些交易付出约 {_yuan(signals['whipsaw_cost'])}。"
                   + (f"另有 {missed} 天没有持有选中的目标（{reasons}）。" if missed else "持仓每天都跟上了信号。")
                   + (f"{signals['extra_holding_days']} 天收盘持有目标以外的标的"
                      + (f"，其中 {signals['empty_target_but_held_days']} 天策略选择空仓却仍有持仓" if signals.get("empty_target_but_held_days") else "")
                      + "：卖出没有完成，或策略内部状态与实际账户不一致。" if signals.get("extra_holding_days") else "")
                   + (f"{retained} 天没有下单、继续持有紧随第一之后的标的：现象与保留规则一致，但没有策略每天的最终目标记录，"
                      "不能确认是规则还是漏单，未计入上面的天数。" if retained else ""),
        "headline": (f"固定成交下，排进前 {signals['buffer_rank']} 就不卖可少换 {avoided} 次，费用和价差情景约 "
                     f"{_yuan(signals['avoided_cost'])}；收益影响需重跑" if worth
                     else f"固定成交下保留带只能少换 {avoided} 次，暂不改"),
        "inputs": inputs,
        "formula": f"{avoided} × ¥{signals['cost_per_round_trip']:,.2f} = {_yuan(signals['avoided_cost'])}",
        "conditions": [] if known else ["首日持仓成本未确认，换仓节省仅为条件情景；补填期初现金并声明空仓可消除"],
        "edits": edits.get("buffer", []),
        "options": [{"label": "维持原样", "effect": "信号每天都更新，换仓跟得最紧"},
                    {"label": f"加保留带：持有的标的排进前 {signals['buffer_rank']} 就继续持有", "effect": "排名小幅波动不换仓"}],
        "saving": f"少换 {avoided} 次，费用和价差约 {_yuan(signals['avoided_cost'])}",
        "sacrifice": "持有的标的掉到第二时不换，会错过新第一名这几天的涨幅；是赚是亏要重跑才知道",
        "net": "需回原平台重跑，用效果验证比较",
        "evidence": "scenario",
        "basis": "用策略导出的每日排名回放换仓次数；每次换仓成本取已平仓交易的平均费用和价差",
        "overturn": "重跑后加保留带的净收益低于原规则：说明第一名的切换本身在赚钱。",
    }


def attach_reruns(cards, reruns):
    """Same-period reruns of a card (spec R6). They are E4: in-sample, never 'verified'."""
    labels = {"better": "历史金额更高", "mixed": "有得有失", "worse": "更差", "model_sensitivity": "费率敏感性"}
    for card in cards:
        runs = reruns.get(card["id"]) or []
        if not runs:
            continue
        card["reruns"] = [{"state": r["state"], "verdict": r["verdict"], "version": r.get("version"), "id": r.get("id"),
                           "parent": r.get("parent", ""), "label": labels.get(r["state"], r["state"])} for r in runs]
        card["evidence"] = "E4"
        latest = runs[-1]
        if latest["state"] in {"worse", "mixed"}:
            card["state"] = "hold"
            card["headline"] = f"同区间复跑{labels[latest['state']]}（样本内）：{card['headline']}"
        elif latest["state"] == "better":
            inference = latest.get("inference_state")
            qualification = "尚未确认稳定改善" if inference != "positive" else "近似区间高于零，仍需独立验证"
            card["headline"] = f"同区间复跑历史金额更高（{qualification}）：{card['headline']}"
        else:
            card["headline"] = f"同区间复跑仅描述{labels.get(latest['state'], latest['state'])}：{card['headline']}"
    return cards


def cards(result, bundle=None):
    """Action cards; with the bundle, each card also points at the code lines to change."""
    from . import audit_levers
    from .audit_rules import defaults
    modules = result.get("modules", {})
    fees_module = modules.get("fee_analysis") or {}
    thresholds = {**defaults(), **((result.get("rules") or {}).get("thresholds") or {})}
    edits = {}
    if bundle:
        levers, platform = audit_levers.locate(bundle), bundle.get("adapter", "")
        more = (fees_module.get("reported_fee") or 0) > (fees_module.get("modeled_fee") or 0)
        edits = {"holding": audit_levers.holding(levers, platform),
                 "price": audit_levers.price_floor(levers, platform),
                 "order_floor": audit_levers.order_floor(levers, platform, fees_module.get("breakpoint_notional") or 0),
                 "fees": audit_levers.fees(levers, bundle, more, (result.get("settings") or {}).get("commission_bp")),
                 "buffer": audit_levers.buffer(levers, (modules.get("signal_mapping") or {}).get("buffer_rank", 2)),
                 "cash_buffer": audit_levers.cash_buffer(levers, thresholds["T-CASH-BUFFER"])}
    out = [_price_jump_card(modules.get("prices")),
           _spec_card(modules.get("spec")),
           _listings_card(modules.get("listings")),
           _limits_card(modules.get("limits")),
           _decisions_card(modules.get("decisions")),
           _state_card(modules.get("state")),
           _off_target_card(modules.get("fulfilment")),
           _tick_card(modules.get("tick_analysis") or {}),
           _holding_card(modules.get("holding_analysis") or {}, edits, thresholds),
           _price_card(modules.get("holding_analysis") or {}, modules.get("tick_analysis") or {}, edits, thresholds),
           _fee_card(fees_module, edits, modules.get("fee_model"), thresholds),
           _minimum_card(fees_module, edits, thresholds),
           _churn_card(modules.get("signal_mapping"), edits, result.get("opening_state"), thresholds),
           _cash_buffer_card(modules.get("signal_mapping"), edits, thresholds),
           _timing_card(modules.get("fill_timing")),
           _partial_card(modules.get("partial_entry")),
           _exit_card(modules.get("activity"), thresholds),
           _capital_card(modules.get("capital")),
           _allocation_card(modules.get("allocation")),
           _idle_card(modules.get("activity")),
           _quality_card(modules.get("fill_quality")),
           _capacity_card(modules.get('capacity')),
           _sequence_card(modules.get("sequence"))]
    order = {"act": 0, "review": 1, "hold": 2}
    cards = sorted((c for c in out if c), key=lambda c: order[c["state"]])
    for card in cards:
        level = _LEVEL.get(card["evidence"], card["evidence"])
        card["conditions"] = card.get("conditions") or []
        if card["conditions"] and level == "E3":
            level = "E3c"
        card["evidence"] = level
        card["rules"] = cite(*_RULES.get(card["id"], ()))
    from .audit_impact import attach
    return attach(cards, result, bundle)
