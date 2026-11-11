<div align="center">

<img src="src/qwb/static/temperq-logo.png" width="88" alt="TemperQ">

# TemperQ

**Independent backtest audit for A-share strategies**

Hand TemperQ the files your backtesting platform exports and get back a reproducible audit report:<br>
do the books close, where did the profit come from, which assumptions do not hold, and what to change next.

[![CI](https://github.com/val1813/temperq/actions/workflows/ci.yml/badge.svg)](https://github.com/val1813/temperq/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey.svg)

[Quick start](#quick-start) · [Audit scope](#audit-scope) · [Report](#the-report) · [Principles](#principles) · [中文](README.md)

</div>

---

## Why audit a backtest

A good-looking equity curve usually mixes three things: the strategy's real selection skill, the backtest engine's fill assumptions, and whatever luck that stretch of history happened to give. A platform report shows only their sum.

TemperQ reads only the backtest's **outputs** (fills, holdings, account, logs and strategy code), rebuilds the ledger day by day, then checks each stage to separate those three as far as the evidence allows, stating plainly what can and cannot be confirmed.

- **Runs locally.** All files and reports stay on your machine; the service listens on `127.0.0.1` only.
- **Never executes your code.** Strategy code is parsed read-only, never imported or run.
- **Brings no data.** It audits only what you upload; it does not download or fill in market data or benchmarks.
- **Reproducible.** Each report carries every input, intermediate result and hash; the same files always produce the same report.

## Quick start

Windows 10/11 or Linux, CPU only, 8 GB RAM recommended.

**Windows**

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. [Download the source](https://github.com/val1813/temperq/archive/refs/heads/main.zip) and extract it.
3. Run `install.bat`.
4. Run `start.bat`; the audit page opens in your browser.

**Linux**

```bash
git clone https://github.com/val1813/temperq.git
cd temperq
sh install.sh
sh start.sh
```

The default address is `http://127.0.0.1:8765`. Without a backtest of your own, download the synthetic sample from the page to try it.

## Two modes

| Mode | Upload | Result |
| --- | --- | --- |
| **Audit a backtest** | Strategy code plus exports for 1 to 5 periods (ZIP accepted) | A full report per period, with a side-by-side view for multiple periods |
| **Compare original and modified** | Two backtests of the same period, before and after one change | A comparison under one cost method: is the change worth keeping |

Reports are produced directly after upload. When materials are missing, the report lists which checks could not run and what to add.

## Audit scope

| Layer | Checks | Materials |
| --- | --- | --- |
| **Books** | Cash, holdings, fills and fees close day by day; returns and drawdown reconciled with the platform | Fills, daily holdings, account |
| **Costs** | Commission, minimum commission, historical stamp duty; small-trade friction; tick-size spread scenario | Fills |
| **Attribution** | Net contribution by security, fee share, losses during the maximum drawdown, holding-period groups | Fills, holdings |
| **Execution** | Volume participation, same-minute cash dependency, unfilled remainders, fills at price limits, fill-time sensitivity | Orders, minute bars, limit prices |
| **Signal** | Rank signal versus forward returns (HAC errors), top pick versus candidate pool, signal timing and look-ahead | Daily rankings, candidate prices |
| **Sample** | Records before listing or after delisting, survivorship flags, whether the history is long enough to conclude | Listing dates |
| **Versions** | Original versus modified, whether two changes add up, consistency across periods | Two or four matched backtests |

The basic audit needs only standard platform exports. Rankings and minute data can be produced on the original platform with [`temperq_recorder.py`](src/qwb/static/temperq_recorder.py) and [`temperq_supplement.py`](src/qwb/static/temperq_supplement.py).

Supported platforms: BigQuant, JoinQuant (via the bundled [export script](src/qwb/static/joinquant_export.py)), RiceQuant / RQAlpha, and any other platform through the [generic CSV format](docs/STRATEGY_AUDIT_FORMAT.md). Coverage: long-only A-share stocks, ETFs/funds and convertible bonds.

## The report

The first screen answers three questions: **Can the returns be trusted? Where did the money go? What should be done first?** The body then covers performance and risk, attribution, findings, and scope. Technical detail is collapsed by default; a one-page summary and a full report can be printed to PDF.

## Principles

- **Reconcile before concluding.** Stages whose books do not close are marked for review, not judged.
- **Graded evidence.** Recomputed accounts, descriptive statistics, scenarios and same-period reruns are labelled separately.
- **Sourced rules.** Exchange rules, tax rates, statistical methods and heuristic thresholds are registered separately; see [Audit rules](docs/AUDIT_RULES.md).
- **Honest detection limits.** In the third of three pre-registered blind tests, TemperQ flagged 37 of 58 unseen defect cases and raised a review card on 13 of 60 clean cases. It surfaces suspicious points; it does not guarantee finding every problem.
- **No return forecasts.** Every conclusion describes the submitted history only and is not investment advice.

## Command line

```bash
qwb audit check  <export-dir>
qwb audit run    <export-dir> --out <out-dir>
qwb audit compare <original-out> <modified-out>
qwb audit interaction <original> <change-A> <change-B> <both>
```

`audit run` writes `report.html`, `checkup.html`, `next-steps.md`, `summary.json`, `evidence.json` and available CSV details. Relative links in the report keep the companion files accessible when sharing the whole output folder. The engine-generated next-run tasks have not been executed.

Without a confirmed opening account, returns start at the first exported close and exclude day one's profit/loss and fees. Exported NAV or capital in strategy code does not establish an opening balance. Use `--opening-cash` only after confirming an empty start and no deposits or withdrawals. Samples shorter than one calendar year are not annualised or compared against platform annual returns.

## Feedback

Please open an [issue](https://github.com/val1813/temperq/issues), attaching anonymised export headers for new platforms. Contact: val9281@qq.com

## License

AGPL-3.0, © 2026 val1813. Third-party components keep their own licenses; see [NOTICE](NOTICE). For research and education only; no investment advice, strategy management or live trading services.
