"""#398: the reply/stderr/exception sanitizer redacts secret-looking assignments, not code that mentions a secret name."""
import pytest

from peerhub.extensions.adapters import Sanitizer

REDACT = [
    "password = hunter2",
    "api_key: abc123def",
    '"token": "s3cr3tvalue"',
    "secret='abcdef'",
    "OPENAI_API_KEY=abc123def",
    "access_token: abc123def456",
    "(password: abc123def)",
]
KEEP = [
    "token = tokens[i]",
    "token = tokens2[i]",
    "token = tokens.item2[i]",
    "token = tokens.item2 [i]",
    "token = config1.get(x)",
    "def f(token: str) -> bool",
    'secret = os.environ["API_KEY"]',
    "password = swordfish",
]


@pytest.mark.parametrize("text", REDACT)
def test_assignment_with_a_secret_shaped_value_is_redacted(text):
    out = Sanitizer().clean(text)
    assert "[REDACTED]" in out, out
    assert out.split("[REDACTED]")[0] != text


@pytest.mark.parametrize("text", KEEP)
def test_code_that_only_names_a_secret_is_kept(text):
    assert Sanitizer().clean(text) == text


def test_redaction_keeps_the_name_and_the_separator():
    assert Sanitizer().clean("password = hunter2") == "password = [REDACTED]"
    assert Sanitizer().clean('"token": "s3cr3tvalue"') == '"token": [REDACTED]'
    assert Sanitizer().clean("a token = tokens[i] and password = hunter2") == "a token = tokens[i] and password = [REDACTED]"
