import hashlib

def compute_directive_digest(rule_markdown: str) -> str:
    """Compute a deterministic digest for a directive's source text.
    
    UTF-8 bytes are digested as-is, which is the simplest and most
    defensible choice for text canonicalization without losing formatting
    or introducing arbitrary whitespace-stripping rules.
    """
    digest = hashlib.sha256(rule_markdown.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
