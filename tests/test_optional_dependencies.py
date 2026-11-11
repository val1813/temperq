import pytest
import optional_dependencies as optional


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    monkeypatch.setattr(optional, "_torch_unavailable", None)


def test_missing_torch_uses_pytest_importorskip(monkeypatch):
    calls = []
    monkeypatch.setattr(optional.pytest, "importorskip", lambda name, **kwargs: calls.append((name, kwargs)) or "torch-module")
    assert optional.import_torch() == "torch-module"
    assert calls[0][0] == "torch" and calls[0][1]["exc_type"] is ImportError


def test_known_windows_loader_failure_skips_with_explicit_reason(monkeypatch):
    error = OSError('Error loading torch/lib/cufft64_11.dll: page file too small')
    error.winerror = 1455
    def broken(*args, **kwargs):
        raise error
    monkeypatch.setattr(optional.pytest, "importorskip", broken)
    with pytest.raises(pytest.skip.Exception, match="WinError 1455"):
        optional.import_torch()


@pytest.mark.parametrize("error", [OSError('disk corrupt'), RuntimeError('code bug'), ValueError('bad configuration')])
def test_other_failures_are_never_silenced(monkeypatch, error):
    def broken(*args, **kwargs):
        raise error
    monkeypatch.setattr(optional.pytest, "importorskip", broken)
    with pytest.raises(type(error)):
        optional.import_torch()
