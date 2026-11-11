"""Windows deep-workspace JSON persistence must remain atomic and readable."""
import hashlib
import os
from pathlib import Path

import pytest

from qwb import experiments as ex

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native path regression')


@pytest.fixture(params=[210, 290], ids=['long_filename', 'long_parent'])
def deep_target(tmp_path, request):
    parent = tmp_path
    while len(str(parent)) < request.param:
        parent /= 'scope-' + 'x'*24
    Path('\\\\?\\'+str(parent)).mkdir(parents=True)
    target = parent/('exposure_matched_' + 'y'*80 + '.json')
    assert 260 < len(str(target)) < 500
    try:
        yield target
    finally:
        Path('\\\\?\\'+str(target)).unlink(missing_ok=True)
        while parent != tmp_path:
            Path('\\\\?\\'+str(parent)).rmdir()
            parent = parent.parent


def test_long_json_path_can_be_created_replaced_read_and_hashed(deep_target):
    ex.write(deep_target, {'old': True})
    assert ex.read(deep_target) == {'old': True}
    ex.write(deep_target, {'new': True})
    raw = Path('\\\\?\\'+str(deep_target)).read_bytes()
    assert ex.read(deep_target) == {'new': True}
    assert ex.digest(deep_target) == 'sha256:'+hashlib.sha256(raw).hexdigest()
    assert not list(Path('\\\\?\\'+str(deep_target.parent)).glob('*.tmp'))


def test_long_json_path_transient_lock_preserves_original_until_replace(deep_target, monkeypatch):
    ex.write(deep_target, {'old': True})
    original = Path.replace
    attempts = []
    def replace(source, target):
        assert ex.read(deep_target) == {'old': True}
        attempts.append(source)
        if len(attempts) == 1:
            raise PermissionError('synthetic transient lock')
        return original(source, target)
    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(ex.time, 'sleep', lambda _: None)
    ex.write(deep_target, {'new': True})
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert ex.read(deep_target) == {'new': True}
    assert not list(Path('\\\\?\\'+str(deep_target.parent)).glob('*.tmp'))


def test_long_json_path_permanent_lock_cleans_temporary_and_preserves_original(deep_target, monkeypatch):
    ex.write(deep_target, {'old': True})
    def locked(*args):
        raise PermissionError('synthetic permanent lock')
    monkeypatch.setattr(Path, 'replace', locked)
    monkeypatch.setattr(ex.time, 'sleep', lambda _: None)
    with pytest.raises(PermissionError): ex.write(deep_target, {'new': True})
    assert ex.read(deep_target) == {'old': True}
    assert not list(Path('\\\\?\\'+str(deep_target.parent)).glob('*.tmp'))


def test_windows_extended_paths_preserve_unc_and_existing_prefixes():
    unc = Path('\\\\server\\share\\' + ('scope\\'*45) + 'result.json')
    extended = ex._io_path(unc)
    assert str(extended) == '\\\\?\\UNC\\' + str(unc)[2:]
    assert ex._io_path(extended) == extended
    relative = Path('result.json')
    assert ex._io_path(relative) == relative


def test_inventory_cannot_silently_omit_a_long_json_file(deep_target, tmp_path):
    ex.write(deep_target, {'evidence': True})
    expected = ex.digest(deep_target)
    assert ex.inventory(deep_target.parent) == {deep_target.name: expected}
    assert ex.inventory(tmp_path) == {deep_target.relative_to(tmp_path).as_posix(): expected}
