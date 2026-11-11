"""Skip only unavailable optional native Torch runtimes, not arbitrary bugs.

pytest.importorskip handles a missing module. Windows native-loader failures
are OSError instead of ImportError; allowlist DLL loader/resource error codes
and require Torch DLL context. Any other OSError or exception must still fail.
"""
import pytest

_torch_unavailable = None


def import_torch():
    global _torch_unavailable
    if _torch_unavailable:
        pytest.skip(_torch_unavailable, allow_module_level=True)
    try:
        return pytest.importorskip("torch", reason="optional Torch training dependency is not installed", exc_type=ImportError)
    except OSError as error:
        message = str(error)
        native = getattr(error, "winerror", None) in {126, 127, 1114, 1455}
        if native and "dll" in message.lower() and "torch" in message.lower():
            _torch_unavailable = f"optional Torch native runtime unavailable (WinError {error.winerror}): {message}"
            pytest.skip(_torch_unavailable, allow_module_level=True)
        raise
