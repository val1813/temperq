"""The shared upload script needs a live status region and an explicit compare mode."""
from html.parser import HTMLParser

import pytest
from qwb.audit_compare_ui import compare_form


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


@pytest.mark.parametrize('original', [None, {'id': 'existing-audit', 'name': '原版'}])
def test_compare_mode_exposes_accessible_upload_feedback(original):
    nodes = Elements(compare_form('test-token', original)).elements
    form = next(attrs for tag, attrs in nodes if tag == 'form')
    assert form['data-mode'] == 'compare' and form['action'] == '/strategy-audits/compare'
    statuses = [attrs for _, attrs in nodes if attrs.get('id') == 'upload-progress']
    assert len(statuses) == 1
    assert statuses[0]['role'] == 'status' and statuses[0]['aria-live'] == 'polite'
    inputs = {attrs['name']: attrs for tag, attrs in nodes if tag == 'input'}
    assert ('original_files' in inputs) == (original is None)
    assert ('original' in inputs) == (original is not None)
    assert 'modified_files' in inputs
