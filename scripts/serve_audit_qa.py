"""Serve an isolated, disposable audit-only workspace for browser verification."""
import argparse
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qwb.audit_app import create_app
import uvicorn

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8872)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='temperq-audit-qa-') as directory:
        uvicorn.run(create_app(Path(directory), port=args.port), host='127.0.0.1', port=args.port,
                    log_level='warning')
