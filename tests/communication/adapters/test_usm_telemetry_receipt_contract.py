"""USM-13/19a: session telemetry receipt v2 keeps unsafe fields unknown."""
import pytest

from peerhub.extensions.session_receipts import binding_attributes


pytestmark = pytest.mark.unit


def test_usm_receipt_01_binding_snapshot_is_bounded_and_rejects_unsafe_values():
    class Runtime:
        def session_binding(self):
            return {"profile": "ag.standard", "model": "sk-live-abcdef0123456789", "effort": "low",
                    "writable": False, "cwd": "C:/private/workspace"}

    assert binding_attributes(Runtime()) == {
        "profile": "ag.standard", "model": None, "effort": "low", "writable": False,
    }


def test_usm_receipt_02_binding_snapshot_drops_paths_and_secret_assignments():
    class Runtime:
        def session_binding(self):
            return {"profile": "C:/private/profile", "model": "token=secret-value", "effort": "standard"}

    assert binding_attributes(Runtime()) == {
        "profile": None, "model": None, "effort": "standard", "writable": None,
    }


def test_usm_receipt_03_binding_snapshot_drops_credential_like_labels():
    class Runtime:
        def session_binding(self):
            return {"model": "token-secretvalue123", "effort": "low"}

    assert binding_attributes(Runtime())["model"] is None
