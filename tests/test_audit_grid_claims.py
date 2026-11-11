import hashlib
import json

import pytest

from qwb import audit_grid, audit_grid_claims as claims
from qwb.cli import main
from test_audit_grid import grid, matched_case


def setup_report(tmp_path):
    result = grid(tmp_path, matched_case(tmp_path))
    folder = tmp_path / "initial"
    audit_grid.write(result, folder)
    return folder / "grid.json"


def request(path, action="confirm", changes=None):
    req = claims.review_template(path)
    c = next(c for c in req["available_claims"] if c["claim_id"].endswith("account.books_reconcile"))
    req.update(reviewer="synthetic-test-reviewer", actions=[{"claim_id": c["claim_id"],
               "parent_revision": c["parent_revision"], "action": action}])
    if changes is not None:
        req["actions"][0]["changes"] = changes
    target = path.parent / f"request_{action}.json"
    target.write_text(json.dumps(req), encoding="utf-8")
    return target


def selected(path):
    g = json.loads(path.read_text(encoding="utf-8"))
    return next(c for c in g["strategies"][0]["claims"] if c["claim_id"].endswith("account.books_reconcile"))


def test_confirm_is_versioned_preserves_evidence_and_does_not_confirm_other_claims(tmp_path):
    parent = setup_report(tmp_path)
    original = parent.read_bytes()
    req = request(parent)
    out = tmp_path / "confirmed"
    assert main(["audit", "claim-review", str(parent), "--request", str(req), "--out", str(out), "--json"]) == 0
    c = selected(out / "grid.json")
    assert c["revision"] == 2 and c["parent_claim_revision"] == 1
    assert c["confirmed"] and c["claim_state"] == "supported_within_scope"
    assert c["per_cell"] == selected(parent)["per_cell"] and c["history"][0]["claim_state"] == "unconfirmed"
    assert c["confirmation"]["parent_grid_sha256"] == hashlib.sha256(original).hexdigest()
    assert c["per_cell"][0]["source_refs"]
    assert parent.read_bytes() == original
    other = json.loads((out / "claim_records.json").read_text(encoding="utf-8"))
    assert sum(x["confirmed"] for x in other) == 1
    with pytest.raises(ValueError, match="不覆盖"):
        claims.review(parent, req, out)


def test_revision_cannot_inherit_old_supported_verdict_even_after_confirmation(tmp_path):
    parent = setup_report(tmp_path)
    req = request(parent, "revise", {"text": "A changed claim needing its own evidence"})
    out = tmp_path / "revised"
    claims.review(parent, req, out)
    c = selected(out / "grid.json")
    assert c["claim_state"] == "unconfirmed" and c["diagnostic_state"] == "insufficient"
    assert c["previous_evaluation"]["diagnostic_state"] == "supported_within_scope"
    req2 = request(out / "grid.json")
    confirmed = tmp_path / "confirm_revision"
    claims.review(out / "grid.json", req2, confirmed)
    c = selected(confirmed / "grid.json")
    assert c["confirmed"] and c["claim_state"] == "insufficient" and len(c["history"]) == 2


@pytest.mark.parametrize("bad", ["hash", "revision", "reviewer", "evidence", "empty"])
def test_stale_or_forged_review_is_rejected_before_writing(tmp_path, bad):
    parent = setup_report(tmp_path)
    req = request(parent)
    obj = json.loads(req.read_text(encoding="utf-8"))
    if bad == "hash":
        obj["parent_grid_sha256"] = "stale"
    elif bad == "revision":
        obj["actions"][0]["parent_revision"] = 100
    elif bad == "reviewer":
        obj["reviewer"] = ""
    elif bad == "empty":
        obj["actions"] = []
    else:
        obj["actions"][0].update(action="revise", changes={"per_cell": []})
    req.write_text(json.dumps(obj), encoding="utf-8")
    out = tmp_path / "blocked"
    with pytest.raises(ValueError):
        claims.review(parent, req, out)
    assert not out.exists()
