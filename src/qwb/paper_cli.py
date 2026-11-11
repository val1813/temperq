"""CLI entry points for a minimal daily paper account."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from . import paper, strategy


def add_parser(sub):
    parser = sub.add_parser("paper", help="CSV-account advice and daily forward paper ledger")
    commands = parser.add_subparsers(dest="paper_cmd", required=True)
    verify = commands.add_parser('verify', help='read-only calendar, timing and frozen-identity checks; never records a session')
    verify.add_argument('name')
    verify.add_argument('--calendar', required=True, help='complete natural-date calendar, including next execution dates')
    verify.add_argument('--through', help='last completed date to check; defaults to latest completed calendar date')
    verify.add_argument('--minimum-forward-intervals', type=int, default=60)
    verify.add_argument('--source-kind', choices=('local_paper', 'synthetic_fixture'), required=True)
    verify.add_argument('--out', help='new JSON file outside the account; never overwrites')
    verify.add_argument('--json', action='store_true')
    preview = commands.add_parser("advice", help="reference-price advice from cash and holdings CSV")
    preview.add_argument("run")
    preview.add_argument("--cash", type=float, required=True)
    preview.add_argument("--holdings")
    preview.add_argument("--execution-day", required=True)
    init = commands.add_parser("init", help="freeze a model and initialize a new forward paper account")
    init.add_argument("name")
    init.add_argument("run")
    init.add_argument("--cash", type=float, required=True)
    init.add_argument("--holdings")
    init.add_argument("--data-dir")
    tick = commands.add_parser("tick", help="record newly completed sessions, once per day")
    tick.add_argument("name", nargs="?")
    tick.add_argument("--all", action="store_true")
    show = commands.add_parser("show", help="show frozen configuration, status and account history")
    show.add_argument("name")
    update = commands.add_parser("update-data", help="append CSV into a new immutable snapshot")
    update.add_argument("feed")
    update.add_argument("csv")
    update.add_argument("--calendar", required=True)
    update.add_argument('--corporate-actions', help='versioned declared event coverage JSON for a new or append-only feed')


def run(args):
    cwd = Path.cwd()
    try:
        if args.paper_cmd == 'verify':
            from . import paper_verification
            calendar = Path(args.calendar)
            if calendar.stat().st_size > 8*1024*1024:
                raise ValueError('日历不能超过8MB')
            result = paper_verification.verify(cwd, args.name, calendar.read_bytes(), through=args.through,
                minimum_forward_intervals=args.minimum_forward_intervals, source_kind=args.source_kind)
            body = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
            if args.out:
                output = Path(args.out).resolve()
                if output.is_relative_to((cwd/'runs/paper').resolve()):
                    raise ValueError('只读核验输出须位于全部纸面账户目录之外')
                with output.open('x', encoding='utf-8') as stream:
                    stream.write(body+'\n')
            print(body)
            return 0 if result['status'] == 'matched' and result['local_forward_coverage_gate'] == 'passed' else 1
        if args.paper_cmd in {"init", "advice"}:
            holdings = strategy.read_holdings(Path(args.holdings)) if args.holdings else []
            if args.paper_cmd == "advice":
                result = strategy.preview(Path(args.run), cwd, args.cash, holdings, args.execution_day)
            else:
                result = paper.initialize(cwd, args.name, Path(args.run), args.cash, holdings, data_dir=args.data_dir)
        elif args.paper_cmd == "update-data":
            from . import daily_data
            result = daily_data.update(Path(args.feed), Path(args.csv).read_bytes(), Path(args.calendar).read_bytes(),
                actions_raw=Path(args.corporate_actions).read_bytes() if args.corporate_actions else None)
        elif args.paper_cmd == "tick":
            if args.all == bool(args.name):
                raise ValueError("提供账户名称或 --all，两者选一")
            result = paper.tick_all(cwd) if args.all else paper.tick(cwd, args.name)
        else:
            result = paper.inspect(cwd, args.name)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if args.paper_cmd == "tick" and args.all:
            return int(any(r["state"] == "BLOCKED" for r in result.values()))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"paper: {exc}", file=sys.stderr)
        return 1
