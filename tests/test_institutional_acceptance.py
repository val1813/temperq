import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("institutional_acceptance", Path(__file__).parents[1] / "scripts/run_institutional_acceptance.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def cache(items):
    return json.dumps({"fields": ["exchange", "cal_date", "is_open"], "items": items}).encode()


def test_calendar_catches_missing_session_even_when_available_prices_are_consistent():
    raw = cache([["SSE", "20250103", 1], ["SSE", "20250104", 0], ["SSE", "20250105", 0], ["SSE", "20250106", 1]])
    r = runner.check_calendar(raw, ["2025-01-03"], "2025-01-03", "2025-01-06")
    assert r["status"] == "different" and r["missing_sessions"] == ["2025-01-06"]
    r = runner.check_calendar(raw, ["2025-01-03", "2025-01-06"], "2025-01-03", "2025-01-06")
    assert r["status"] == "matched" and r["expected_sessions"] == 2


def test_calendar_requires_closed_days_too_and_rejects_duplicate_rows():
    with pytest.raises(ValueError, match="every requested"):
        runner.check_calendar(cache([["SSE", "20250103", 1]]), ["2025-01-03"], "2025-01-03", "2025-01-04")
    with pytest.raises(ValueError, match="Duplicate calendar"):
        runner.check_calendar(cache([["SSE", "20250103", 1]] * 2), ["2025-01-03"], "2025-01-03", "2025-01-03")


def test_calendar_rejects_duplicate_snapshots_and_reports_unexpected_closed_session():
    raw = cache([["SSE", "20250104", 0]])
    with pytest.raises(ValueError, match="Duplicate account"):
        runner.check_calendar(raw, ["2025-01-04"] * 2, "2025-01-04", "2025-01-04")
    r = runner.check_calendar(raw, ["2025-01-04"], "2025-01-04", "2025-01-04")
    assert r["status"] == "different" and r["unexpected_sessions"] == ["2025-01-04"]
