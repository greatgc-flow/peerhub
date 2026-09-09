import pytest
from peerhub.governance.directive_digest import compute_directive_digest

def test_directive_digest_is_stable_and_reproducible():
    rule = "Always use the designated artifacts directory."
    digest1 = compute_directive_digest(rule)
    digest2 = compute_directive_digest(rule)
    assert digest1 == digest2
    assert digest1.startswith("sha256:")
    # hardcode the exact sha256 to prove reproducibility
    assert digest1 == "sha256:1f30277ca2607283703d6fdd49270e3fcdacf3841cc045972411e222db59d771"
