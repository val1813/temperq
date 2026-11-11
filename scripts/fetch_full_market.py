"""Fetch complete full-market daily snapshots using private local API settings."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from qwb.full_market import update

if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--feed", required=True)
    parser.add_argument("--end")
    parser.add_argument("--corporate-actions", help="Declared event JSON; append-only successor for an existing feed")
    args = parser.parse_args()
    try:
        actions_raw = Path(args.corporate_actions).read_bytes() if args.corporate_actions else None
        print(update(ROOT, args.feed, args.end, actions_raw=actions_raw), flush=True)
    except Exception as exc:
        print(f"Full-market fetch stopped ({type(exc).__name__}); no partial snapshot published", file=sys.stderr)
        raise SystemExit(1)
