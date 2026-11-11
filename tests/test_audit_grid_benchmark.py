"""Known-answer grid benchmark: generator is deterministic, and each method reads the same materials."""
import pytest

from qwb import audit_grid_benchmark as b


@pytest.fixture(scope="module")
def seed0():
    return {n: b.scenario(0, n) for n in ("healthy", "implementation", "signal_decay", "mixed", "missing_evidence")}


def test_generator_is_deterministic():
    assert b.market(3, None)["close"][-1] == b.market(3, None)["close"][-1]
    # Turning decay on does not redraw scores or volumes.
    assert b.market(3, "half")["score"] == b.market(3, None)["score"]
    assert b.market(3, "half")["volume"] == b.market(3, None)["volume"]


def test_volume_cap_manifests_against_its_no_cap_twin(seed0):
    assert b.manifested(seed0["implementation"], seed0["healthy"], .01)


def test_grid_audit_separates_execution_from_signal_on_a_known_seed(seed0):
    assert b.method_c(seed0["healthy"]) == "healthy"
    assert b.method_c(seed0["implementation"]) == "implementation"
    assert b.method_c(seed0["signal_decay"]) == "signal_change"
    assert b.method_c(seed0["missing_evidence"]) == "insufficient"
    # The plain multi-window reading calls the execution-only path difference a signal change.
    assert b.method_b(seed0["implementation"], .01) == "signal_change"


def test_wilson_interval_is_bounded_and_zero_count_is_not_zero_risk():
    lo, hi = b.wilson(0, 30)
    assert lo == 0 and 0 < hi < .2
    assert b.wilson(0, 0) is None


def test_first_execution_difference_is_located_on_the_generator_date(seed0):
    truth = b.true_divergence(0, None, True)
    got = b.localization(seed0["implementation"], truth)
    assert got["with_event"] > 0 and got["exact"] == got["with_event"] and got["false_event"] == 0
    # No volume cap, no execution event: nothing to locate, and nothing is invented.
    calm = b.localization(seed0["signal_decay"], b.true_divergence(0, "half", False))
    assert calm["with_event"] == 0 and calm["false_event"] == 0


def test_shared_windows_do_not_create_independent_binomial_trials():
    r = b.clustered_rate([(0, 4, 4), (1, 0, 4)])
    assert r["seeds"] == 2 and r["rate"] == .5 and "wilson95" not in r
    assert r["cluster_percentile95"] == [0., 1.]
    repeated = b.clustered_rate([(0, 400, 400), (1, 0, 400)])
    assert repeated["cluster_percentile95"] == r["cluster_percentile95"]
    assert repeated["seed_any_numerator"] == r["seed_any_numerator"]


def test_perfect_localization_reports_seed_risk_and_no_window_wilson():
    r = b.clustered_rate([(seed, 3, 3) for seed in range(30)])
    assert r["rate"] == 1. and "wilson95" not in r
    assert r["cluster_percentile95"] == [1., 1.]
    assert b.wilson(30, 30)[0] < .9
