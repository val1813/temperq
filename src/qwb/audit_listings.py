"""Did the strategy see names that did not exist yet, and did its pool lose any name along the way?

With listing dates (`listings.csv`: code, listed_date, delisted_date; exported by the TemperQ supplement
script), two facts are checked:
  1. A name scored or traded before it was listed, or after it was delisted: the candidate pool was built
     with knowledge of the future.
  2. How many names in the pool were delisted during the period. None at all, in a large stock pool, is
     what a pool built from today's list looks like (survivorship). It is reported as a question, never as
     an error: a pool of ETFs or large caps can genuinely lose nothing.
"""
from __future__ import annotations


def analyze(bundle, accounting, thresholds=None):
    rows = {r["code"]: r for r in bundle.get("listings") or []}
    if not rows:
        return None
    pool = {}
    for s in bundle.get("scores") or []:
        pool.setdefault(s["code"], []).append(s["date"])
    traded = {}
    for f in bundle.get("fills") or []:
        traded.setdefault(f["code"], []).append(f["date"])
    early, late, missing = [], [], []
    for code in sorted(set(pool) | set(traded)):
        r = rows.get(code)
        if not r:
            missing.append(code)
            continue
        seen = pool.get(code, []) + traded.get(code, [])
        if r.get("listed_date") and min(seen) < r["listed_date"]:
            early.append({"code": code, "first_seen": min(seen), "listed_date": r["listed_date"]})
        if r.get("delisted_date") and max(seen) > r["delisted_date"]:
            late.append({"code": code, "last_seen": max(seen), "delisted_date": r["delisted_date"]})
    days = [s["date"] for s in bundle.get("snapshots") or []]
    period = (days[0], days[-1]) if days else (None, None)
    gone = [c for c in pool if (rows.get(c) or {}).get("delisted_date") and period[0] and
            period[0] <= rows[c]["delisted_date"] <= period[1]]
    stocks = [c for c in pool if c[:1] in "036" or c[:2] in ("68", "30")]
    out = {"id": "listings", "checked": len(set(pool) | set(traded)) - len(missing), "missing": missing[:10],
           "before_listing": early[:10], "after_delisting": late[:10], "pool": len(pool), "delisted_in_period": len(gone),
           "status": "flagged" if early or late else "normal"}
    parts = []
    if early or late:
        parts.append(f"{len(early)} 只标的在上市前、{len(late)} 只在退市后仍出现在打分或成交里：候选池用到了当时还不知道的信息")
    if pool:
        if gone:
            parts.append(f"候选池 {len(pool)} 只里有 {len(gone)} 只在回测期内退市，候选池包含了后来消失的标的")
        elif len(stocks) >= 30:
            parts.append(f"候选池 {len(pool)} 只（{len(stocks)} 只股票）在回测期内没有一只退市：如果候选池是按今天的名单建的，"
                         "回测有幸存者偏差；按当时名单建的则没有问题，需要确认")
    if missing:
        parts.append(f"{len(missing)} 只标的没有上市日期记录，未检查")
    out["verdict"] = "；".join(parts) or f"{out['checked']} 只标的都在上市期内被打分和交易"
    out["survivorship_question"] = bool(pool) and not gone and len(stocks) >= 30
    return out
